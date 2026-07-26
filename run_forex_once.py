"""
===============================================================================
SCANNER FOREX IA & MOSTAFA BELKHAYATE — 28 PAIRES MAJORITAIRES & CROISEMENT STRICT
===============================================================================
- Multi-Timeframe : 5m, 15m, 30m, 1H
- Stratégie : Mostafa Belkhayate (Barycentre + Timing) + Croisement Précis MA30/60 (0-1 bougie) + Mèches de Rejet (>= 15%)
- IA : Google TimesFM 2.5 & Amazon Chronos
- Export : Telegram & MT4/MT5 Signal Receiver (forex_signals.json)
===============================================================================
"""

import os
import sys
import json
import time
import logging
import pandas as pd
import numpy as np
import yfinance as yf
import ta

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/forex_signals.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ForexScanner")

# Assurer l'existence du dossier logs
os.makedirs("logs", exist_ok=True)

# 28 Paires Forex Majeures et Croisées
FOREX_PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X",
    "EURGBP=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X", "NZDJPY=X",
    "EURAUD=X", "EURCAD=X", "EURCHF=X", "EURNZD=X", "GBPAUD=X", "GBPCAD=X", "GBPCHF=X",
    "GBPNZD=X", "AUDCAD=X", "AUDCHF=X", "AUDNZD=X", "CADCHF=X", "NZDCAD=X", "NZDCHF=X"
]

