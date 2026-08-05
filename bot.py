"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.0                               ║
║     bot.py — Orchestrateur Principal                            ║
║                                                                  ║
║  RÈGLES :                                                        ║
║  • 1 seul trade à la fois (MAX_CONCURRENT = 1)                  ║
║  • Levier x40                                                    ║
║  • Scan toutes les 60 secondes                                   ║
║  • Dès qu'un trade est fermé → recherche immédiate              ║
║  • Triple confirmation : Carnet + TimesFM + Tendance 4H          ║
║  • TP + SL garantis sur MEXC                                     ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
import csv
import os
import requests
from datetime import datetime
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import mexc_api as api
from strategy import OrderFlowStrategy, Signal
from scanner import get_active_pairs
from timesfm_predictor import preload_model
from config import (
    TIMEFRAME, KLINE_LIMIT, UPDATE_INTERVAL_SEC,
    SIGNAL_COOLDOWN_SEC, RISK_PER_TRADE_PCT, MAX_CONCURRENT,
    MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT, LEVERAGE,
    USE_TRAILING_SL, TRAILING_TRIGGER, TRAILING_STEP,
    LOG_TRADES, LOG_FILE, LOG_SIGNALS, SIGNAL_LOG_FILE,
    TG_ENABLED, TG_BOT_TOKEN, TG_CHAT_ID,
    AUTO_SCAN, AUTO_SCAN_TOP_N, AUTO_SCAN_INTERVAL,
)

# ══════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log', encoding='utf-8'),
    ]
)
log = logging.getLogger('IHP-BOT')

# ══════════════════════════════════════════════════════════════════
#  ÉTAT GLOBAL
# ══════════════════════════════════════════════════════════════════
_lock             = Lock()
_active_positions: dict = {}   # symbol → {direction, side, entry, sl, tp, vol, order_id}
_last_signal_time: dict = {}   # symbol → timestamp
_daily_pnl        = 0.0
_start_balance    = 0.0
_trade_count      = 0
_win_count        = 0


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════
def tg_send(msg: str):
    if not TG_ENABLED or not TG_BOT_TOKEN:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={'chat_id': TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=5
        )
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  RISK MANAGEMENT
# ══════════════════════════════════════════════════════════════════
def calc_lot_size(balance: float, entry: float, sl: float) -> float:
    if entry <= 0 or sl <= 0 or abs(entry - sl) < 1e-10:
        return 0.0
    risk_usd = balance * (RISK_PER_TRADE_PCT / 100.0)
    risk_per_contract = abs(entry - sl)
    vol = (risk_usd / risk_per_contract) * LEVERAGE
    return round(max(vol, 0.001), 3)


def count_active_positions() -> int:
    try:
        return len(api.get_open_positions())
    except Exception:
        return len(_active_positions)


def check_risk_limits(balance: float) -> bool:
    """Retourne True si on doit arrêter le trading."""
    if _start_balance <= 0:
        return False
    loss_pct = (_start_balance - balance) / _start_balance * 100
    if loss_pct >= MAX_DAILY_LOSS_PCT:
        log.warning(f"STOP JOURNALIER : -{loss_pct:.1f}%")
        tg_send(f"STOP JOURNALIER -{loss_pct:.1f}% — Trading suspendu")
        return True
    if loss_pct >= MAX_DRAWDOWN_PCT:
        log.critical(f"DRAWDOWN MAX {loss_pct:.1f}% — Bot arrete !")
        tg_send(f"DRAWDOWN MAX {loss_pct:.1f}% — Bot arrete !")
        return True
    return False


