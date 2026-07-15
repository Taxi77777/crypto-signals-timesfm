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


def generate_signal(symbol: str, df: pd.DataFrame, predictions: np.ndarray) -> TradingSignal | None:
    """Génère un signal de trading pour une crypto."""
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
        forecast = get_forecast_direction(current_price, predictions)
        direction = forecast["direction"]
        confidence_tf = forecast["confidence"]
        target_4h = forecast["target_4h"]

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
        if direction == "BUY":  buy_score  += 3
        if direction == "SELL": sell_score += 3

        # ── Décision finale ──────────────────────────────────────────────────
        max_score = 10
        if buy_score > sell_score and buy_score >= 5:
            signal     = "BUY"
            confidence = min(95, int((buy_score / max_score) * 100) + confidence_tf // 4)
            tp_mult    = 1 + (atr * 2 / current_price)
            sl_mult    = 1 - (atr * 1.5 / current_price)
        elif sell_score > buy_score and sell_score >= 5:
            signal     = "SELL"
            confidence = min(95, int((sell_score / max_score) * 100) + confidence_tf // 4)
            tp_mult    = 1 - (atr * 2 / current_price)
            sl_mult    = 1 + (atr * 1.5 / current_price)
        else:
            return None  # Pas de signal clair

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
            stop_loss     = _format_crypto_price(sl_price),
            confidence    = confidence,
            rsi           = rsi,
            rsi_status    = rsi_status,
            macd_trend    = macd_trend,
            ema_trend     = ema_trend,
            bb_position   = bb_position,
            forecast_dir  = "📈 Hausse" if direction == "BUY" else "📉 Baisse" if direction == "SELL" else "↔️ Neutre",
            forecast_4h   = _format_crypto_price(target_4h),
            tp_pct        = str(tp_pct),
            sl_pct        = str(sl_pct),
            is_strong     = is_strong,
        )

    except Exception as e:
        logger.error(f"Erreur génération signal {symbol}: {e}")
        return None
