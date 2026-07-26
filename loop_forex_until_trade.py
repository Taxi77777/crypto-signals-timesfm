"""
===============================================================================
BOUCLE DE SCAN CONTINU FOREX — IA & MOSTAFA BELKHAYATE
===============================================================================
- Scanne les 28 paires Forex en boucle (pause 15s)
- Déclenche et sauvegarde les signaux confirmés avec Belkhayate + Mèches
- Export vers MT4/MT5 Signal Receiver (forex_signals.json)
===============================================================================
"""

import time
import sys
import os
import logging
from run_forex_once import run_forex_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/forex_loop.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ForexLoop")

def main():
    logger.info("Demarrage de la boucle de scan continu Forex (IA & Mostafa Belkhayate)...")
    scan_count = 0

    while True:
        scan_count += 1
        logger.info(f"\n==================================================")
        logger.info(f"SCAN FOREX AUTOMATIQUE #{scan_count}")
        logger.info(f"==================================================")

        try:
            signals = run_forex_scan()
            if signals and len(signals) > 0:
                logger.info(f"==> {len(signals)} SIGNAL/SIGNAUX FOREX CONFIRMES TROUVES !")
                for s in signals:
                    logger.info(f"--> {s['symbol']} {s['direction']} | Entree: {s['entry_price']} | TP: {s['tp']} | SL: {s['sl']}")
        except Exception as e:
            logger.error(f"Erreur durant le scan Forex #{scan_count} : {e}")

        logger.info("Pause de 15 secondes avant le prochain scan...")
        time.sleep(15)

if __name__ == "__main__":
    main()
