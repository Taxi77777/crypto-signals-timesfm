"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.0 — BOT ONCE (CLOUD 24/7)        ║
║     bot_once.py — Exécution 1 cycle complet pour GitHub Actions   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import sys
import time
import logging
from datetime import datetime

# Importer les modules du bot
from bot import scan_pair, monitor_positions, count_active_positions, _start_balance
from scanner import get_active_pairs
from timesfm_predictor import preload_model
from config import AUTO_SCAN, MAX_CONCURRENT, LEVERAGE, RISK_PER_TRADE_PCT, USE_SL
import mexc_api as api

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('IHP-CLOUD')

def run_single_cycle():
    log.info("=" * 65)
    log.info("  IHP v5.0 CLOUD EXECUTION — CYCLE UNIQUE 5 MIN")
    log.info(f"  Levier: x{LEVERAGE} | Risque: {RISK_PER_TRADE_PCT}% | SL: {'ACTIF' if USE_SL else 'DESACTIVE'}")
    log.info("=" * 65)

    # 1. Charger TimesFM
    log.info("[TimesFM] Chargement du modele Google TimesFM 2.5...")
    model = preload_model()
    if model is None:
        log.warning("[TimesFM] Non disponible — mode consensus 90%+")

    # 2. Récupérer les paires actives
    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"Paires actives a scanner : {len(active_pairs)}")

    # 3. Vérifier solde MEXC
    try:
        acc = api.get_account()
        balance = acc.get('balance', 100.0)
        log.info(f"Balance MEXC : {balance:.2f} USDT | Equity: {acc.get('equity', 0):.2f}")
    except Exception as e:
        log.warning(f"Impossible de lire le solde MEXC : {e}")
        balance = 100.0

    # 4. Monitoring positions existantes
    monitor_positions()

    # 5. Si déjà 1 position ouverte -> arrêt du scan
    if count_active_positions() >= MAX_CONCURRENT:
        log.info("1 position active deja en cours sur MEXC. Pas de nouveau trade.")
        return

    # 6. Scan des paires en parallèle
    log.info(f"🚀 Scan en cours sur les {len(active_pairs)} paires crypto...")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    signals = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(scan_pair, sym, balance): sym for sym in active_pairs}
        for fut in as_completed(futures, timeout=120):
            try:
                sig = fut.result()
                if sig:
                    signals.append(sig)
            except Exception:
                pass

    if not signals:
        log.info("Aucun signal valide trouve sur ce cycle.")
        return

    # Trier par force et consensus
    signals.sort(key=lambda s: (0 if s.strength == 'STRONG' else 1, -s.consensus_pct, -s.rr))
    best = signals[0]

    log.info(f"🎯 MEILLEUR SIGNAL DETECTE: {best.symbol} {best.direction} ({best.consensus_pct:.0f}%)")

    # Exécution du trade via bot.py
    from bot import execute_trade
    execute_trade(best, balance)

if __name__ == '__main__':
    run_single_cycle()
