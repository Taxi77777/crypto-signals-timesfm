"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — PAPER TRADING ENGINE              ║
║     paper_engine.py                                              ║
║                                                                  ║
║  Pourquoi ce module existe :                                     ║
║                                                                  ║
║  La strategie repose sur le carnet d'ordres (OBI, Stacked        ║
║  Imbalances) et le CVD. Ces donnees N'EXISTENT PAS en            ║
║  historique : aucun exchange ne publie gratuitement les          ║
║  snapshots L2 passes. Un backtest classique est donc             ║
║  IMPOSSIBLE pour cette strategie.                                ║
║                                                                  ║
║  La seule mesure honnete est le forward test : on enregistre     ║
║  les signaux en direct, on simule l'execution, et on evalue      ║
║  le resultat sur les bougies qui suivent. Aucun ordre reel.      ║
║                                                                  ║
║  Ce que ce moteur simule fidelement :                            ║
║   • Entree au prix du signal                                     ║
║   • Sortie en Take Profit (touche par le high/low d'une bougie)  ║
║   • Sortie en Stop Loss si USE_SL est actif                      ║
║   • LIQUIDATION : avec un levier x40 sans SL, une variation      ║
║     adverse d'environ 100/levier % efface la marge. C'est le     ║
║     scenario que le mode reel cacherait jusqu'au jour ou il      ║
║     arrive.                                                      ║
║   • Expiration apres MAX_HOLD_HOURS                              ║
║   • Frais taker a l'entree ET a la sortie                        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os
import json
import time
import csv
import logging
from datetime import datetime, timezone

import mexc_api as api
from config import (
    LEVERAGE, POSITION_MARGIN_PCT, MAX_MARGIN_USDT, USE_SL,
    MAX_HOLD_HOURS, TAKER_FEE_PCT, LIQUIDATION_BUFFER,
    PAPER_STATE_FILE, PAPER_TRADES_FILE,
)

log = logging.getLogger("IHP-PAPER")

# Bougies utilisees pour rejouer ce qui s'est passe apres l'entree.
REPLAY_INTERVAL = "5m"
REPLAY_LIMIT    = 500          # ~41 h de couverture en 5 min


# ══════════════════════════════════════════════════════════════════
#  PERSISTANCE
# ══════════════════════════════════════════════════════════════════
def _empty_state() -> dict:
    return {'open': [], 'closed': [], 'created_at': datetime.now(timezone.utc).isoformat(timespec='seconds')}


def load_paper_state() -> dict:
    if not os.path.exists(PAPER_STATE_FILE):
        return _empty_state()
    try:
        with open(PAPER_STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
        s.setdefault('open', [])
        s.setdefault('closed', [])
        return s
    except Exception as e:
        log.warning(f"[PAPER] Etat illisible ({e}) — reinitialisation.")
        return _empty_state()


def save_paper_state(state: dict):
    try:
        state['updated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
        with open(PAPER_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        log.error(f"[PAPER] Sauvegarde impossible : {e}")


# ══════════════════════════════════════════════════════════════════
#  OUVERTURE D'UNE POSITION SIMULEE
# ══════════════════════════════════════════════════════════════════
def liquidation_price(entry: float, direction: str) -> float:
    """
    Prix approximatif de liquidation en marge isolee.

    Sans stop-loss, la perte maximale est la marge engagee. Elle est
    atteinte quand le prix bouge de ~100/levier % contre la position.
    LIQUIDATION_BUFFER (< 1) rapproche legerement le seuil pour tenir
    compte des frais et de la marge de maintenance.

    Levier x40 -> environ 2.5 % x buffer.
    """
    adverse_pct = (100.0 / max(LEVERAGE, 1)) * LIQUIDATION_BUFFER / 100.0
    if direction == 'BUY':
        return entry * (1.0 - adverse_pct)
    return entry * (1.0 + adverse_pct)


def open_paper_trade(signal, balance: float, state: dict) -> dict:
    """Enregistre une position simulee. N'envoie AUCUN ordre."""
    margin = min(balance * (POSITION_MARGIN_PCT / 100.0), MAX_MARGIN_USDT)
    if margin <= 0 or signal.entry <= 0:
        log.warning(f"[PAPER] {signal.symbol} marge ou prix invalide — ignore")
        return {}

    notional = margin * LEVERAGE
    liq      = liquidation_price(signal.entry, signal.direction)

    trade = {
        'symbol':        signal.symbol,
        'direction':     signal.direction,
        'entry':         float(signal.entry),
        'tp':            float(signal.tp),
        'sl':            float(signal.sl) if (USE_SL and signal.sl > 0) else 0.0,
        'liq':           float(liq),
        'margin':        round(margin, 4),
        'notional':      round(notional, 4),
        'leverage':      LEVERAGE,
        'open_time_ms':  int(time.time() * 1000),
        'open_time':     datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'consensus_pct': float(getattr(signal, 'consensus_pct', 0)),
        'timesfm':       f"{getattr(signal, 'timesfm_direction', '?')}"
                         f"({getattr(signal, 'timesfm_change_pct', 0):+.2f}%)",
        'strength':      getattr(signal, 'strength', '?'),
        'rr':            float(getattr(signal, 'rr', 0)),
        'reason':        (getattr(signal, 'reason', '') or '')[:200],
    }

    state['open'].append(trade)
    log.info(
        f"[PAPER] OUVERTURE {trade['direction']} {trade['symbol']} @ {trade['entry']:.6f} | "
        f"TP={trade['tp']:.6f} | LIQ={trade['liq']:.6f} | "
        f"notionnel={trade['notional']:.2f} USDT"
    )
    return trade


# ══════════════════════════════════════════════════════════════════
#  EVALUATION DES POSITIONS OUVERTES
# ══════════════════════════════════════════════════════════════════
def _pnl(trade: dict, exit_price: float) -> tuple:
    """Retourne (pnl_usdt, move_pct) frais inclus (taker aller + retour)."""
    entry = trade['entry']
    if entry <= 0:
        return 0.0, 0.0

    if trade['direction'] == 'BUY':
        move_pct = (exit_price - entry) / entry * 100.0
    else:
        move_pct = (entry - exit_price) / entry * 100.0

    notional = trade['notional']
    gross    = notional * (move_pct / 100.0)
    fees     = notional * (TAKER_FEE_PCT / 100.0) * 2.0     # entree + sortie
    return gross - fees, move_pct


def _resolve_trade(trade: dict) -> dict:
    """
    Rejoue les bougies 5m posterieures a l'entree et determine si la
    position s'est cloturee, et comment.

    Ordre de verification a l'interieur d'une bougie : LIQUIDATION /
    STOP d'abord, puis TAKE PROFIT. On ne connait pas l'ordre reel des
    ticks, on retient donc l'hypothese defavorable — c'est la seule
    honnete pour une mesure de performance.
    """
    symbol = trade['symbol']
    try:
        df = api.get_klines(symbol, REPLAY_INTERVAL, REPLAY_LIMIT)
    except Exception as e:
        log.debug(f"[PAPER] {symbol} klines indisponibles : {e}")
        return {}

    if df is None or len(df) == 0:
        return {}

    entry_ms = trade['open_time_ms']
    after    = df[df['open_time'] > entry_ms]
    if len(after) == 0:
        return {}   # pas encore de bougie close depuis l'entree

    direction = trade['direction']
    tp   = trade['tp']
    sl   = trade['sl']
    liq  = trade['liq']

    for _, c in after.iterrows():
        high, low, close = float(c['high']), float(c['low']), float(c['close'])
        ts = int(c['open_time'])

        if direction == 'BUY':
            if low <= liq:
                return {'exit_price': liq, 'exit_reason': 'LIQUIDATION', 'exit_ms': ts}
            if sl > 0 and low <= sl:
                return {'exit_price': sl, 'exit_reason': 'STOP_LOSS', 'exit_ms': ts}
            if tp > 0 and high >= tp:
                return {'exit_price': tp, 'exit_reason': 'TAKE_PROFIT', 'exit_ms': ts}
        else:
            if high >= liq:
                return {'exit_price': liq, 'exit_reason': 'LIQUIDATION', 'exit_ms': ts}
            if sl > 0 and high >= sl:
                return {'exit_price': sl, 'exit_reason': 'STOP_LOSS', 'exit_ms': ts}
            if tp > 0 and low <= tp:
                return {'exit_price': tp, 'exit_reason': 'TAKE_PROFIT', 'exit_ms': ts}

        # Expiration
        if (ts - entry_ms) >= MAX_HOLD_HOURS * 3600 * 1000:
            return {'exit_price': close, 'exit_reason': 'EXPIRATION', 'exit_ms': ts}

    return {}   # toujours ouverte


def update_paper_positions(state: dict) -> list:
    """Evalue toutes les positions ouvertes. Retourne celles qui viennent de se fermer."""
    still_open  = []
    just_closed = []

    for trade in state.get('open', []):
        res = _resolve_trade(trade)
        if not res:
            still_open.append(trade)
            continue

        pnl_usdt, move_pct = _pnl(trade, res['exit_price'])
        closed = dict(trade)
        closed.update({
            'exit_price':   round(res['exit_price'], 8),
            'exit_reason':  res['exit_reason'],
            'exit_time':    datetime.fromtimestamp(res['exit_ms'] / 1000, tz=timezone.utc).isoformat(timespec='seconds'),
            'duration_min': int((res['exit_ms'] - trade['open_time_ms']) / 60000),
            'move_pct':     round(move_pct, 4),
            'pnl_usdt':     round(pnl_usdt, 4),
            'pnl_on_margin_pct': round(pnl_usdt / trade['margin'] * 100.0, 2) if trade['margin'] else 0.0,
        })
        just_closed.append(closed)
        state.setdefault('closed', []).append(closed)

        icon = "GAGNE" if pnl_usdt > 0 else "PERDU"
        log.info(
            f"[PAPER] CLOTURE {icon} {closed['direction']} {closed['symbol']} "
            f"({closed['exit_reason']}) | move={move_pct:+.2f}% | "
            f"PnL={pnl_usdt:+.4f} USDT ({closed['pnl_on_margin_pct']:+.1f}% de la marge) | "
            f"{closed['duration_min']} min"
        )
        _append_csv(closed)

    state['open'] = still_open
    return just_closed


def _append_csv(closed: dict):
    exists = os.path.exists(PAPER_TRADES_FILE)
    try:
        with open(PAPER_TRADES_FILE, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            if not exists:
                w.writerow([
                    'open_time', 'exit_time', 'symbol', 'direction', 'entry', 'exit_price',
                    'tp', 'sl', 'liq', 'exit_reason', 'move_pct', 'pnl_usdt',
                    'pnl_on_margin_pct', 'duration_min', 'notional', 'margin',
                    'leverage', 'consensus_pct', 'timesfm', 'strength', 'rr',
                ])
            w.writerow([
                closed['open_time'], closed['exit_time'], closed['symbol'], closed['direction'],
                closed['entry'], closed['exit_price'], closed['tp'], closed['sl'], closed['liq'],
                closed['exit_reason'], closed['move_pct'], closed['pnl_usdt'],
                closed['pnl_on_margin_pct'], closed['duration_min'], closed['notional'],
                closed['margin'], closed['leverage'], closed['consensus_pct'],
                closed['timesfm'], closed['strength'], closed['rr'],
            ])
    except Exception as e:
        log.error(f"[PAPER] Ecriture CSV impossible : {e}")


# ══════════════════════════════════════════════════════════════════
#  STATISTIQUES
# ══════════════════════════════════════════════════════════════════
def compute_stats(state: dict) -> dict:
    closed = state.get('closed', [])
    n = len(closed)
    if n == 0:
        return {'n_trades': 0, 'n_open': len(state.get('open', []))}

    pnls   = [c['pnl_usdt'] for c in closed]
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    gross_win  = sum(wins)
    gross_loss = abs(sum(losses))

    # Drawdown maximum sur la courbe d'equity cumulee
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in pnls:
        equity += p
        peak    = max(peak, equity)
        max_dd  = max(max_dd, peak - equity)

    reasons = {}
    for c in closed:
        reasons[c['exit_reason']] = reasons.get(c['exit_reason'], 0) + 1

    return {
        'n_trades':      n,
        'n_open':        len(state.get('open', [])),
        'n_wins':        len(wins),
        'n_losses':      len(losses),
        'win_rate':      round(len(wins) / n * 100.0, 1),
        'total_pnl':     round(sum(pnls), 4),
        'avg_win':       round(gross_win / len(wins), 4) if wins else 0.0,
        'avg_loss':      round(-gross_loss / len(losses), 4) if losses else 0.0,
        'expectancy':    round(sum(pnls) / n, 4),
        'profit_factor': round(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        'max_drawdown':  round(max_dd, 4),
        'exit_reasons':  reasons,
    }


def format_stats(stats: dict) -> str:
    if stats.get('n_trades', 0) == 0:
        return (
            f"PAPER TRADING — aucun trade cloture pour l'instant\n"
            f"Positions simulees en cours : {stats.get('n_open', 0)}"
        )

    pf = stats['profit_factor']
    pf_str = f"{pf:.2f}" if pf is not None else "infini (aucune perte)"
    reasons = " | ".join(f"{k}:{v}" for k, v in sorted(stats['exit_reasons'].items()))

    return (
        f"PAPER TRADING — {stats['n_trades']} trades clotures\n"
        f"  Taux de reussite : {stats['win_rate']:.1f}% ({stats['n_wins']}G / {stats['n_losses']}P)\n"
        f"  PnL total        : {stats['total_pnl']:+.4f} USDT\n"
        f"  Esperance/trade  : {stats['expectancy']:+.4f} USDT\n"
        f"  Gain moyen       : {stats['avg_win']:+.4f} | Perte moyenne : {stats['avg_loss']:+.4f}\n"
        f"  Profit factor    : {pf_str}\n"
        f"  Drawdown max     : -{stats['max_drawdown']:.4f} USDT\n"
        f"  Sorties          : {reasons}\n"
        f"  Positions ouvertes : {stats['n_open']}"
    )
