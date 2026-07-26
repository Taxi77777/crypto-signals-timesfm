"""
loop_until_trade.py — Relance automatique du scanner Crypto jusqu'à ce qu'un ordre soit trouvé et exécuté.
"""

import time
import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("LoopTrader")

logger.info("🚀 Démarrage de la boucle de scan continue jusqu'à détection et ouverture d'un ordre !")

scan_count = 0
while True:
    scan_count += 1
    logger.info(f"\n==================================================")
    logger.info(f"🔄 SCAN CRYPTO AUTOMATIQUE #{scan_count}")
    logger.info(f"==================================================")
    
    try:
        res = subprocess.run([sys.executable, "-u", "run_once.py"], capture_output=True, text=True, encoding="utf-8", errors="ignore")
        output = res.stdout or ""
        if output:
            for line in output.splitlines():
                if any(k in line for k in ["IMPULSION", "MOSTAFA", "SIGNAL", "Signal", "TRADE", "BILAN", "Orderbook", "Analyse"]):
                    logger.info(line)
        
        # Vérifier si un ordre a été ouvert
        if "TRADE ASPIRATION OUVERT" in output or "Ordre passé" in output or "place_order" in output or "Signal Telegram" in output:
            logger.info("🎉 UN ORDRE A ÉTÉ DÉTECTÉ ET OUVERT AVEC SUCCÈS sur MEXC Futures !")
            break
        elif "Slots de trading remplis" in output:
            logger.info("ℹ️ Une position est déjà ouverte sur MEXC Futures. Fin du scan.")
            break
    except Exception as e:
        logger.error(f"Erreur durant le scan #{scan_count}: {e}")
        
    logger.info("⏳ Aucun ordre valide au scan courant. Prochain scan dans 15 secondes...")
    time.sleep(15)
