"""
src/chronos_predictor.py — Prédictions via Amazon Chronos
"""

import logging
import numpy as np
import torch
from typing import Optional
from chronos import ChronosPipeline
import config

logger = logging.getLogger(__name__)

_pipeline = None


def _load_pipeline():
    """Charge Amazon Chronos (t5-mini) — une seule fois."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    try:
        logger.info("Chargement d'Amazon Chronos (t5-mini, 20M params)...")
        _pipeline = ChronosPipeline.from_pretrained(
            "amazon/chronos-t5-mini",
            device_map="cpu",
            torch_dtype=torch.float32,
        )
        logger.info("Amazon Chronos chargé avec succès !")
        return _pipeline
    except Exception as e:
        logger.error(f"Erreur chargement Chronos: {e}")
        return None


def predict_chronos(price_series: np.ndarray) -> Optional[np.ndarray]:
    """Génère une prédiction via Amazon Chronos."""
    pipeline = _load_pipeline()
    if pipeline is None:
        return None
    try:
        context = torch.tensor(price_series, dtype=torch.float32)
        forecast = pipeline.predict(
            context,
            config.FORECAST_HORIZON,
            num_samples=20,
        )
        # Prendre la médiane des échantillons pour obtenir la prédiction
        predictions = forecast[0].median(dim=0).values.numpy()
        return predictions.astype(np.float64)
    except Exception as e:
        logger.error(f"Erreur prédiction Chronos: {e}")
        return None


def get_chronos_direction(current_price: float, predictions: np.ndarray | None) -> str:
    """Retourne la direction prédite par Chronos (BUY, SELL ou HOLD)."""
    if predictions is None or len(predictions) == 0:
        return "HOLD"

    target_4h = float(predictions[min(3, len(predictions) - 1)])
    variation_4h = (target_4h - current_price) / current_price * 100

    if variation_4h > 0.05:
        return "BUY"
    elif variation_4h < -0.05:
        return "SELL"
    return "HOLD"
