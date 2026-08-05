"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.1 — BOT ONCE (CLOUD 24/7)        ║
║     bot_once.py — Execution d'un cycle complet pour GitHub Actions║
║                                                                  ║
║  CORRECTIFS v5.1 :                                               ║
║  • open_trade() au lieu de execute_trade() (fonction inexistante) ║
║  • Verification des variables d'environnement au demarrage        ║
║  • Chargement / sauvegarde de l'etat entre les runs               ║
║  • Sortie en erreur explicite si les credentials manquent         ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from bot import (
    scan_pair, monitor_positions, count_active_positions,
    open_trade, load_state, save_state, tg_send,
)
from scanner import get_active_pairs
from timesfm_predictor import preload_model
from config import (
    AUTO_SCAN, MAX_CONCURRENT, LEVERAGE, USE_SL,
    POSITION_MARGIN_PCT, MAX_MARGIN_USDT, validate_env,
)
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


def run_single_cycle() -> int:
    log.info("=" * 65)
    log.info("  IHP v5.1 CLOUD EXECUTION — CYCLE UNIQUE")
    log.info(f"  Levier: x{LEVERAGE} | SL: {'ACTIF' if USE_SL else 'DESACTIVE'}")
    log.info(f"  Marge par trade: {POSITION_MARGIN_PCT}% (plafond {MAX_MARGIN_USDT} USDT)")
    log.info("=" * 65)

    # ── 0. Verification des credentials ──────────────────────────
    missing = validate_env(require_trading=True)
    if missing:
        log.error("=" * 65)
        log.error(f"  VARIABLES D'ENVIRONNEMENT MANQUANTES : {', '.join(missing)}")
        log.error("  Verifie les repository secrets GitHub et le bloc 'env:'")
        log.error("  de .github/workflows/crypto_signals.yml")
        log.error("=" * 65)
        return 1

    # ── 1. Etat des runs precedents ──────────────────────────────
    load_state()

    # ── 2. Chargement TimesFM ────────────────────────────────────
    log.info("[TimesFM] Chargement du modele Google TimesFM 2.5...")
    model = preload_model()
    if model is None:
        log.warning("[TimesFM] Non disponible — consensus >=90% requis pour trader")
    else:
        log.info("[TimesFM] Modele pret.")

    # ── 3. Paires actives ────────────────────────────────────────
    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"Paires actives a scanner : {len(active_pairs)}")
    if not active_pairs:
        log.error("Aucune paire active — scanner MEXC injoignable ?")
        save_state()
        return 1

    # ── 4. Solde MEXC ────────────────────────────────────────────
    try:
        acc = api.get_account()
        if not acc:
            log.error("get_account() vide — cles API invalides ou refusees par MEXC.")
            balance = 0.0
        else:
            balance = acc.get('balance', 0.0)
            log.info(f"Balance MEXC : {balance:.2f} USDT | Equity: {acc.get('equity', 0):.2f}")
    except Exception as e:
        log.error(f"Impossible de lire le solde MEXC : {e}")
        balance = 0.0

    if balance <= 0:
        log.error("Solde nul ou illisible — aucun trade ne sera tente ce cycle.")
        monitor_positions()
        save_state()
        return 0

    # ── 5. Monitoring des positions existantes ───────────────────
    monitor_positions()

    # ── 6. Limite de positions simultanees ───────────────────────
    if count_active_positions() >= MAX_CONCURRENT:
        log.info(f"{MAX_CONCURRENT} position(s) deja active(s) sur MEXC. Pas de nouveau trade.")
        save_state()
        return 0

    # ── 7. Scan parallele ────────────────────────────────────────
    log.info(f"Scan en cours sur {len(active_pairs)} paires...")
    signals = []
    with ThreadPoolExecutor(max_workers=15) as executor:
        futures = {executor.submit(scan_pair, sym, balance): sym for sym in active_pairs}
        try:
            for fut in as_completed(futures, timeout=300):
                try:
                    sig = fut.result()
                    if sig:
                        signals.append(sig)
                except Exception:
                    pass
        except Exception as e:
            log.warning(f"Scan interrompu (timeout global) : {e}")

    if not signals:
        log.info("Aucun signal valide trouve sur ce cycle.")
        save_state()
        return 0

    # ── 8. Meilleur signal ───────────────────────────────────────
    signals.sort(key=lambda s: (0 if s.strength == 'STRONG' else 1, -s.consensus_pct, -s.rr))
    best = signals[0]
    log.info(f"MEILLEUR SIGNAL : {best.symbol} {best.direction} ({best.consensus_pct:.0f}%)")

    # ── 9. Execution ─────────────────────────────────────────────
    ok = open_trade(best, balance)
    if not ok:
        log.warning("Le trade n'a pas ete ouvert (voir les lignes precedentes).")

    save_state()
    return 0


if __name__ == '__main__':
    try:
        sys.exit(run_single_cycle())
    except Exception as e:
        log.critical(f"Erreur fatale du cycle : {e}", exc_info=True)
        try:
            tg_send(f"⛔ IHP cycle en erreur : {str(e)[:200]}")
        except Exception:
            pass
        sys.exit(1)