# ══════════════════════════════════════════════════════════════════
#  OUVERTURE TRADE
# ══════════════════════════════════════════════════════════════════
def open_trade(signal: Signal, balance: float) -> bool:
    global _trade_count

    if not signal.is_valid():
        return False

    with _lock:
        if count_active_positions() >= MAX_CONCURRENT:
            log.info(f"[{signal.symbol}] MAX_CONCURRENT={MAX_CONCURRENT} atteint — attente")
            return False
        if signal.symbol in _active_positions:
            log.info(f"[{signal.symbol}] Position deja ouverte")
            return False

        vol = calc_lot_size(balance, signal.entry, signal.sl)
        if vol <= 0:
            log.warning(f"[{signal.symbol}] Volume invalide : {vol}")
            return False

        # side: 1=BUY Long Open, 3=SELL Short Open
        side = 1 if signal.direction == 'BUY' else 3

        log.info(f"\n{'='*60}")
        log.info(f"  SIGNAL VALIDE — {signal.symbol}")
        log.info(f"  Direction  : {signal.direction}")
        log.info(f"  Entry      : {signal.entry:.4f}")
        log.info(f"  SL         : {signal.sl:.4f}")
        log.info(f"  TP         : {signal.tp:.4f}  (R/R 1:{signal.rr:.2f})")
        log.info(f"  Volume     : {vol} contrats")
        log.info(f"  Levier     : x{LEVERAGE}")
        log.info(f"  TimesFM    : {signal.timesfm_direction} ({signal.timesfm_change_pct:+.2f}%)")
        log.info(f"  Raison     : {signal.reason}")
        log.info(f"{'='*60}\n")

        # Exécution avec SL + TP garantis (4 étapes dans mexc_api)
        result = api.place_order_with_sl_tp(
            symbol     = signal.symbol,
            side       = side,
            vol        = vol,
            sl_price   = signal.sl,
            tp_price   = signal.tp,
            leverage   = LEVERAGE,
        )

        if not result or not result.get('order_id'):
            err = result.get('order_resp', {}).get('message', 'Pas de reponse') if result else 'Erreur'
            log.error(f"[{signal.symbol}] Ordre refuse : {err}")
            return False

        order_id = str(result['order_id'])
        sl_ok    = result.get('sl_set', False)
        tp_ok    = result.get('tp_set', False)

        if not sl_ok or not tp_ok:
            log.warning(f"[{signal.symbol}] ATTENTION : SL={sl_ok} TP={tp_ok} non confirmes !")

        _active_positions[signal.symbol] = {
            'direction': signal.direction,
            'side':      side,
            'entry':     signal.entry,
            'sl':        signal.sl,
            'tp':        signal.tp,
            'vol':       vol,
            'order_id':  order_id,
            'open_time': time.time(),
            'sl_set':    sl_ok,
            'tp_set':    tp_ok,
        }
        _trade_count += 1
        _last_signal_time[signal.symbol] = time.time()

    emoji = "🟢" if signal.direction == 'BUY' else "🔴"
    sl_icon = "✅" if result.get('sl_set') else "⚠️"
    tp_icon = "✅" if result.get('tp_set') else "⚠️"

    sl_str = f"{signal.sl:.4f} USDT" if signal.sl > 0 else "AUCUN (Désactivé)"
    tg_send(
        f"{emoji} <b>TRADE OUVERT — {signal.direction} {signal.symbol}</b>\n"
        f"  Entree  : {signal.entry:.4f} USDT\n"
        f"  {tp_icon} TP     : {signal.tp:.4f} USDT\n"
        f"  🛡️ SL     : {sl_str}\n"
        f"  R/R     : 1:{signal.rr:.2f}\n"
        f"  Levier  : x{LEVERAGE}\n"
        f"  Volume  : {vol} contrats\n"
        f"  TimesFM : {signal.timesfm_direction} ({signal.timesfm_change_pct:+.2f}%)\n"
        f"  Raison  : {signal.reason[:120]}"
    )

    if LOG_TRADES:
        _log_trade(signal, vol, 'OPEN', result)

    return True


# ══════════════════════════════════════════════════════════════════
#  MONITORING POSITIONS
# ══════════════════════════════════════════════════════════════════
def monitor_positions():
    global _daily_pnl, _win_count

    try:
        open_pos  = api.get_open_positions()
        open_syms = {p.get('symbol') for p in open_pos}
    except Exception as e:
        log.debug(f"Monitor positions erreur : {e}")
        return

    with _lock:
        closed = [sym for sym in list(_active_positions.keys()) if sym not in open_syms]

    for sym in closed:
        pos = _active_positions.pop(sym, None)
        if not pos:
            continue

        ticker     = api.get_ticker(sym)
        last_price = ticker.get('last', pos['entry'])
        pnl_pts    = (last_price - pos['entry']) if pos['direction'] == 'BUY' else (pos['entry'] - last_price)
        pnl_usd    = pnl_pts * pos['vol'] * LEVERAGE
        _daily_pnl += pnl_usd
        if pnl_usd > 0:
            _win_count += 1

        duration_min = int((time.time() - pos['open_time']) / 60)
        icon = "✅ GAGNE" if pnl_usd > 0 else "❌ PERDU"

        log.info(f"{icon} [{sym}] {pos['direction']} | PnL: {pnl_usd:+.2f}$ | Duree: {duration_min}min")
        tg_send(
            f"{'✅' if pnl_usd > 0 else '❌'} <b>Trade ferme : {pos['direction']} {sym}</b>\n"
            f"  PnL     : {pnl_usd:+.2f} USDT\n"
            f"  Duree   : {duration_min} min\n"
            f"  PnL Jour: {_daily_pnl:+.2f} USDT\n"
            f"  Bot cherche un nouveau trade..."
        )
        if LOG_TRADES:
            _log_close(sym, pos, pnl_usd, duration_min)

    # Trailing Stop
    if USE_TRAILING_SL:
        _update_trailing_stops()


