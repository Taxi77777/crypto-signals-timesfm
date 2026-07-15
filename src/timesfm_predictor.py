"""
src/timesfm_predictor.py — Prédictions via Google TimesFM 2.5
"""

import logging
import numpy as np
from typing import Optional
import config

logger = logging.getLogger(__name__)

_model = None


def _load_model():
    """Charge TimesFM 2.5 (200M) depuis HuggingFace — une seule fois."""
    global _model
    if _model is not None:
        return _model

    if not config.USE_TIMESFM:
        return None

    try:
        import timesfm
        logger.info("Chargement de Google TimesFM 2.5 (200M params)...")
        _model = timesfm.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch",
            torch_compile=False,
        )
        forecast_cfg = timesfm.ForecastConfig(max_horizon=config.FORECAST_HORIZON)
        _model.compile(forecast_cfg)
        logger.info("Google TimesFM 2.5 chargé avec succès !")
        return _model
    except ImportError:
        logger.warning("Package timesfm non installé — fallback actif")
        return None
    except Exception as e:
        logger.error(f"Erreur chargement TimesFM: {e}")
        return None


def predict_timesfm(price_series: np.ndarray) -> Optional[np.ndarray]:
    """Génère une prédiction via TimesFM 2.5."""
    model = _load_model()
    if model is None:
        logger.warning("TimesFM indisponible — aucune prédiction (pas de vote IA, sécurité)")
        return None
    try:
        inputs = [price_series.astype(np.float32)]
        point_forecast, _ = model.forecast(
            horizon=config.FORECAST_HORIZON,
            inputs=inputs,
        )
        predictions = point_forecast[0]
        return predictions.astype(np.float64)
    except Exception as e:
        logger.error(f"Erreur prédiction TimesFM: {e} — aucune prédiction (pas de vote IA)")
        return None


def get_forecast_direction(current_price: float, predictions: np.ndarray) -> dict:
    """Analyse la direction et calcule la confiance à partir des prédictions."""
    if predictions is None or len(predictions) == 0:
        return {"direction": "HOLD", "variation_4h": 0.0, "variation_24h": 0.0,
                "target_4h": current_price, "target_24h": current_price, "confidence": 50}

    target_4h  = float(predictions[min(3,  len(predictions) - 1)])
    target_24h = float(predictions[min(23, len(predictions) - 1)])

    variation_4h  = (target_4h  - current_price) / current_price * 100
    variation_24h = (target_24h - current_price) / current_price * 100

    direction = (
        "BUY"  if variation_4h >  0.05
        else "SELL" if variation_4h < -0.05
        else "HOLD"
    )

    mid = predictions[:min(12, len(predictions))]
    if direction == "BUY":
        confidence = int(np.sum(mid > current_price) / len(mid) * 100)
    elif direction == "SELL":
        confidence = int(np.sum(mid < current_price) / len(mid) * 100)
    else:
        confidence = 50

    return {
        "direction":     direction,
        "variation_4h":  round(variation_4h,  4),
        "variation_24h": round(variation_24h, 4),
        "target_4h":     round(target_4h,  4),
        "target_24h":    round(target_24h, 4),
        "confidence":    confidence,
    }
