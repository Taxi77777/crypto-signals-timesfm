"""
src/lagllama_predictor.py — Prédictions via Lag-Llama
Monkeypatch norm_freq_str inclus (compatibilité GluonTS / Pandas 2.2+)
"""

import gc
import logging
import numpy as np
from typing import Optional
import config

logger = logging.getLogger(__name__)

_predictor = None


def _apply_monkeypatch():
    """Corrige l'incompatibilité norm_freq_str entre GluonTS et Pandas 2.2+."""
    try:
        import gluonts.time_feature.lag as lag_module
        if getattr(lag_module.norm_freq_str, "_patched", False):
            return
        original_norm = lag_module.norm_freq_str

        def patched_norm(freq_str: str) -> str:
            res = original_norm(freq_str)
            mapping = {
                "min": "T", "h": "H", "s": "S", "d": "D",
                "w": "W", "QE": "Q", "YE": "A", "ME": "M",
            }
            return mapping.get(res, res)

        patched_norm._patched = True
        lag_module.norm_freq_str = patched_norm
        logger.info("Monkeypatch norm_freq_str appliqué (compat Pandas 2.2+)")
    except Exception as e:
        logger.warning(f"Monkeypatch norm_freq_str non appliqué: {e}")


def _load():
    """Charge Lag-Llama — une seule fois."""
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        _apply_monkeypatch()
        import torch
        from huggingface_hub import hf_hub_download
        from lag_llama.gluon.estimator import LagLlamaEstimator

        logger.info("Chargement de Lag-Llama...")
        ckpt_path = hf_hub_download(
            repo_id="time-series-foundation-models/Lag-Llama",
            filename="lag-llama.ckpt",
        )
        ckpt = torch.load(ckpt_path, map_location=torch.device("cpu"), weights_only=False)
        args = ckpt["hyper_parameters"]["model_kwargs"]

        estimator = LagLlamaEstimator(
            ckpt_path=ckpt_path,
            prediction_length=config.FORECAST_HORIZON,
            context_length=32,
            input_size=args["input_size"],
            n_layer=args["n_layer"],
            n_embd_per_head=args["n_embd_per_head"],
            n_head=args["n_head"],
            scaling=args.get("scaling", "robust"),
            time_feat=args.get("time_feat", True),
            batch_size=1,
            num_parallel_samples=20,
            trainer_kwargs={"accelerator": "cpu", "max_epochs": 0},
        )
        transformation = estimator.create_transformation()
        lightning_module = estimator.create_lightning_module()
        _predictor = estimator.create_predictor(transformation, lightning_module)
        logger.info("Lag-Llama chargé avec succès !")
        return _predictor
    except Exception as e:
        logger.error(f"Erreur chargement Lag-Llama: {e}")
        return None


def predict_lagllama(price_series: np.ndarray) -> Optional[np.ndarray]:
    """Génère une prédiction via Lag-Llama. Retourne None si indisponible."""
    predictor = _load()
    if predictor is None:
        return None
    try:
        import pandas as pd
        from gluonts.dataset.pandas import PandasDataset

        series = price_series.astype(np.float32)
        df = pd.DataFrame(
            {"target": series},
            index=pd.date_range(start="2020-01-01", periods=len(series), freq="15min"),
        )
        dataset = PandasDataset(df, target="target")
        forecast = next(iter(predictor.predict(dataset, num_samples=20)))
        preds = np.median(forecast.samples, axis=0)
        return np.asarray(preds, dtype=np.float64)[: config.FORECAST_HORIZON]
    except Exception as e:
        logger.error(f"Erreur prédiction Lag-Llama: {e}")
        return None


def unload_lagllama():
    """Libère le modèle de la RAM (passes séquentielles multi-modèles)."""
    global _predictor
    _predictor = None
    gc.collect()