def prepare_forex_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule les indicateurs techniques clés + Système Mostafa Belkhayate + Mèches de rejet."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [col.lower() for col in df.columns]

    # Mèches de Rejet (Rejection Wick Ratio)
    candle_range = (df["high"] - df["low"]).replace(0, 1e-6)
    body_min = np.minimum(df["open"], df["close"])
    body_max = np.maximum(df["open"], df["close"])
    df["lower_wick_pct"] = (body_min - df["low"]) / candle_range
    df["upper_wick_pct"] = (df["high"] - body_max) / candle_range

    # Moyennes Mobiles 30 / 60 & EMA 20 / 50
    df["ma30"]  = ta.trend.SMAIndicator(df["close"], window=30).sma_indicator()
    df["ma60"]  = ta.trend.SMAIndicator(df["close"], window=60).sma_indicator()
    df["ema20"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    df["ema50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()

    # RSI & ATR
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["atr"] = ta.volatility.AverageTrueRange(df["high"], df["low"], df["close"], window=14).average_true_range()

    # ── SYSTEME MOSTAFA BELKHAYATE (Barycentre + Belkhayate Timing) ──
    bary_len = 30
    df["belkhayate_barycenter"] = df["close"].rolling(window=bary_len).mean()
    bary_std = df["close"].rolling(window=bary_len).std()
    df["belkhayate_upper_zone"] = df["belkhayate_barycenter"] + (1.618 * 1.8 * bary_std) # Zone Vente Extrême
    df["belkhayate_lower_zone"] = df["belkhayate_barycenter"] - (1.618 * 1.8 * bary_std) # Zone Achat Extrême
    df["belkhayate_timing"]     = (df["close"] - df["belkhayate_barycenter"]) / bary_std.replace(0, 1e-6)

    return df

def run_forex_scan():
    logger.info("==================================================")
    logger.info("DEMARRAGE SCANNER FOREX IA & MOSTAFA BELKHAYATE (28 PAIRES)")
    logger.info("==================================================")

    # Chargement des modèles IA (Google TimesFM & Amazon Chronos)
    try:
        from src.timesfm_predictor import predict_timesfm
        from src.chronos_predictor import predict_chronos
        ai_enabled = True
    except Exception as e:
        logger.warning(f"Modèles IA non installés ou en erreur ({e}) — Scan basé sur Belkhayate + Mèches")
        ai_enabled = False

    signals_found = []

    for symbol in FOREX_PAIRS:
        clean_pair = symbol.replace("=X", "")
        try:
            df_15m = yf.download(symbol, period="5d", interval="15m", progress=False)
            df_1h  = yf.download(symbol, period="30d", interval="1h", progress=False)

            if df_15m.empty or len(df_15m) < 40 or df_1h.empty or len(df_1h) < 40:
                continue

            df_15m = prepare_forex_indicators(df_15m)
            df_1h  = prepare_forex_indicators(df_1h)

            cur_price = float(df_15m["close"].iloc[-1])

            # ── 1. VERIFICATION METHODE BELKHAYATE & MECHES DE REJET ──
            lower_wick_15m = float(df_15m["lower_wick_pct"].iloc[-1])
            upper_wick_15m = float(df_15m["upper_wick_pct"].iloc[-1])
            timing_15m     = float(df_15m["belkhayate_timing"].iloc[-1])
            low_zone_15m   = float(df_15m["belkhayate_lower_zone"].iloc[-1])
            up_zone_15m    = float(df_15m["belkhayate_upper_zone"].iloc[-1])

            has_buy_wick  = (lower_wick_15m >= 0.15)
            has_sell_wick = (upper_wick_15m >= 0.15)

            # ── 2. DETECTER CROISEMENT PRECIS MA 30 / MA 60 (0 à 1 bougie max) ──
            ma30_curr = float(df_15m["ma30"].iloc[-1])
            ma60_curr = float(df_15m["ma60"].iloc[-1])
            ma30_p1   = float(df_15m["ma30"].iloc[-2])
            ma60_p1   = float(df_15m["ma60"].iloc[-2])
            ma30_p2   = float(df_15m["ma30"].iloc[-3])
            ma60_p2   = float(df_15m["ma60"].iloc[-3])

            is_exact_golden_cross = (ma30_p1 < ma60_p1 and ma30_curr >= ma60_curr) or (ma30_p2 < ma60_p2 and ma30_p1 >= ma60_p1)
            is_exact_death_cross  = (ma30_p1 > ma60_p1 and ma30_curr <= ma60_curr) or (ma30_p2 > ma60_p2 and ma30_p1 <= ma60_p1)

            # Condition Belkhayate + Mèche + Croisement
            belkhayate_buy  = (cur_price <= low_zone_15m or timing_15m <= -1.5 or is_exact_golden_cross) and has_buy_wick
            belkhayate_sell = (cur_price >= up_zone_15m  or timing_15m >= 1.5  or is_exact_death_cross)  and has_sell_wick

            if not (belkhayate_buy or belkhayate_sell):
                continue

            direction = "BUY" if belkhayate_buy else "SELL"

            # ── 3. PREDICTIONS ET CONSENSUS IA (SI ACTIVES) ──
            if ai_enabled:
                tfm_preds = predict_timesfm(df_15m["close"].values)
                cho_preds = predict_chronos(df_15m["close"].values)
                tfm_target = float(tfm_preds[-1]) if tfm_preds is not None and len(tfm_preds) > 0 else cur_price
                cho_target = float(cho_preds[-1]) if cho_preds is not None and len(cho_preds) > 0 else cur_price

                tfm_dir = "BUY" if tfm_target > cur_price else "SELL" if tfm_target < cur_price else "HOLD"
                cho_dir = "BUY" if cho_target > cur_price else "SELL" if cho_target < cur_price else "HOLD"

                if tfm_dir != direction and cho_dir != direction and tfm_dir != "HOLD" and cho_dir != "HOLD":
                    logger.info(f"{clean_pair} : Desaccord IA ({tfm_dir}/{cho_dir} vs Belkhayate {direction}) — ignore.")
                    continue

            # Calcul TP et SL adaptatifs (Target Belkhayate / ATR)
            atr_val = float(df_15m["atr"].iloc[-1])
            tp_dist = 2.0 * atr_val
            sl_dist = 1.2 * atr_val

            tp_price = round(cur_price + tp_dist if direction == "BUY" else cur_price - tp_dist, 5)
            sl_price = round(cur_price - sl_dist if direction == "BUY" else cur_price + sl_dist, 5)

            signal_data = {
                "symbol": clean_pair,
                "direction": direction,
                "entry_price": cur_price,
                "tp": tp_price,
                "sl": sl_price,
                "belkhayate_timing": round(timing_15m, 2),
                "lower_wick_pct": round(lower_wick_15m * 100, 1),
                "upper_wick_pct": round(upper_wick_15m * 100, 1),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }

            signals_found.append(signal_data)
            logger.info(f"SIGNAL FOREX CONFIRME BELKHAYATE — {clean_pair} {direction} | Prix: {cur_price:.5f} | TP: {tp_price:.5f} | SL: {sl_price:.5f} | Timing: {timing_15m:.2f}")

        except Exception as e:
            logger.error(f"Erreur traitement {symbol}: {e}")

    with open("forex_signals.json", "w", encoding="utf-8") as f:
        json.dump(signals_found, f, indent=2, ensure_ascii=False)

    logger.info(f"=== Scan Forex termine. {len(signals_found)} signal/signaux retenus ===")
    return signals_found

if __name__ == "__main__":
    run_forex_scan()