def _update_trailing_stops():
    with _lock:
        for sym, pos in list(_active_positions.items()):
            try:
                cur = api.get_ticker(sym).get('last', 0)
                if cur <= 0:
                    continue
                entry, sl = pos['entry'], pos['sl']
                if pos['direction'] == 'BUY':
                    if (cur - entry) / entry * 100 >= TRAILING_TRIGGER:
                        new_sl = cur * (1 - TRAILING_STEP / 100)
                        if new_sl > sl:
                            api.set_sl_tp(sym, new_sl, pos['tp'])
                            _active_positions[sym]['sl'] = new_sl
                            log.info(f"[{sym}] Trailing SL -> {new_sl:.4f}")
                else:
                    if (entry - cur) / entry * 100 >= TRAILING_TRIGGER:
                        new_sl = cur * (1 + TRAILING_STEP / 100)
                        if new_sl < sl:
                            api.set_sl_tp(sym, new_sl, pos['tp'])
                            _active_positions[sym]['sl'] = new_sl
                            log.info(f"[{sym}] Trailing SL -> {new_sl:.4f}")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════
#  SCANNER UNE PAIRE
# ══════════════════════════════════════════════════════════════════
def scan_pair(symbol: str, balance: float) -> Optional[Signal]:
    # Cooldown anti-spam sur la même paire
    if time.time() - _last_signal_time.get(symbol, 0) < SIGNAL_COOLDOWN_SEC:
        return None
    if symbol in _active_positions:
        return None

    try:
        df_klines = api.get_klines(symbol, TIMEFRAME, KLINE_LIMIT)
        if df_klines is None or len(df_klines) < 30:
            return None

        strategy = OrderFlowStrategy(symbol)
        signal   = strategy.analyze(df_klines)

        if signal.direction != 'NEUTRAL':
            log.info(
                f"[SIGNAL] {symbol} {signal.direction} | "
                f"Consensus:{signal.consensus_pct:.0f}% | "
                f"TimesFM:{signal.timesfm_direction}({signal.timesfm_change_pct:+.2f}%) | "
                f"4H:{signal.trend_bias} | RR:1:{signal.rr:.1f}"
            )
            if LOG_SIGNALS:
                _log_signal(signal)

        return signal if signal.is_valid() else None

    except Exception as e:
        log.debug(f"[{symbol}] Erreur scan : {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE — SCAN TOUTES LES 60 SECONDES
# ══════════════════════════════════════════════════════════════════
def run():
    global _start_balance

    log.info("=" * 65)
    log.info("  INSTITUTIONAL HUNTER PRO v5.0")
    log.info("  Triple Confirmation : Carnet + TimesFM Google + Tendance 4H")
    log.info(f"  Levier     : x{LEVERAGE}  |  Risque : {RISK_PER_TRADE_PCT}%  |  1 trade max")
    log.info(f"  Exchanges  : MEXC, Bitget, Bybit, OKX, Binance, Kraken")
    log.info(f"  Scan       : toutes les {UPDATE_INTERVAL_SEC}s")
    log.info("=" * 65)

    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"Paires actives au demarrage : {len(active_pairs)}")

    # ── PRE-CHARGEMENT GOOGLE TIMESFM ─────────────────────────────
    # Charger et compiler TimesFM AVANT le premier cycle
    # pour ne pas bloquer la 1ere prediction (compilation JIT)
    log.info("[TimesFM] Pre-chargement en cours (environ 10s)...")
    tfm_ready = preload_model() is not None
    if tfm_ready:
        log.info("[TimesFM] JUGE FINAL PRET — actif pour tous les trades")
    else:
        log.warning("[TimesFM] Non disponible — signal >90% requis sans IA")

    # Balance initiale
    try:
        account        = api.get_account()
        _start_balance = account.get('balance', 0)
        log.info(f"Balance MEXC : {_start_balance:.2f} USDT | Equity: {account.get('equity',0):.2f}")
        tg_send(
            f"IHP v5.0 demarre\n"
            f"Balance : {_start_balance:.2f} USDT\n"
            f"Paires  : {len(active_pairs)} | Levier: x{LEVERAGE}\n"
            f"Scan toutes les {UPDATE_INTERVAL_SEC}s — 1 trade a la fois\n"
            f"TimesFM Google AI : ACTIF"
        )
    except Exception as e:
        log.warning(f"Balance non disponible : {e}")
        _start_balance = 100.0

    cycle      = 0
    last_rescan = time.time()

    while True:
        try:
            cycle += 1
            n_pos  = count_active_positions()
            log.info(f"\n-- Cycle #{cycle} {datetime.now().strftime('%H:%M:%S')} | Positions:{n_pos}/{MAX_CONCURRENT} | Paires:{len(active_pairs)} --")

            # Re-scan des meilleures paires par volume
            if AUTO_SCAN and (time.time() - last_rescan) > AUTO_SCAN_INTERVAL:
                active_pairs = get_active_pairs(AUTO_SCAN)
                last_rescan  = time.time()
                log.info(f"Re-scan volumes : {len(active_pairs)} paires")

            # Balance actuelle
            try:
                balance = api.get_account().get('balance', _start_balance)
            except Exception:
                balance = _start_balance

            # Vérif limites de risque
            if check_risk_limits(balance):
                time.sleep(3600)
                continue

            # Monitoring positions existantes
            monitor_positions()

            # Si 1 trade déjà en cours → on attend sans scanner
            if count_active_positions() >= MAX_CONCURRENT:
                log.info(f"  1 trade actif — surveillance en cours (scan dans {UPDATE_INTERVAL_SEC}s)")
                time.sleep(UPDATE_INTERVAL_SEC)
                continue

            # Scan toutes les paires EN PARALLELE
            log.info(f"  Scan de {len(active_pairs)} paires...")
            signals = []
            with ThreadPoolExecutor(max_workers=15) as executor:
                futures = {executor.submit(scan_pair, sym, balance): sym for sym in active_pairs}
                for fut in as_completed(futures, timeout=180):
                    try:
                        sig = fut.result()
                        if sig:
                            signals.append(sig)
                    except Exception:
                        pass

            # Trier : STRONG > NORMAL, puis consensus %, puis R/R
            signals.sort(key=lambda s: (
                0 if s.strength == 'STRONG' else 1,
                -s.consensus_pct,
                -s.rr
            ))

            if signals:
                log.info(f"  {len(signals)} signal(s) detecte(s) !")
                for sig in signals:
                    if count_active_positions() >= MAX_CONCURRENT:
                        break
                    open_trade(sig, balance)
            else:
                log.info(f"  Aucun signal — marche indecis, prochain scan dans {UPDATE_INTERVAL_SEC}s")

            wr = (_win_count / _trade_count * 100) if _trade_count > 0 else 0
            log.info(f"  Stats: Trades={_trade_count} WR={wr:.0f}% PnL_Jour={_daily_pnl:+.2f}$ Balance={balance:.2f}")

            time.sleep(UPDATE_INTERVAL_SEC)

        except KeyboardInterrupt:
            log.info("Bot arrete par l'utilisateur (Ctrl+C)")
            tg_send("Bot arrete manuellement.")
            break
        except Exception as e:
            log.error(f"Erreur boucle : {e}", exc_info=True)
            time.sleep(20)


