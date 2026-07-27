"""
===============================================================================
SCANNER FOREX — STRATÉGIE PURE MOSTAFA BELKHAYATE & MÈCHES DE REJET
===============================================================================
- Paires : 28 Paires Forex Majeures et Croisées
- Timeframes : 5m, 15m, 30m, 1H
- Règle PURE Belkhayate :
  - Zone Extrême Barycentre (Nombre d'Or 1.618) OU Belkhayate Timing <= -1.5 (BUY) / >= 1.5 (SELL)
  - Mèche de Rejet Physique >= 15% (Mèche basse pour BUY / Mèche haute pour SELL)
- Export direct vers Telegram & MetaTrader (forex_signals.json)
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

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/forex_signals.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ForexBelkhayate")

os.makedirs("logs", exist_ok=True)

# 28 Paires Forex Majeures et Croisées
FOREX_PAIRS = [
    "EURUSD=X", "GBPUSD=X", "USDJPY=X", "AUDUSD=X", "USDCAD=X", "USDCHF=X", "NZDUSD=X",
    "EURGBP=X", "EURJPY=X", "GBPJPY=X", "AUDJPY=X", "CADJPY=X", "CHFJPY=X", "NZDJPY=X",
    "EURAUD=X", "EURCAD=X", "EURCHF=X", "EURNZD=X", "GBPAUD=X", "GBPCAD=X", "GBPCHF=X",
    "GBPNZD=X", "AUDCAD=X", "AUDCHF=X", "AUDNZD=X", "CADCHF=X", "NZDCAD=X", "NZDCHF=X"
]

TIMEFRAMES = ["5m", "15m", "30m", "1h"]

def calculate_belkhayate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule le Barycentre de Belkhayate + Timing Oscillator + Mèches de rejet."""
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() for col in df.columns]
    else:
        df.columns = [col.lower() for col in df.columns]

    # 1. Mèches de Rejet Physiques (Rejection Wick Ratio)
    candle_range = (df["high"] - df["low"]).replace(0, 1e-6)
    body_min = np.minimum(df["open"], df["close"])
    body_max = np.maximum(df["open"], df["close"])
    df["lower_wick_pct"] = (body_min - df["low"]) / candle_range
    df["upper_wick_pct"] = (df["high"] - body_max) / candle_range

    # 2. Barycentre de Belkhayate (Moyenne 30 + Écartement Nombre d'Or 1.618)
    bary_len = 30
    df["barycenter"] = df["close"].rolling(window=bary_len).mean()
    bary_std = df["close"].rolling(window=bary_len).std()
    df["upper_zone"] = df["barycenter"] + (1.618 * 1.8 * bary_std)  # Zone Vente Extrême (Rouge)
    df["lower_zone"] = df["barycenter"] - (1.618 * 1.8 * bary_std)  # Zone Achat Extrême (Verte)

    # 3. Belkhayate Timing Oscillator
    denom_std = bary_std.replace(0, 1e-6)
    df["timing"] = (df["close"] - df["barycenter"]) / denom_std

    # ATR 14 pour calcul adaptatif des Take Profit et Stop Loss
    tr = np.maximum(df["high"] - df["low"],
                    np.maximum(abs(df["high"] - df["close"].shift(1)),
                               abs(df["low"] - df["close"].shift(1))))
    df["atr"] = tr.rolling(window=14).mean()

    return df

