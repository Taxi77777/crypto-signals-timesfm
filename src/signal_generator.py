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
    symbol:          str
    pair_name:       str
    signal:          str   # BUY / SELL / HOLD
    current_price:   str
    take_profit:     str
    stop_loss:       str
    confidence:      int
    rsi:             float
    rsi_status:      str
    macd_trend:      str
    ema_trend:       str
    bb_position:     str
    forecast_dir:    str
    forecast_4h:     str
    tp_pct:          str
    sl_pct:          str
    is_strong:       bool
    fisher:          float   # Fisher Transform value
    fisher_status:   str     # Extreme BUY / Extreme SELL / Neutre


def _format_crypto_price(price: float) -> str:
    """Formate le prix selon la magnitude (BTC vs DOGE)."""
    if price >= 1000:
        return f"${price:,.2f}"
    elif price >= 1:
        return f"${price:.4f}"
    else:
        return f"${price:.6f}"


def _ai_direction(current_price: float, predictions, threshold_pct: float = 0.05) -> str:
    """Direction prédite par un modèle IA. 'N/A' = modèle indisponible."""
    if predictions is None or len(predictions) == 0:
        return "N/A"
    idx = min(config.FORECAST_HORIZON - 1, len(predictions) - 1)
    target = float(predictions[idx])
    var = (target - current_price) / current_price * 100
    if var > threshold_pct:
        return "BUY"
    if var < -threshold_pct:
        return "SELL"
    return "HOLD"


def _majority_consensus(dirs: dict) -> tuple:
    """
    Consensus MAJORITE IA : au moins 3 IA disponibles doivent etre d'accord.
    Minimum 3 modeles disponibles requis.
    Retourne (direction, nb_disponibles, consensus_atteint: bool).
    """
    avail = {k: v for k, v in dirs.items() if v != "N/A"}
    n = len(avail)
    if n < 3:
        return ("HOLD", n, False)
    
    buys = list(avail.values()).count("BUY")
    sells = list(avail.values()).count("SELL")
    
    if buys >= 3:
        return ("BUY", n, True)
    if sells >= 3:
        return ("SELL", n, True)
        
    return ("HOLD", n, False)


def _fmt_dirs(dirs: dict) -> str:
    return "/".join(f"{k}:{v}" for k, v in dirs.items())


