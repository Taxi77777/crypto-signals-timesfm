"""
src/signal_generator.py — Génération des signaux de trading crypto
"""

import logging
import pandas as pd
import numpy as np
from dataclasses import dataclass
import config
from src.timesfm_predictor import get_forecast_direction

logger = logging.getLogger(__name__)


@dataclass
class TradingSignal:
    symbol:        str
    pair_name:     str
    signal:        str   # BUY / SELL / HOLD
    current_price: str
    take_profit:   str
    stop_loss:     str
    confidence:    int
    rsi:           float
    rsi_status:    str
    macd_trend:    str
    ema_trend:     str
    bb_position:   str
    forecast_dir:  str
    forecast_4h:   str
    tp_pct:        str
    sl_pct:        str
    is_strong:     bool


def _format_crypto_price(price: float) -> str:
    """Formate le prix selon la magnitude (BTC vs DOGE)."""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def generate_signal(symbol: str, df: pd.DataFrame, timesfm_predictions: np.ndarray, chronos_predictions: np.ndarray | None) -> TradingSignal | None:
    """Génère un signal de trading pour une crypto en combinant TimesFM + Chronos."""
    try:
        last = df.iloc[-1]
        current_price = float(last["close"])
        rsi           = round(float(last["rsi"]), 1)
        macd_hist     = float(last["macd_hist"])
        macd_val      = float(last["macd"])
        ema20         = float(last["ema20"])
        ema50         = float(last["ema50"])
        bb_upper      = float(last["bb_upper"])
        bb_lower      = float(last["bb_lower"])
        atr           = float(last["atr"])
        adx           = float(last["adx"])
        volume        = float(last["volume"])
        volume_sma    = float(last["volume_sma"])

        # ── Filtres de Tendance Forte & Volume (Anti-Range / Volume mort) ──
        if adx < 20:
            logger.info(f"⏳ Filtre Range actif sur {symbol} (ADX: {adx:.1f} < 20) → Signal annulé")
            return None

        if volume < volume_sma * 0.6:
            logger.info(f"⏳ Filtre Volume actif sur {symbol} (Volume: {volume:.0f} < 60% de SMA: {volume_sma:.0f}) → Signal annulé")
            return None

        # ── Analyse des indicateurs ─────────────────────────────────────────
        rsi_status = (
            "Suracheté 🔴" if rsi > 70
            else "Survendu 🟢"  if rsi < 30
            else "Neutre ⚪"
        )

        macd_bullish = macd_hist > 0 and macd_val > 0
        macd_bearish = macd_hist < 0 and macd_val < 0
        macd_trend   = (
            "Haussier 🟢" if macd_bullish
            else "Baissier 🔴" if macd_bearish
            else "Neutre ⚪"
        )

        ema_bullish = ema20 > ema50
        ema_bearish = ema20 < ema50
        ema_trend   = (
            "EMA20 > EMA50 🟢" if ema_bullish
            else "EMA20 < EMA50 🔴" if ema_bearish
            else "EMA alignées ⚪"
        )

        bb_position = (
            "Prix > BB Haute 🔴" if current_price > bb_upper
            else "Prix < BB Basse 🟢" if current_price < bb_lower
            else "Dans les bandes ⚪"
        )

        # ── Prédiction TimesFM ───────────────────────────────────────────────
        forecast = get_forecast_direction(current_price, timesfm_predictions)
        timesfm_dir = forecast["direction"]
        confidence_tf = forecast["confidence"]
        target_4h = forecast["target_4h"]

        # ── Prédiction Amazon Chronos ────────────────────────────────────────
        from src.chronos_predictor import get_chronos_direction
        chronos_dir = get_chronos_direction(current_price, chronos_predictions)

        # ── Score composite ──────────────────────────────────────────────────
        buy_score  = 0
        sell_score = 0

        # RSI
        if rsi < 35:   buy_score  += 2
        elif rsi < 45: buy_score  += 1
        if rsi > 65:   sell_score += 2
        elif rsi > 55: sell_score += 1

        # MACD
        if macd_bullish: buy_score  += 2
        if macd_bearish: sell_score += 2

        # EMA
        if ema_bullish: buy_score  += 1
        if ema_bearish: sell_score += 1

        # Bollinger
        if current_price < bb_lower: buy_score  += 2
        if current_price > bb_upper: sell_score += 2

        # TimesFM
        if timesfm_dir == "BUY":  buy_score  += 3
        if timesfm_dir == "SELL": sell_score += 3

        # Chronos
        if chronos_dir == "BUY":  buy_score  += 3
        if chronos_dir == "SELL": sell_score += 3

        # ── Décision finale ──────────────────────────────────────────────────
        max_score = 13
        if buy_score > sell_score and buy_score >= 6:
            signal     = "BUY"
            confidence = min(95, int((buy_score / max_score) * 100) + confidence_tf // 4)
            tp_mult    = 1 + (atr * 3.5 / current_price)
            sl_mult    = 1 - (atr * 3.0 / current_price)
        elif sell_score > buy_score and sell_score >= 6:
            signal     = "SELL"
            confidence = min(95, int((sell_score / max_score) * 100) + confidence_tf // 4)
            tp_mult    = 1 - (atr * 3.5 / current_price)
            sl_mult    = 1 + (atr * 3.0 / current_price)
        else:
            return None  # Pas de signal clair

        # ── Filtre de Double Consensus Strict ──
        if signal in ["BUY", "SELL"]:
            if timesfm_dir != chronos_dir:
                logger.info(
                    f"⚖️ Désaccord IA sur {symbol} (TimesFM: {timesfm_dir} vs Chronos: {chronos_dir}) "
                    f"→ Signal filtré à HOLD pour sécurité"
                )
                return None  # Pas de trade si désaccord

        tp_price = current_price * tp_mult
        sl_price = current_price * sl_mult
        tp_pct   = round(abs(tp_price - current_price) / current_price * 100, 2)
        sl_pct   = round(abs(sl_price - current_price) / current_price * 100, 2)
        is_strong = confidence >= config.MIN_CONFIDENCE

        pair_name = config.PAIR_NAMES.get(symbol, symbol)

        return TradingSignal(
            symbol        = symbol,
            pair_name     = pair_name,
            signal        = signal,
            current_price = _format_crypto_price(current_price),
            take_profit   = _format_crypto_price(tp_price),
            stop_loss     = "Aucun",
            confidence    = confidence,
            rsi           = rsi,
            rsi_status    = rsi_status,
            macd_trend    = macd_trend,
            ema_trend     = ema_trend,
            bb_position   = bb_position,
            forecast_dir  = f"TFM:{timesfm_dir}/CHO:{chronos_dir}",
            forecast_4h   = _format_crypto_price(target_4h),
            tp_pct        = str(tp_pct),
            sl_pct        = "0.0",
            is_strong     = is_strong,
        )

    except Exception as e:
        logger.error(f"Erreur génération signal {symbol}: {e}")
        return None
