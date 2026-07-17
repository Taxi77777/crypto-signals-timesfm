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
        # ── SUPERTREND : direction de tendance + flip (le moteur crypto) ──
        st_dir     = int(last["supertrend_dir"]) if "supertrend_dir" in last else 0
        st_flip_up = bool(last["st_flip_up"])    if "st_flip_up" in last else False
        st_flip_dn = bool(last["st_flip_down"])  if "st_flip_down" in last else False
        st_value   = round(float(last["supertrend"]), 6) if "supertrend" in last else current_price
        # Croisement Fisher / ligne signal (trigger = Fisher decale de 1, style TradingView)
        f1 = float(df.iloc[-1]["fisher"]) if "fisher" in last else 0.0
        f2 = float(df.iloc[-2]["fisher"]) if len(df) > 1 and "fisher" in last else f1
        f3 = float(df.iloc[-3]["fisher"]) if len(df) > 2 and "fisher" in last else f2
        fisher_cross_up   = f1 > f2 and f2 <= f3   # retournement haussier (creux)
        fisher_cross_down = f1 < f2 and f2 >= f3   # retournement baissier (sommet)

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

        # Fisher Transform — CROISEMENT en zone extreme (style TradingView)
        # BUY  = le Fisher croise sa ligne signal a la hausse depuis un creux extreme (<= -1.5)
        # SELL = le Fisher croise sa ligne signal a la baisse depuis un sommet extreme (>= +1.5)
        fisher_status = "Neutre"
        depth = f2  # profondeur du creux/sommet au moment du croisement
        if fisher_cross_up and depth <= -1.5:
            if depth <= -4.0:   fisher_status = "💎💎 CROISEMENT EXTREME MAX — Retournement BUY tres fort"
            elif depth <= -3.0: fisher_status = "💎 Croisement tres extreme (BUY fort)"
            elif depth <= -2.0: fisher_status = "⚠️ Croisement extreme bas (BUY)"
            else:               fisher_status = "📉 Croisement zone basse (BUY leger)"
        elif fisher_cross_down and depth >= 1.5:
            if depth >= 4.0:    fisher_status = "🔥🔥 CROISEMENT EXTREME MAX — Retournement SELL tres fort"
            elif depth >= 3.0:  fisher_status = "🔥 Croisement tres extreme (SELL fort)"
            elif depth >= 2.0:  fisher_status = "⚠️ Croisement extreme haut (SELL)"
            else:               fisher_status = "📈 Croisement zone haute (SELL leger)"

        # Fisher en ZONE extreme (sans exiger le croisement pile sur la derniere bougie)
        # -> reouvre les signaux tout en gardant le Fisher comme fort contributeur
        if fisher_status == "Neutre":
            if fisher <= -1.5:
                fisher_status = "📉 Zone basse (BUY)" if fisher > -3 else "💎 Zone tres basse (BUY fort)"
                depth = fisher
            elif fisher >= 1.5:
                fisher_status = "📈 Zone haute (SELL)" if fisher < 3 else "🔥 Zone tres haute (SELL fort)"
                depth = fisher
        # Le Fisher n'est plus un filtre bloquant : il enrichit le score (voir plus bas).
        # On ne rejette QUE si toutes les autres conditions echouent aussi.

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

        # ── SUPERTREND : moteur de tendance (adapte a la crypto qui trend fort) ──
        # Flip de tendance = signal fort (poids 5). Tendance etablie = poids 2.
        if st_flip_up:
            buy_score  += 5          # retournement haussier confirme
        elif st_dir == 1:
            buy_score  += 2          # tendance haussiere en cours
        if st_flip_dn:
            sell_score += 5          # retournement baissier confirme
        elif st_dir == -1:
            sell_score += 2          # tendance baissiere en cours

        # Fisher conserve en APPOINT (poids leger) : confirme les exces
        if fisher <= -2.0:   buy_score  += 1
        if fisher >= 2.0:    sell_score += 1

        # ── PONDERATION DYNAMIQUE : chaque IA vote selon son taux de reussite reel ──
        from src.track_record import load_track, get_weight
        _track = load_track()
        _dirs_tmp = {
            "TFM": timesfm_dir, "CHO": chronos_dir, "MOI": moirai_dir,
            "LLA": lagllama_dir, "GRA": granite_dir,
        }
        ai_weights = {k: get_weight(_track, k, symbol) for k in _dirs_tmp}
        for k, d in _dirs_tmp.items():
            w = ai_weights[k]
            if w == 0:
                continue  # (ne se produit plus : plancher a 2)
            if d == "BUY":  buy_score  += w
            if d == "SELL": sell_score += w

        # ── FILTRE SUPERTREND : jamais a contre-tendance ──
        # (un BUY exige que le Supertrend ne soit pas baissier, et inversement)

        # ── Décision finale ──────────────────────────────────────────────────
        max_score = 27  # 5 IA x3 + RSI2 + MACD2 + EMA1 + BB2 + ST5 + Fisher1
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
        dirs = {k: (v if ai_weights.get(k, 3) > 0 else "N/A") for k, v in _dirs_tmp.items()}
        consensus, n_avail, has_consensus = _majority_consensus(dirs)
        if not has_consensus or consensus != signal:
            logger.info(
                f"Pas de consensus majoritaire (>=3) sur {symbol} ({_fmt_dirs(dirs)}, "
                f"{n_avail}/5 modeles actifs) -> Signal rejete"
            )
            return None
        logger.info(f"CONSENSUS {n_avail}/5 IA MAJORITAIRE sur {symbol} : {consensus} ({_fmt_dirs(dirs)}) | poids: {ai_weights}")

        # FILTRE MULTI-TIMEFRAME (SUPERTREND 4H) — cohérent avec le moteur 15m
        if df_4h is not None and not df_4h.empty:
            from src.indicators import compute_all_indicators
            df_4h_ind = compute_all_indicators(df_4h)
            if not df_4h_ind.empty:
                st_dir_4h = int(df_4h_ind.iloc[-1]["supertrend_dir"])  # 1=haussier, -1=baissier

                if signal == "BUY" and st_dir_4h == -1:
                    logger.info(f"⏳ Filtre Supertrend 4H actif sur {symbol} (tendance 4h baissiere) -> Signal BUY annulé")
                    return None
                if signal == "SELL" and st_dir_4h == 1:
                    logger.info(f"⏳ Filtre Supertrend 4H actif sur {symbol} (tendance 4h haussiere) -> Signal SELL annulé")
                    return None
                logger.info(f"✅ Filtre Supertrend 4H valide sur {symbol} (tendance 4h alignee: {'haussiere' if st_dir_4h==1 else 'baissiere'})")

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