def generate_signal(
    symbol: str,
    df: pd.DataFrame,
    timesfm_predictions: np.ndarray | None,
    chronos_predictions: np.ndarray | None,
    moirai_predictions: np.ndarray | None = None,
    lagllama_predictions: np.ndarray | None = None,
    granite_predictions: np.ndarray | None = None,
    df_4h: pd.DataFrame | None = None,
    df_1d: pd.DataFrame | None = None,
) -> TradingSignal | None:
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
        fisher        = round(float(last["fisher"]), 2) if "fisher" in last else 0.0

        # Filtres de Tendance Forte & Volume (Assouplis)
        if adx < 15:
            logger.info(f"Filtre Range actif sur {symbol} (ADX: {adx:.1f} < 15) -> Signal annule")
            return None

        # Volume=0 = bougie en cours non cloturee (Yahoo Finance) -> on ignore ce filtre
        if volume > 0 and volume < volume_sma * 0.2:
            logger.info(f"Filtre Volume actif sur {symbol} (Volume: {volume:.0f} < 20% de SMA: {volume_sma:.0f}) -> Signal annule")
            return None

        # ── Analyse des indicateurs ─────────────────────────────────────────
        rsi_status = (
            "Surachete" if rsi > 70
            else "Survendu"  if rsi < 30
            else "Neutre"
        )

        # Fisher Transform — detection des zones extremes (echelle graduee jusqu'a +-4)
        fisher_status = "Neutre"
        if fisher >= 4.0:
            fisher_status = "🔥🔥 EXTREME MAX ACHAT — Retournement SELL imminent"
        elif fisher >= 3.0:
            fisher_status = "🔥 Tres extreme (zone SELL forte)"
        elif fisher >= 2.0:
            fisher_status = "⚠️ Zone extreme haute (SELL probable)"
        elif fisher >= 1.5:
            fisher_status = "📈 Zone haute (pression vendeuse)"
        elif fisher <= -4.0:
            fisher_status = "💎💎 EXTREME MAX VENTE — Retournement BUY imminent"
        elif fisher <= -3.0:
            fisher_status = "💎 Tres extreme (zone BUY forte)"
        elif fisher <= -2.0:
            fisher_status = "⚠️ Zone extreme basse (BUY probable)"
        elif fisher <= -1.5:
            fisher_status = "📉 Zone basse (pression acheteuse)"

        # Filtre de securite : On ne veut QUE des signaux en zone d'exces Fisher (pas de neutre)
        if fisher_status == "Neutre":
            logger.info(f"⏳ Filtre Fisher actif sur {symbol} (Fisher: {fisher:+.2f} est Neutre) -> Signal annule")
            return None

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
        confidence_tf = forecast["confidence"]
        target_4h = forecast["target_4h"]

        # ── Directions des 5 IA (consensus strict) ──────────────────────────
        timesfm_dir  = _ai_direction(current_price, timesfm_predictions)
        chronos_dir  = _ai_direction(current_price, chronos_predictions)
        moirai_dir   = _ai_direction(current_price, moirai_predictions)
        lagllama_dir = _ai_direction(current_price, lagllama_predictions)
        granite_dir  = _ai_direction(current_price, granite_predictions)

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

        # Fisher Transform (extremes = signaux forts de retournement, echelle jusqu'a +-4)
        if fisher <= -4.0:   buy_score  += 5  # ultra extreme vente = tres fort retournement haussier
        elif fisher <= -3.0: buy_score  += 4
        elif fisher <= -2.0: buy_score  += 3
        elif fisher <= -1.5: buy_score  += 1
        if fisher >= 4.0:    sell_score += 5  # ultra extreme achat = tres fort retournement baissier
        elif fisher >= 3.0:  sell_score += 4
        elif fisher >= 2.0:  sell_score += 3
        elif fisher >= 1.5:  sell_score += 1

        # TimesFM
        if timesfm_dir == "BUY":  buy_score  += 3
        if timesfm_dir == "SELL": sell_score += 3

        # Chronos
        if chronos_dir == "BUY":  buy_score  += 3
        if chronos_dir == "SELL": sell_score += 3

        # Moirai 2.0
        if moirai_dir == "BUY":  buy_score  += 3
        if moirai_dir == "SELL": sell_score += 3

        # Lag-Llama
        if lagllama_dir == "BUY":  buy_score  += 3
        if lagllama_dir == "SELL": sell_score += 3

        # Granite TTM
        if granite_dir == "BUY":  buy_score  += 3
        if granite_dir == "SELL": sell_score += 3

        # ── Décision finale ──────────────────────────────────────────────────
        max_score = 22
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

        # FILTRE DE CONSENSUS MAJORITAIRE IA
        dirs = {
            "TFM": timesfm_dir, "CHO": chronos_dir, "MOI": moirai_dir,
            "LLA": lagllama_dir, "GRA": granite_dir,
        }
        consensus, n_avail, has_consensus = _majority_consensus(dirs)
        if not has_consensus or consensus != signal:
            logger.info(
                f"Pas de consensus majoritaire (>=3) sur {symbol} ({_fmt_dirs(dirs)}, "
                f"{n_avail}/5 modeles actifs) -> Signal rejete"
            )
            return None
        logger.info(f"CONSENSUS {n_avail}/5 IA MAJORITAIRE sur {symbol} : {consensus} ({_fmt_dirs(dirs)})")

        # FILTRE MULTI-TIMEFRAME (4H & 1D TREND)
        if df_4h is not None and not df_4h.empty and df_1d is not None and not df_1d.empty:
            from src.indicators import compute_all_indicators
            df_4h_ind = compute_all_indicators(df_4h)
            df_1d_ind = compute_all_indicators(df_1d)
            if not df_4h_ind.empty and not df_1d_ind.empty:
                last_4h = df_4h_ind.iloc[-1]
                last_1d = df_1d_ind.iloc[-1]
                ema20_4h = float(last_4h["ema20"])
                ema50_4h = float(last_4h["ema50"])
                ema20_1d = float(last_1d["ema20"])
                ema50_1d = float(last_1d["ema50"])
                
                if signal == "BUY":
                    if ema20_4h < ema50_4h:
                        logger.info(f"⏳ Filtre Multi-Timeframe actif sur {symbol} (4h EMA20 < EMA50) -> Signal BUY annulé")
                        return None
                    if ema20_1d < ema50_1d:
                        logger.info(f"⏳ Filtre Multi-Timeframe actif sur {symbol} (1d EMA20 < EMA50) -> Signal BUY annulé")
                        return None
                elif signal == "SELL":
                    if ema20_4h > ema50_4h:
                        logger.info(f"⏳ Filtre Multi-Timeframe actif sur {symbol} (4h EMA20 > EMA50) -> Signal SELL annulé")
                        return None
                    if ema20_1d > ema50_1d:
                        logger.info(f"⏳ Filtre Multi-Timeframe actif sur {symbol} (1d EMA20 > EMA50) -> Signal SELL annulé")
                        return None
                logger.info(f"✅ Filtre Multi-Timeframe valide sur {symbol} (4h & 1d EMA alignees)")

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
            forecast_dir  = _fmt_dirs(dirs),
            forecast_4h   = _format_crypto_price(target_4h),
            tp_pct        = str(tp_pct),
            sl_pct        = "0.0",
            is_strong     = is_strong,
            fisher        = fisher,
            fisher_status = fisher_status,
        )

    except Exception as e:
        logger.error(f"Erreur génération signal {symbol}: {e}")
        return None
