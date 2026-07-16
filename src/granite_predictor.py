"""
src/granite_predictor.py — Prédictions via IBM Granite TTM (TinyTimeMixer r2)
"""

import gc
import logging
import numpy as np
from typing import Optional
import config

logger = logging.getLogger(__name__)

_model = None
_CONTEXT = 512   # TTM-r2 : contexte 512, horizon natif 96


def _load():
    """Charge IBM Granite TTM r2 — une seule fois."""
    global _model
    if _model is not None:
        return _model
    try:
        try:
            from tsfm_public.models.tinytimemixer import TinyTimeMixerForPrediction
        except ImportError:
            from tsfm_public import TinyTimeMixerForPrediction  # fallback anciennes versions
        logger.info("Chargement d'IBM Granite TTM (granite-timeseries-ttm-r2)...")
        _model = TinyTimeMixerForPrediction.from_pretrained(
            "ibm-granite/granite-timeseries-ttm-r2"
        )
        _model.eval()
        logger.info("IBM Granite TTM chargé avec succès !")
        return _model
    except Exception as e:
        logger.error(f"Erreur chargement Granite TTM: {e}")
        return None


def predict_granite(price_series: np.ndarray) -> Optional[np.ndarray]:
    """Génère une prédiction via Granite TTM. Retourne None si indisponible."""
    model = _load()
    if model is None:
        return None
    try:
        import torch

        x = price_series.astype(np.float32)
        if len(x) >= _CONTEXT:
            x = x[-_CONTEXT:]
        else:
            pad = np.full(_CONTEXT - len(x), x[0], dtype=np.float32)
            x = np.concatenate([pad, x])

        tensor = torch.tensor(x).reshape(1, _CONTEXT, 1)
        with torch.no_grad():
            outputs = model(past_values=tensor)
        preds = outputs.prediction_outputs[0, :, 0].numpy()
        return preds.astype(np.float64)[: config.FORECAST_HORIZON]
    except Exception as e:
        logger.error(f"Erreur prédiction Granite TTM: {e}")
        return None


def unload_granite():
    """Libère le modèle de la RAM (passes séquentielles multi-modèles)."""
    global _model
    _model = None
    gc.collect()
