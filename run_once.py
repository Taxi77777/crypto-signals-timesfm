"""
run_once.py — Analyse crypto unique pour GitHub Actions
Lance une analyse complète, envoie les signaux forts sur Telegram, puis quitte.
"""

import logging
import os
import sys
import json
import time
from datetime import datetime, timezone

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/signals.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import config
from src.data_fetcher       import fetch_all_pairs, prepare_timesfm_input
from src.indicators         import compute_all_indicators
from src.timesfm_predictor  import predict_timesfm
from src.signal_generator   import generate_signal
from src.telegram_bot       import send_signal


def main():
    logger.info("=== GitHub Actions — Analyse Crypto démarrée ===")

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant !")
        sys.exit(1)

    logger.info(f"Analyse de {len(config.CRYPTO_PAIRS)} cryptos...")
    all_data = fetch_all_pairs()

    if not all_data:
        logger.error("Aucune donnée récupérée")
        sys.exit(1)

    signals = []
    for symbol, df in all_data.items():
        pair_name = config.PAIR_NAMES.get(symbol, symbol)
        try:
            df_ind      = compute_all_indicators(df)
            if df_ind.empty:
                continue
            price_series = prepare_timesfm_input(df)
            predictions  = predict_timesfm(price_series)
            signal       = generate_signal(symbol, df_ind, predictions)
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.error(f"Erreur {pair_name}: {e}")
            continue

    # Signaux forts uniquement
    strong_signals = [s for s in signals if s.is_strong and s.signal != "HOLD"]

    # Export JSON pour le site web
    web_data = {
        "last_update": datetime.now(timezone.utc).isoformat(),
        "signals": [
            {
                "pair_name":     s.pair_name,
                "signal":        s.signal,
                "current_price": s.current_price,
                "take_profit":   s.take_profit,
                "stop_loss":     s.stop_loss,
                "confidence":    s.confidence,
                "rsi":           s.rsi,
                "macd_trend":    s.macd_trend,
                "forecast_dir":  s.forecast_dir,
            }
            for s in strong_signals
        ]
    }

    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(web_data, f, indent=2, ensure_ascii=False)
    logger.info("signals.json mis à jour.")

    if strong_signals:
        logger.info(f"Envoi de {len(strong_signals)} signaux forts sur Telegram...")
        for s in strong_signals:
            send_signal(s)
            time.sleep(0.5)
        logger.info(f"OK — {len(strong_signals)} signaux envoyés !")
    else:
        logger.info("Aucun signal fort détecté ce scan.")

    logger.info("=== Analyse terminée ===")


if __name__ == "__main__":
    main()
