"""
src/moirai_predictor.py — Prédictions via Salesforce Moirai 2.0 (uni2ts)
"""

import gc
import logging
import numpy as np
from typing import Optional
import config

logger = logging.getLogger(__name__)

_predictor = None


def _load():
    """Charge Salesforce Moirai 2.0 R-small — une seule fois."""
    global _predictor
    if _predictor is not None:
        return _predictor
    try:
        from uni2ts.model.moirai2 import Moirai2Forecast, Moirai2Module
        logger.info("Chargement de Salesforce Moirai 2.0 (R-small)...")
        module = Moirai2Module.from_pretrained("Salesforce/moirai-2.0-R-small")
        model = Moirai2Forecast(
            module=module,
            prediction_length=config.FORECAST_HORIZON,
            context_length=config.CONTEXT_LENGTH,
            target_dim=1,
            feat_dynamic_real_dim=0,
            past_feat_dynamic_real_dim=0,
        )
        _predictor = model.create_predictor(batch_size=1)
        logger.info("Salesforce Moirai 2.0 chargé avec succès !")
        return _predictor
    except Exception as e:
        logger.error(f"Erreur chargement Moirai: {e}")
        return None


def predict_moirai(price_series: np.ndarray) -> Optional[np.ndarray]:
    """Génère une prédiction via Moirai 2.0. Retourne None si indisponible."""
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
        forecast = next(iter(predictor.predict(dataset)))
        try:
            median = forecast.quantile(0.5)
        except Exception:
            median = np.median(forecast.samples, axis=0)
        return np.asarray(median, dtype=np.float64)[: config.FORECAST_HORIZON]
    except Exception as e:
        logger.error(f"Erreur prédiction Moirai: {e}")
        return None


def unload_moirai():
    """Libère le modèle de la RAM (passes séquentielles multi-modèles)."""
    global _predictor
    _predictor = None
    gc.collect()
