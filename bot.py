"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT PRINCIPAL          ║
║          bot.py — Orchestrateur multi-paires                     ║
║                                                                  ║
║  Lance le scan de toutes les paires configurées en parallèle,   ║
║  gère l'exécution des ordres, le suivi des positions et les     ║
║  alertes Telegram.                                               ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
import csv
import os
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import mexc_api as api
from strategy import LVNStrategy, Signal
from scanner import get_active_pairs, print_market_overview, scan_all_futures
from config import (
    TIMEFRAME, KLINE_LIMIT, UPDATE_INTERVAL_SEC,
    SIGNAL_COOLDOWN_SEC, RISK_PER_TRADE_PCT, MAX_CONCURRENT,
    MAX_DAILY_LOSS_PCT, MAX_DRAWDOWN_PCT, LEVERAGE,
    USE_TRAILING_SL, TRAILING_TRIGGER,
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
#  ÉTAT GLOBAL DU BOT
# ══════════════════════════════════════════════════════════════════
_lock            = Lock()
_active_positions: dict = {}    # symbol → {side, entry, sl, tp, vol, order_id}
_last_signal_time: dict = {}    # symbol → timestamp
_daily_pnl       = 0.0
_start_balance   = 0.0
_trade_count     = 0
_win_count       = 0


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM ALERTES
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
#  GESTION DU RISQUE
# ══════════════════════════════════════════════════════════════════
def calc_lot_size(balance: float, entry: float, sl: float) -> float:
    """
    Calcule la taille de position basée sur le % de risque.
    Risque $ = balance × RISK_PCT
    Vol (contrats) = Risque $ / |entry - sl|
    """
    if entry <= 0 or sl <= 0 or abs(entry - sl) < 1e-10:
        return 0.0
    risk_usd  = balance * (RISK_PER_TRADE_PCT / 100.0)
    risk_per_contract = abs(entry - sl)
    vol = risk_usd / risk_per_contract
    # Adapter au levier
    vol = vol * LEVERAGE
    return round(max(vol, 0.001), 3)


def check_daily_loss(balance: float) -> bool:
    """Retourne True si la limite de perte journalière est atteinte."""
    if _start_balance <= 0:
        return False
    daily_loss_pct = (_start_balance - balance) / _start_balance * 100
    if daily_loss_pct >= MAX_DAILY_LOSS_PCT:
        log.warning(f"🛑 STOP JOURNALIER atteint : -{daily_loss_pct:.1f}%")
        tg_send(f"🛑 <b>STOP JOURNALIER</b>\nPerte : {daily_loss_pct:.1f}% — Trading suspendu")
        return True
    return False


def count_active_positions() -> int:
    """Compte les positions actuellement ouvertes (API + cache)."""
    try:
        open_pos = api.get_open_positions()
        return len(open_pos)
    except Exception:
        return len(_active_positions)


# ══════════════════════════════════════════════════════════════════
#  GESTION DES POSITIONS
# ══════════════════════════════════════════════════════════════════
def open_trade(signal: Signal, balance: float) -> bool:
    """Ouvre un trade sur MEXC selon le signal."""
    global _trade_count

    if not signal.is_valid():
        return False

    with _lock:
        # Vérifier les limites
        if count_active_positions() >= MAX_CONCURRENT:
            log.info(f"[{signal.symbol}] Max positions atteint ({MAX_CONCURRENT})")
            return False
        if signal.symbol in _active_positions:
            log.info(f"[{signal.symbol}] Position déjà ouverte")
            return False

        # Calculer la taille
        vol = calc_lot_size(balance, signal.entry, signal.sl)
        if vol <= 0:
            log.warning(f"[{signal.symbol}] Volume invalide : {vol}")
            return False

        # Définir le sens MEXC
        # side: 1=BUY Long, 3=SELL Short
        side = 1 if signal.direction == 'BUY' else 3

        # Régler le levier
        api.set_leverage(signal.symbol, LEVERAGE)

        # Passer l'ordre Market avec SL/TP
        result = api.place_order(
            symbol     = signal.symbol,
            side       = side,
            vol        = vol,
            order_type = 5,          # Market
            sl_price   = signal.sl,
            tp_price   = signal.tp,
        )

        if not result or result.get('code') != 200:
            err = result.get('message', 'Inconnu') if result else 'Pas de réponse'
            log.error(f"[{signal.symbol}] Ordre refusé : {err}")
            return False

        order_id = str(result.get('data', ''))
        _active_positions[signal.symbol] = {
            'direction': signal.direction,
            'side':      side,
            'entry':     signal.entry,
            'sl':        signal.sl,
            'tp':        signal.tp,
            'vol':       vol,
            'order_id':  order_id,
            'open_time': time.time(),
            'trailing':  False,
        }
        _trade_count += 1
        _last_signal_time[signal.symbol] = time.time()

    log.info(f"✅ [{signal.symbol}] {signal.direction} ouvert | "
             f"Entry:{signal.entry:.4f} SL:{signal.sl:.4f} TP:{signal.tp:.4f} "
             f"RR:1:{signal.rr:.1f} Vol:{vol}")

    # Alerte Telegram avec rétrospective 3 étapes
    tg_send(signal.retrospective())

    if LOG_TRADES:
        _log_trade(signal, vol, 'OPEN')

    return True


def monitor_positions():
    """Surveille les positions ouvertes : trailing stop, fermeture."""
    global _daily_pnl, _win_count

    try:
        open_pos = api.get_open_positions()
        open_syms = {p.get('symbol') for p in open_pos}
    except Exception as e:
        log.debug(f"Monitor positions erreur : {e}")
        return

    with _lock:
        closed = [sym for sym in list(_active_positions.keys())
                  if sym not in open_syms]

    for sym in closed:
        pos = _active_positions.pop(sym, None)
        if not pos:
            continue
        ticker = api.get_ticker(sym)
        last_price = ticker.get('last', 0)
        if pos['direction'] == 'BUY':
            pnl_pts = last_price - pos['entry']
        else:
            pnl_pts = pos['entry'] - last_price
        pnl_usd = pnl_pts * pos['vol']
        _daily_pnl += pnl_usd
        if pnl_usd > 0:
            _win_count += 1

        duration_min = int((time.time() - pos['open_time']) / 60)
        result_emoji = "✅" if pnl_usd > 0 else "❌"
        log.info(f"{result_emoji} [{sym}] {pos['direction']} fermé | "
                 f"PnL: {pnl_usd:+.2f}$ | Durée: {duration_min}min")

        tg_send(
            f"{result_emoji} Trade fermé: <b>{pos['direction']} {sym}</b>\n"
            f"  PnL    : {pnl_usd:+.2f} USDT\n"
            f"  Durée  : {duration_min} min\n"
            f"  PnL Jour: {_daily_pnl:+.2f} USDT"
        )
        if LOG_TRADES:
            _log_close(sym, pos, pnl_usd, duration_min)

    # Trailing Stop
    if USE_TRAILING_SL:
        _update_trailing_stops()


def _update_trailing_stops():
    """Met à jour les trailing stops."""
    with _lock:
        for sym, pos in list(_active_positions.items()):
            try:
                ticker = api.get_ticker(sym)
                cur    = ticker.get('last', 0)
                if cur <= 0:
                    continue

                entry = pos['entry']
                sl    = pos['sl']

                if pos['direction'] == 'BUY':
                    profit_pct = (cur - entry) / entry * 100
                    if profit_pct >= TRAILING_TRIGGER:
                        new_sl = cur - (cur - entry) * 0.4
                        if new_sl > sl:
                            api._set_sl_tp(sym, new_sl)
                            _active_positions[sym]['sl'] = new_sl
                            pos['trailing'] = True
                            log.info(f"[{sym}] Trailing SL → {new_sl:.4f}")
                else:
                    profit_pct = (entry - cur) / entry * 100
                    if profit_pct >= TRAILING_TRIGGER:
                        new_sl = cur + (entry - cur) * 0.4
                        if new_sl < sl:
                            api._set_sl_tp(sym, new_sl)
                            _active_positions[sym]['sl'] = new_sl
                            pos['trailing'] = True
                            log.info(f"[{sym}] Trailing SL → {new_sl:.4f}")
            except Exception:
                pass


# ══════════════════════════════════════════════════════════════════
#  SCAN UNE PAIRE
# ══════════════════════════════════════════════════════════════════
def scan_pair(symbol: str, balance: float) -> Optional[Signal]:
    """Analyse une paire et retourne un signal si valide."""
    # Cooldown entre 2 signaux sur la même paire
    last = _last_signal_time.get(symbol, 0)
    if time.time() - last < SIGNAL_COOLDOWN_SEC:
        return None

    # Déjà en position
    if symbol in _active_positions:
        return None

    try:
        df = api.get_klines(symbol, TIMEFRAME, KLINE_LIMIT)
        if df is None or len(df) < 70:
            return None

        strategy = LVNStrategy(symbol)
        signal   = strategy.analyze(df)

        if signal.direction != 'NEUTRAL':
            log.info(f"\n{signal.summary()}")
            if signal.warnings:
                log.warning(f"  {'|'.join(signal.warnings)}")
            if LOG_SIGNALS:
                _log_signal(signal)

        return signal if signal.is_valid() else None

    except Exception as e:
        log.debug(f"[{symbol}] Erreur scan : {e}")
        return None


# ══════════════════════════════════════════════════════════════════
#  BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════════════════
def run():
    global _start_balance

    log.info("═" * 60)
    log.info("  INSTITUTIONAL HUNTER PRO — MEXC BOT démarré")
    log.info("  Stratégie : 15m LVN ACCELERATION & REJECTION")
    log.info(f"  Mode      : {'AUTO-SCAN TOP ' + str(AUTO_SCAN_TOP_N) + ' par volume' if AUTO_SCAN else 'Paires manuelles'}")
    log.info(f"  Levier    : x{LEVERAGE}  |  Risque/Trade : {RISK_PER_TRADE_PCT}%")
    log.info("═" * 60)

    # Scan initial des paires
    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"📋 {len(active_pairs)} paires actives au démarrage")

    # Balance initiale
    try:
        account = api.get_account()
        _start_balance = account.get('balance', 0)
        log.info(f"💰 Balance MEXC : {_start_balance:.2f} USDT")
        tg_send(
            f"🤖 <b>IHP MEXC Bot démarré — x{LEVERAGE}</b>\n"
            f"Balance : {_start_balance:.2f} USDT\n"
            f"Mode    : {'Auto-scan TOP ' + str(AUTO_SCAN_TOP_N) if AUTO_SCAN else 'Manuel'}\n"
            f"Risque  : {RISK_PER_TRADE_PCT}%/trade | x{LEVERAGE}\n"
            f"Paires  : {len(active_pairs)}"
        )
    except Exception as e:
        log.warning(f"Balance non disponible (sans clé API) : {e}")
        _start_balance = 1000.0

    # Aperçu du marché au démarrage
    print_market_overview()

    cycle        = 0
    last_rescan  = time.time()

    while True:
        try:
            cycle += 1
            log.info(f"\n── Cycle #{cycle} — {datetime.now().strftime('%H:%M:%S')} "
                     f"| {len(active_pairs)} paires | x{LEVERAGE} ──")

            # Re-scanner les paires toutes les AUTO_SCAN_INTERVAL secondes
            if AUTO_SCAN and (time.time() - last_rescan) > AUTO_SCAN_INTERVAL:
                active_pairs = get_active_pairs(AUTO_SCAN)
                last_rescan  = time.time()
                log.info(f"🔄 Re-scan : {len(active_pairs)} paires actives")

            # Rafraîchir le solde
            try:
                account = api.get_account()
                balance = account.get('balance', _start_balance)
            except Exception:
                balance = _start_balance

            # ── Stop journalier ──────────────────────────────────
            if check_daily_loss(balance):
                log.info("Trading suspendu jusqu'à demain.")
                time.sleep(3600)
                continue

            # ── Stop drawdown absolu ─────────────────────────────
            if _start_balance > 0:
                dd = (_start_balance - balance) / _start_balance * 100
                if dd >= MAX_DRAWDOWN_PCT:
                    log.critical(f"🛑 DRAWDOWN MAX {dd:.1f}% — Bot arrêté !")
                    tg_send(f"🛑 DRAWDOWN {dd:.1f}% — Bot arrêté !")
                    break

            # Surveiller les positions existantes
            monitor_positions()

            # Scanner toutes les paires en parallèle
            signals = []
            with ThreadPoolExecutor(max_workers=10) as executor:
                futures_map = {
                    executor.submit(scan_pair, sym, balance): sym
                    for sym in active_pairs
                }
                for fut in as_completed(futures_map, timeout=120):
                    sig = fut.result()
                    if sig:
                        signals.append(sig)

            # Trier : STRONG d'abord, puis par RR décroissant
            signals.sort(key=lambda s: (
                0 if s.strength == 'STRONG' else 1 if s.strength == 'NORMAL' else 2,
                -s.rr
            ))

            # Log des signaux trouvés
            if signals:
                log.info(f"🎯 {len(signals)} signal(s) valide(s) trouvé(s)")
                for s in signals[:5]:
                    log.info(f"   {'🟢' if s.direction=='BUY' else '🔴'} "
                             f"{s.direction} {s.symbol} | RR:1:{s.rr:.1f} | {s.strength}")

            # Exécuter les meilleurs signaux
            for sig in signals:
                if count_active_positions() >= MAX_CONCURRENT:
                    log.info(f"Max {MAX_CONCURRENT} positions atteint")
                    break
                open_trade(sig, balance)

            # Rapport
            wr = (_win_count / _trade_count * 100) if _trade_count > 0 else 0
            log.info(f"📊 Pos:{count_active_positions()}/{MAX_CONCURRENT} | "
                     f"Trades:{_trade_count} | WR:{wr:.0f}% | "
                     f"PnL Jour:{_daily_pnl:+.2f}$ | Balance:{balance:.2f}$")

            time.sleep(UPDATE_INTERVAL_SEC)

        except KeyboardInterrupt:
            log.info("Bot arrêté par l'utilisateur.")
            tg_send("⛔ Bot IHP arrêté manuellement.")
            break
        except Exception as e:
            log.error(f"Erreur boucle principale : {e}", exc_info=True)
            time.sleep(30)


# ══════════════════════════════════════════════════════════════════
#  LOGS CSV
# ══════════════════════════════════════════════════════════════════
def _log_trade(signal: Signal, vol: float, action: str):
    exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['datetime','symbol','action','direction','entry',
                        'sl','tp','rr','vol','fisher','reason'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            signal.symbol, action, signal.direction,
            signal.entry, signal.sl, signal.tp, signal.rr,
            vol, signal.fisher_val, signal.reason
        ])

def _log_close(sym, pos, pnl, duration):
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        csv.writer(f).writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            sym, 'CLOSE', pos['direction'],
            pos['entry'], pos['sl'], pos['tp'], '',
            pos['vol'], '', f'PnL={pnl:.2f}$ Dur={duration}min'
        ])

def _log_signal(signal: Signal):
    exists = os.path.exists(SIGNAL_LOG_FILE)
    with open(SIGNAL_LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(['datetime','symbol','direction','strength',
                        'rr','fisher','entry','sl','tp','reason'])
        w.writerow([
            datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            signal.symbol, signal.direction, signal.strength,
            signal.rr, signal.fisher_val,
            signal.entry, signal.sl, signal.tp, signal.reason
        ])


if __name__ == '__main__':
    run()
