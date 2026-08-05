"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.1 — BOT ONCE (CLOUD 24/7)        ║
║     bot_once.py — Un cycle complet pour GitHub Actions           ║
║                                                                  ║
║  MODE PAPER (PAPER_MODE=True, valeur par defaut) :               ║
║    Le pipeline complet tourne — 6 exchanges, consensus, TimesFM  ║
║    — mais AUCUN ordre n'est envoye a MEXC. Les entrees sont      ║
║    simulees et evaluees sur les bougies suivantes.               ║
║                                                                  ║
║    Raison : la strategie repose sur le carnet d'ordres et le     ║
║    CVD, donnees qui n'existent pas en historique. Un backtest    ║
║    est impossible ; le forward test est la seule mesure reelle.  ║
║                                                                  ║
║  MODE REEL (PAPER_MODE=False) :                                  ║
║    Ordres envoyes sur MEXC Futures.                              ║
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
    PAPER_MODE, TIMESFM_STRICT, TIMESFM_MIN_CONFIDENCE,
    MIN_CONSENSUS_PCT, MAX_HOLD_HOURS,
)
import mexc_api as api
import paper_engine as paper

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('IHP-CLOUD')


def _banner():
    mode = "PAPER (aucun ordre reel)" if PAPER_MODE else "REEL (ordres envoyes a MEXC)"
    log.info("=" * 68)
    log.info("  IHP v5.1 — CYCLE UNIQUE")
    log.info(f"  MODE            : {mode}")
    log.info(f"  Levier          : x{LEVERAGE} | SL : {'ACTIF' if USE_SL else 'DESACTIVE'}")
    log.info(f"  Marge par trade : {POSITION_MARGIN_PCT}% (plafond {MAX_MARGIN_USDT} USDT)")
    log.info(f"  Consensus min   : {MIN_CONSENSUS_PCT}%")
    log.info(f"  TimesFM         : strict={TIMESFM_STRICT} | conf min={TIMESFM_MIN_CONFIDENCE:.0%}")
    log.info(f"  Duree max trade : {MAX_HOLD_HOURS} h")
    log.info("=" * 68)


def run_single_cycle() -> int:
    _banner()

    # ── 0. Credentials ───────────────────────────────────────────
    # En mode paper, les cles MEXC ne sont pas indispensables :
    # seules les APIs publiques (klines, carnets) sont utilisees.
    missing = validate_env(require_trading=not PAPER_MODE)
    if missing:
        log.error(f"  VARIABLES MANQUANTES : {', '.join(missing)}")
        log.error("  Settings > Secrets and variables > Actions")
        return 1

    load_state()
    paper_state = paper.load_paper_state()

    # ── 1. Positions simulees en cours ───────────────────────────
    if PAPER_MODE:
        closed = paper.update_paper_positions(paper_state)
        if closed:
            for c in closed:
                tg_send(
                    f"{'✅' if c['pnl_usdt'] > 0 else '❌'} <b>[PAPER] {c['direction']} {c['symbol']}</b>\n"
                    f"  Sortie  : {c['exit_reason']}\n"
                    f"  Mouvement : {c['move_pct']:+.2f}%\n"
                    f"  PnL     : {c['pnl_usdt']:+.4f} USDT ({c['pnl_on_margin_pct']:+.1f}% de la marge)\n"
                    f"  Duree   : {c['duration_min']} min"
                )
        paper.save_paper_state(paper_state)
    else:
        monitor_positions()

    # ── 2. TimesFM ───────────────────────────────────────────────
    log.info("[TimesFM] Chargement du modele Google TimesFM 2.5...")
    if preload_model() is None:
        log.warning("[TimesFM] Non disponible — en mode strict, aucun trade ne sera valide.")
    else:
        log.info("[TimesFM] Modele pret.")

    # ── 3. Paires ────────────────────────────────────────────────
    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"Paires a scanner : {len(active_pairs)}")
    if not active_pairs:
        log.error("Aucune paire active — scanner MEXC injoignable ?")
        paper.save_paper_state(paper_state)
        save_state()
        return 1

    # ── 4. Solde ─────────────────────────────────────────────────
    balance = 0.0
    try:
        acc = api.get_account()
        balance = acc.get('balance', 0.0) if acc else 0.0
        if balance > 0:
            log.info(f"Balance MEXC : {balance:.2f} USDT | Equity: {acc.get('equity', 0):.2f}")
        else:
            log.warning("Solde MEXC nul ou illisible.")
    except Exception as e:
        log.warning(f"Lecture du solde impossible : {e}")

    if PAPER_MODE and balance <= 0:
        # En simulation, on part d'un capital de reference pour que les
        # statistiques restent lisibles meme sans acces au compte.
        balance = 100.0
        log.info("[PAPER] Capital de reference simule : 100.00 USDT")

    if not PAPER_MODE and balance <= 0:
        log.error("Solde nul — aucun trade reel possible.")
        save_state()
        return 0

    # ── 5. Limite de positions ───────────────────────────────────
    n_open = len(paper_state['open']) if PAPER_MODE else count_active_positions()
    if n_open >= MAX_CONCURRENT:
        log.info(f"{n_open} position(s) deja ouverte(s) — pas de nouveau trade ce cycle.")
        _report(paper_state)
        paper.save_paper_state(paper_state)
        save_state()
        return 0

    # ── 6. Scan ──────────────────────────────────────────────────
    log.info(f"Scan de {len(active_pairs)} paires (desequilibre 6 exchanges d'abord)...")
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
            log.warning(f"Scan interrompu : {e}")

    if not signals:
        log.info("Aucun signal valide sur ce cycle.")
        _report(paper_state)
        paper.save_paper_state(paper_state)
        save_state()
        return 0

    # ── 7. Meilleur signal ───────────────────────────────────────
    signals.sort(key=lambda s: (0 if s.strength == 'STRONG' else 1, -s.consensus_pct, -s.rr))
    best = signals[0]
    log.info(best.summary())

    # ── 8. Execution ─────────────────────────────────────────────
    if PAPER_MODE:
        trade = paper.open_paper_trade(best, balance, paper_state)
        if trade:
            tg_send(
                f"{'🟢' if best.direction == 'BUY' else '🔴'} <b>[PAPER] SIGNAL {best.direction} {best.symbol}</b>\n"
                f"  Entree    : {trade['entry']:.6f}\n"
                f"  TP        : {trade['tp']:.6f}\n"
                f"  Liquidation simulee : {trade['liq']:.6f}\n"
                f"  Consensus : {best.consensus_pct:.0f}% sur {best.exchanges_ok} exchanges\n"
                f"  TimesFM   : {best.timesfm_direction} conf={best.timesfm_confidence:.0%}\n"
                f"  <i>Aucun ordre reel envoye.</i>"
            )
    else:
        if not open_trade(best, balance):
            log.warning("Le trade n'a pas ete ouvert (voir lignes precedentes).")

    _report(paper_state)
    paper.save_paper_state(paper_state)
    save_state()
    return 0


def _report(paper_state: dict):
    """Affiche les statistiques cumulées du forward test."""
    if not PAPER_MODE:
        return
    stats = paper.compute_stats(paper_state)
    log.info("\n" + "=" * 68)
    for line in paper.format_stats(stats).splitlines():
        log.info("  " + line)
    log.info("=" * 68)


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