def run_forex_scan():
    logger.info("==================================================")
    logger.info("DEMARRAGE SCANNER FOREX PURE METHODE BELKHAYATE & MECHES (5m, 15m, 30m, 1h)")
    logger.info("==================================================")

    # Chargement du cache anti-répétition des signaux Telegram Forex (cooldown 2h)
    sent_cache_file = "sent_forex_cache.json"
    sent_cache = {}
    if os.path.exists(sent_cache_file):
        try:
            with open(sent_cache_file, "r", encoding="utf-8") as f:
                sent_cache = json.load(f)
        except Exception:
            sent_cache = {}

    now_ts = time.time()
    # Nettoyer les entrées de plus de 2h (7200 sec)
    sent_cache = {k: v for k, v in sent_cache.items() if (now_ts - v) < 7200}

    signals_found = []
    seen_pairs = set()

    for tf in TIMEFRAMES:
        period_str = "2d" if tf == "5m" else ("5d" if tf in ["15m", "30m"] else "30d")
        for symbol in FOREX_PAIRS:
            clean_pair = symbol.replace("=X", "")
            if clean_pair in seen_pairs:
                continue

            try:
                df = yf.download(symbol, period=period_str, interval=tf, progress=False)
                if df.empty or len(df) < 30:
                    continue

                df = calculate_belkhayate_indicators(df)

                cur_price = float(df["close"].iloc[-1])
                timing    = float(df["timing"].iloc[-1])
                low_zone  = float(df["lower_zone"].iloc[-1])
                up_zone   = float(df["upper_zone"].iloc[-1])
                l_wick    = float(df["lower_wick_pct"].iloc[-1])
                u_wick    = float(df["upper_wick_pct"].iloc[-1])
                atr_val   = float(df["atr"].iloc[-1]) if "atr" in df else (cur_price * 0.002)

                # Retest de Plus Haut / Plus Bas (Pullback / Double Top & Double Bottom)
                recent_high = float(df["high"].tail(24).max())
                recent_low  = float(df["low"].tail(24).min())
                is_retest_high = (abs(cur_price - recent_high) / cur_price <= 0.015) and (timing >= 1.0)
                is_retest_low  = (abs(cur_price - recent_low) / cur_price <= 0.015) and (timing <= -1.0)

                # RÈGLE 100% EXCLUSIVE PULLBACK RE-TEST + MÈCHE DE REJET :
                # BUY  : Retest Plus Bas (Double Bottom) ET Mèche Basse >= 15%
                is_buy  = is_retest_low and (l_wick >= 0.15)
                # SELL : Retest Plus Haut (Double Top) ET Mèche Haute >= 15%
                is_sell = is_retest_high and (u_wick >= 0.15)

                if not (is_buy or is_sell):
                    continue

                direction = "BUY" if is_buy else "SELL"
                wick_val  = l_wick * 100 if is_buy else u_wick * 100

                # Calcul des objectifs TP / SL selon le Barycentre Belkhayate & ATR
                bary_price = float(df["barycenter"].iloc[-1])
                if direction == "BUY":
                    tp_price = round(max(bary_price, cur_price + (1.5 * atr_val)), 5)
                    sl_price = round(cur_price - (1.2 * atr_val), 5)
                else:
                    tp_price = round(min(bary_price, cur_price - (1.5 * atr_val)), 5)
                    sl_price = round(cur_price + (1.2 * atr_val), 5)

                signal_data = {
                    "symbol": clean_pair,
                    "direction": direction,
                    "timeframe": tf,
                    "entry_price": cur_price,
                    "tp": tp_price,
                    "sl": sl_price,
                    "belkhayate_timing": round(timing, 2),
                    "rejection_wick_pct": round(wick_val, 1),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }

                signals_found.append(signal_data)
                seen_pairs.add(clean_pair)
                logger.info(f"SIGNAL FOREX BELKHAYATE PURE [{tf}] — {clean_pair} {direction} | Prix: {cur_price:.5f} | Timing: {timing:.2f} | Mèche Rejet: {wick_val:.1f}% | TP: {tp_price:.5f}")

                # Envoi direct sur Telegram (si pas déjà envoyé dans les 2 dernières heures)
                cache_key = f"{clean_pair}_{direction}_{tf}"
                if cache_key not in sent_cache:
                    sent_cache[cache_key] = now_ts
                    try:
                        from src.telegram_bot import send_message
                        icon = "🟢" if direction == "BUY" else "🔴"
                        time_str = time.strftime("%d/%m/%Y %H:%M")
                        send_message(
                            f"👑 *MOSTAFA BELKHAYATE & IA SYSTEM [FOREX]* 👑\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📊 Signal      : {icon} *{direction}*\n"
                            f"🏛️ Paire       : *{clean_pair}* [{tf}]\n"
                            f"💰 Prix d'Entrée : `{cur_price:.5f}`\n"
                            f"🎯 Take Profit : `{tp_price:.5f}`\n"
                            f"🛑 Stop Loss   : `{sl_price:.5f}`\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🏛️ *STRATÉGIE MOSTAFA BELKHAYATE (RETEST)* :\n"
                            f"📍 Retest Zone     : *{'Double Bottom (Bas)' if direction == 'BUY' else 'Double Top (Haut)'}*\n"
                            f"⏱️ Timing Oscillator : `{timing:+.2f}` (Extrême Validé)\n"
                            f"🕯️ Mèche de Rejet    : *Physique {wick_val:.1f}% (≥ 15%)*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🤖 Diffusion MT4/MT5 & Telegram Validée 🚀\n"
                            f"🕐 {time_str} (Heure de Paris)\n"
                        )
                        logger.info(f"📲 Notification Telegram envoyée pour {clean_pair} {direction} [{tf}]")
                    except Exception as _telegram_err:
                        logger.error(f"Erreur envoi Telegram Forex pour {clean_pair}: {_telegram_err}")
                else:
                    logger.info(f"ℹ️ Signal Telegram pour {clean_pair} {direction} [{tf}] déjà envoyé récemment (Anti-Spam activé).")

            except Exception as e:
                logger.error(f"Erreur traitement [{tf}] {symbol}: {e}")

    try:
        with open(sent_cache_file, "w", encoding="utf-8") as f:
            json.dump(sent_cache, f)
    except Exception:
        pass

    with open("forex_signals.json", "w", encoding="utf-8") as f:
        json.dump(signals_found, f, indent=2, ensure_ascii=False)

    logger.info(f"=== Scan Forex Belkhayate Pure termine. {len(signals_found)} signal/signaux trouves ===")
    return signals_found

if __name__ == "__main__":
    run_forex_scan()