# ══════════════════════════════════════════════════════════════════
#  LOGS CSV
# ══════════════════════════════════════════════════════════════════
def _log_trade(signal, vol, action, result=None):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['datetime','symbol','action','direction','entry','sl','tp','rr',
                        'vol','leverage','consensus_pct','timesfm','reason'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            signal.symbol, action, signal.direction,
            signal.entry, signal.sl, signal.tp, signal.rr,
            vol, LEVERAGE, signal.consensus_pct,
            f"{signal.timesfm_direction}({signal.timesfm_change_pct:+.2f}%)",
            signal.reason[:100]
        ])


def _log_close(sym, pos, pnl, duration):
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            sym, 'CLOSE', pos['direction'],
            pos['entry'], pos['sl'], pos['tp'], '',
            pos['vol'], LEVERAGE, '', '',
            f'PnL={pnl:.2f}$ Dur={duration}min'
        ])


def _log_signal(signal):
    exists = os.path.exists(SIGNAL_LOG_FILE)
    with open(SIGNAL_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['datetime','symbol','direction','consensus_pct','timesfm','trend_bias','rr','entry','sl','tp'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            signal.symbol, signal.direction, signal.consensus_pct,
            f"{signal.timesfm_direction}({signal.timesfm_change_pct:+.2f}%)",
            signal.trend_bias, signal.rr, signal.entry, signal.sl, signal.tp
        ])


if __name__ == '__main__':
    run()
