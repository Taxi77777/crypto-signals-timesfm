# -*- coding: utf-8 -*-
"""
main.py — Point d'entrée principal pour le bot Crypto en boucle (Non-stop)
"""
import logging
import os
import sys
import time
import schedule
from datetime import datetime

# Forcer l'encodage UTF-8 pour Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

os.makedirs('logs', exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('logs/loop.log', encoding='utf-8'),
    ],
)
logger = logging.getLogger(__name__)

import run_once

def run_analysis_safe():
    logger.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    logger.info(f'🚀 Analyse récurrente Crypto démarrée — {datetime.now().strftime("%d/%m/%Y %H:%M:%S")}')
    logger.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━')
    try:
        run_once.main()
    except Exception as e:
        logger.error(f'❌ Erreur critique lors de l\'exécution de l\'analyse : {e}', exc_info=True)
    logger.info('━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n')

def main():
    logger.info('╔══════════════════════════════════════════════╗')
    logger.info('║    🤖 BOT SIGNAUX CRYPTO EN BOUCLE (5 IA)    ║')
    logger.info('║         github.com/Taxi77777                 ║')
    logger.info('╚══════════════════════════════════════════════╝')
    
    # Première exécution immédiate
    run_analysis_safe()
    
    # Planifier toutes les 5 minutes
    schedule.every(5).minutes.do(run_analysis_safe)
    logger.info('⏱️ Boucle active : analyse planifiée toutes les 5 minutes.')
    
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        logger.info('🛑 Bot arrêté par l\'utilisateur.')

if __name__ == '__main__':
    main()
