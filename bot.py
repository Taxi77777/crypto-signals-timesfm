"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.1                               ║
║     bot.py — Orchestrateur Principal                            ║
║                                                                  ║
║  CHANGEMENTS v5.1 :                                              ║
║  • Credentials lus via config.py -> variables d'environnement    ║
║  • calc_lot_size() ne depend plus du SL (USE_SL=False)           ║
║  • Taille de position = % fixe du capital en marge x levier      ║
║  • Taille contrat MEXC recuperee via /contract/detail            ║
║  • Etat persiste dans state.json (indispensable en CI)           ║
║  • Trailing stop corrige (fonctionne aussi quand sl == 0)        ║
║  • Alias execute_trade -> open_trade (compatibilite)             ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
import csv
import os
import json
import requests
from datetime import datetime, date
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
    USE_SL, POSITION_MARGIN_PCT, MAX_MARGIN_USDT, STATE_FILE,
    validate_env,
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
_active_positions: dict = {}   # symbol -> dict
_last_signal_time: dict = {}   # symbol -> timestamp
_daily_pnl        = 0.0
_start_balance    = 0.0
_trade_count      = 0
_win_count        = 0
_pnl_day          = ''         # date ISO du _daily_pnl courant


# ══════════════════════════════════════════════════════════════════
#  PERSISTANCE DE L'ÉTAT (state.json)
#
#  En mode GitHub Actions, chaque run est un processus neuf : sans
#  ce fichier, _active_positions repart vide, donc aucune fermeture
#  n'est jamais detectee, aucun cooldown n'est respecte et les
#  limites de perte ne se declenchent jamais.
# ══════════════════════════════════════════════════════════════════
def load_state():
    global _active_positions, _last_signal_time, _daily_pnl
    global _start_balance, _trade_count, _win_count, _pnl_day

    if not os.path.exists(STATE_FILE):
        log.info("[STATE] Aucun etat precedent — demarrage a neuf.")
        _pnl_day = date.today().isoformat()
        return

    try:
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            s = json.load(f)
    except Exception as e:
        log.warning(f"[STATE] Lecture impossible ({e}) — demarrage a neuf.")
        _pnl_day = date.today().isoformat()
        return

    _active_positions = s.get('active_positions', {}) or {}
    _last_signal_time = {k: float(v) for k, v in (s.get('last_signal_time', {}) or {}).items()}
    _trade_count      = int(s.get('trade_count', 0))
    _win_count        = int(s.get('win_count', 0))
    _start_balance    = float(s.get('start_balance', 0.0))
    _pnl_day          = s.get('pnl_day', '') or date.today().isoformat()

    # Le PnL journalier et la balance de reference se reinitialisent chaque jour
    today = date.today().isoformat()
    if _pnl_day != today:
        log.info(f"[STATE] Nouveau jour ({_pnl_day} -> {today}) — reset PnL journalier.")
        _daily_pnl     = 0.0
        _start_balance = 0.0
        _pnl_day       = today
    else:
        _daily_pnl = float(s.get('daily_pnl', 0.0))

    log.info(
        f"[STATE] Charge : {len(_active_positions)} position(s) suivie(s) | "
        f"trades={_trade_count} | PnL jour={_daily_pnl:+.2f}"
    )


def save_state():
    try:
        payload = {
            'active_positions': _active_positions,
            'last_signal_time': _last_signal_time,
            'daily_pnl':        round(_daily_pnl, 4),
            'start_balance':    round(_start_balance, 4),
            'trade_count':      _trade_count,
            'win_count':        _win_count,
            'pnl_day':          _pnl_day or date.today().isoformat(),
            'updated_at':       datetime.now().isoformat(timespec='seconds'),
        }
        with open(STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        log.warning(f"[STATE] Sauvegarde impossible : {e}")


# ══════════════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════════════
def tg_send(msg: str):
    if not TG_ENABLED or not TG_BOT_TOKEN or not TG_CHAT_ID:
        log.debug("[TG] Desactive (token ou chat_id manquant)")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
            json={'chat_id': TG_CHAT_ID, 'text': msg, 'parse_mode': 'HTML'},
            timeout=8
        )
        if r.status_code != 200:
            # On log l'erreur : un token invalide passait avant totalement inapercu.
            log.warning(f"[TG] Echec envoi ({r.status_code}) : {r.text[:180]}")
    except Exception as e:
        log.warning(f"[TG] Exception envoi : {e}")


# ══════════════════════════════════════════════════════════════════
#  TAILLE DE CONTRAT MEXC
# ══════════════════════════════════════════════════════════════════
_contract_cache: dict = {}


def get_contract_size(symbol: str) -> float:
    """
    Recupere contractSize depuis /api/v1/contract/detail.
    Sur MEXC Futures, 1 contrat != 1 unite de crypto : sans cette
    valeur, le volume calcule est faux (parfois d'un facteur 1000).
    Retourne 1.0 en cas d'echec (comportement historique).
    """
    if symbol in _contract_cache:
        return _contract_cache[symbol]

    size = 1.0
    try:
        data = api._get_public("/api/v1/contract/detail", params={'symbol': symbol})
        d = data.get('data') if isinstance(data, dict) else None
        if isinstance(d, list):
            d = next((x for x in d if x.get('symbol') == symbol), None)
        if isinstance(d, dict):
            cs = float(d.get('contractSize', 0) or 0)
            if cs > 0:
                size = cs
    except Exception as e:
        log.debug(f"[CONTRACT] {symbol} taille non recuperee : {e}")

    _contract_cache[symbol] = size
    return size


# ══════════════════════════════════════════════════════════════════
#  DIMENSIONNEMENT DE POSITION
# ══════════════════════════════════════════════════════════════════
def calc_lot_size(symbol: str, balance: float, entry: float, sl: float = 0.0) -> float:
    """
    Calcule le volume en contrats.

    Deux modes :

    1. USE_SL = True  -> dimensionnement classique par le risque.
       Le volume est tel qu'une sortie au SL coute RISK_PER_TRADE_PCT % du capital.

    2. USE_SL = False -> dimensionnement par marge fixe (mode actuel).
       Impossible de dimensionner par le risque sans stop : on engage donc
       une fraction fixe du capital en marge.
         marge      = min(balance x POSITION_MARGIN_PCT %, MAX_MARGIN_USDT)
         notionnel  = marge x LEVERAGE
         volume     = notionnel / (prix x taille_contrat)

    ⚠️ L'ancienne version retournait systematiquement 0.0 des que sl valait 0,
       ce qui empechait TOUTE ouverture de position quand USE_SL=False.
    """
    if entry <= 0 or balance <= 0:
        log.warning(f"[SIZE] {symbol} entree ({entry}) ou balance ({balance}) invalide")
        return 0.0

    if USE_SL:
        if sl <= 0 or abs(entry - sl) < 1e-10:
            log.warning(f"[SIZE] {symbol} USE_SL=True mais SL absent — volume 0")
            return 0.0
        risk_usd = balance * (RISK_PER_TRADE_PCT / 100.0)
        return round(max(risk_usd / abs(entry - sl), 0.001), 3)

    margin = min(balance * (POSITION_MARGIN_PCT / 100.0), MAX_MARGIN_USDT)
    if margin <= 0:
        log.warning(f"[SIZE] {symbol} marge nulle — volume 0")
        return 0.0

    notional      = margin * LEVERAGE
    contract_size = get_contract_size(symbol)
    vol           = notional / (entry * contract_size)

    log.info(
        f"[SIZE] {symbol} balance={balance:.2f} marge={margin:.2f} "
        f"notionnel={notional:.2f} prix={entry:.6f} contractSize={contract_size} "
        f"-> vol={vol:.4f} contrats"
    )
    return round(max(vol, 0.001), 3)


def count_active_positions() -> int:
    try:
        return len(api.get_open_positions())
    except Exception:
        return len(_active_positions)


def check_risk_limits(balance: float) -> bool:
    """Retourne True si le trading doit etre suspendu."""
    if _start_balance <= 0:
        return False
    loss_pct = (_start_balance - balance) / _start_balance * 100
    if loss_pct >= MAX_DRAWDOWN_PCT:
        log.critical(f"DRAWDOWN MAX {loss_pct:.1f}% — Bot arrete !")
        tg_send(f"⛔ DRAWDOWN MAX {loss_pct:.1f}% — Bot arrete !")
        return True
    if loss_pct >= MAX_DAILY_LOSS_PCT:
        log.warning(f"STOP JOURNALIER : -{loss_pct:.1f}%")
        tg_send(f"⛔ STOP JOURNALIER -{loss_pct:.1f}% — Trading suspendu")
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

        vol = calc_lot_size(signal.symbol, balance, signal.entry, signal.sl)
        if vol <= 0:
            log.warning(f"[{signal.symbol}] Volume invalide : {vol}")
            return False

        # side: 1 = BUY Long Open, 3 = SELL Short Open
        side = 1 if signal.direction == 'BUY' else 3

        sl_display = f"{signal.sl:.4f}" if signal.sl > 0 else "AUCUN (desactive)"
        log.info(f"\n{'='*60}")
        log.info(f"  SIGNAL VALIDE — {signal.symbol}")
        log.info(f"  Direction  : {signal.direction}")
        log.info(f"  Entry      : {signal.entry:.4f}")
        log.info(f"  SL         : {sl_display}")
        log.info(f"  TP         : {signal.tp:.4f}  (R/R 1:{signal.rr:.2f})")
        log.info(f"  Volume     : {vol} contrats")
        log.info(f"  Levier     : x{LEVERAGE}")
        log.info(f"  TimesFM    : {signal.timesfm_direction} ({signal.timesfm_change_pct:+.2f}%)")
        log.info(f"  Raison     : {signal.reason}")
        log.info(f"{'='*60}\n")

        result = api.place_order_with_sl_tp(
            symbol   = signal.symbol,
            side     = side,
            vol      = vol,
            sl_price = signal.sl,
            tp_price = signal.tp,
            leverage = LEVERAGE,
        )

        if not result or not result.get('order_id'):
            resp = (result or {}).get('order_resp', {}) or {}
            err  = resp.get('message') or resp.get('msg') or str(resp)[:200] or 'Pas de reponse'
            log.error(f"[{signal.symbol}] Ordre refuse : {err}")
            tg_send(f"⚠️ Ordre refuse sur {signal.symbol} — {str(err)[:150]}")
            return False

        order_id = str(result['order_id'])
        sl_ok    = result.get('sl_set', False)
        tp_ok    = result.get('tp_set', False)

        if not tp_ok:
            log.warning(f"[{signal.symbol}] ATTENTION : TP non confirme !")

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

    emoji   = "🟢" if signal.direction == 'BUY' else "🔴"
    tp_icon = "✅" if result.get('tp_set') else "⚠️"
    sl_str  = f"{signal.sl:.4f} USDT" if signal.sl > 0 else "AUCUN (desactive)"

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

    save_state()
    return True


# Alias de compatibilite — bot_once.py importait execute_trade, qui n'existait pas.
execute_trade = open_trade


# ══════════════════════════════════════════════════════════════════
#  MONITORING POSITIONS
# ══════════════════════════════════════════════════════════════════
def monitor_positions():
    global _daily_pnl, _win_count

    try:
        open_pos  = api.get_open_positions()
        open_syms = {p.get('symbol') for p in open_pos}
    except Exception as e:
        log.warning(f"[MONITOR] Lecture positions impossible : {e}")
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
        contract_size = get_contract_size(sym)
        pnl_usd    = pnl_pts * pos['vol'] * contract_size
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

    if USE_TRAILING_SL:
        _update_trailing_stops()

    if closed:
        save_state()


def _update_trailing_stops():
    """
    Verrouillage de gain. Ne se declenche qu'apres +TRAILING_TRIGGER % de
    profit : il ne peut donc jamais transformer un gain en perte seche.

    Correctif : quand USE_SL=False, pos['sl'] vaut 0. L'ancienne condition
    `new_sl < sl` etait toujours fausse pour les shorts (rien n'est < 0),
    donc le trailing ne fonctionnait jamais en vente.
    """
    with _lock:
        for sym, pos in list(_active_positions.items()):
            try:
                cur = api.get_ticker(sym).get('last', 0)
                if cur <= 0:
                    continue
                entry = pos['entry']
                sl    = pos.get('sl', 0) or 0
                has_sl = sl > 0

                if pos['direction'] == 'BUY':
                    if (cur - entry) / entry * 100 >= TRAILING_TRIGGER:
                        new_sl = cur * (1 - TRAILING_STEP / 100)
                        if not has_sl or new_sl > sl:
                            api.set_sl_tp(sym, new_sl, pos['tp'])
                            _active_positions[sym]['sl'] = new_sl
                            log.info(f"[{sym}] Trailing SL -> {new_sl:.6f} (gain verrouille)")
                else:
                    if (entry - cur) / entry * 100 >= TRAILING_TRIGGER:
                        new_sl = cur * (1 + TRAILING_STEP / 100)
                        if not has_sl or new_sl < sl:
                            api.set_sl_tp(sym, new_sl, pos['tp'])
                            _active_positions[sym]['sl'] = new_sl
                            log.info(f"[{sym}] Trailing SL -> {new_sl:.6f} (gain verrouille)")
            except Exception as e:
                log.debug(f"[{sym}] Trailing erreur : {e}")


# ══════════════════════════════════════════════════════════════════
#  SCAN D'UNE PAIRE
# ══════════════════════════════════════════════════════════════════
def scan_pair(symbol: str, balance: float) -> Optional[Signal]:
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
#  BOUCLE PRINCIPALE (mode local / VPS)
# ══════════════════════════════════════════════════════════════════
def run():
    global _start_balance

    missing = validate_env(require_trading=True)
    if missing:
        log.error(f"Variables d'environnement manquantes : {', '.join(missing)}")
        log.error("Le bot ne peut pas trader sans credentials. Arret.")
        return

    load_state()

    log.info("=" * 65)
    log.info("  INSTITUTIONAL HUNTER PRO v5.1")
    log.info("  Triple Confirmation : Carnet + TimesFM Google + Tendance 4H")
    log.info(f"  Levier     : x{LEVERAGE}  |  SL : {'ACTIF' if USE_SL else 'DESACTIVE'}  |  1 trade max")
    log.info(f"  Sizing     : {POSITION_MARGIN_PCT}% du capital en marge (max {MAX_MARGIN_USDT} USDT)")
    log.info(f"  Exchanges  : MEXC, Bitget, Bybit, OKX, Binance, Kraken")
    log.info(f"  Scan       : toutes les {UPDATE_INTERVAL_SEC}s")
    log.info("=" * 65)

    active_pairs = get_active_pairs(AUTO_SCAN)
    log.info(f"Paires actives au demarrage : {len(active_pairs)}")

    log.info("[TimesFM] Pre-chargement en cours...")
    tfm_ready = preload_model() is not None
    if tfm_ready:
        log.info("[TimesFM] JUGE FINAL PRET — actif pour tous les trades")
    else:
        log.warning("[TimesFM] Non disponible — consensus >=90% requis sans IA")

    try:
        account = api.get_account()
        if _start_balance <= 0:
            _start_balance = account.get('balance', 0)
        log.info(f"Balance MEXC : {account.get('balance', 0):.2f} USDT | Equity: {account.get('equity',0):.2f}")
        tg_send(
            f"IHP v5.1 demarre\n"
            f"Balance : {account.get('balance', 0):.2f} USDT\n"
            f"Paires  : {len(active_pairs)} | Levier: x{LEVERAGE}\n"
            f"SL : {'ACTIF' if USE_SL else 'DESACTIVE'} | Marge/trade : {POSITION_MARGIN_PCT}%\n"
            f"TimesFM : {'ACTIF' if tfm_ready else 'INDISPONIBLE'}"
        )
    except Exception as e:
        log.warning(f"Balance non disponible : {e}")
        if _start_balance <= 0:
            _start_balance = 100.0

    cycle       = 0
    last_rescan = time.time()

    while True:
        try:
            cycle += 1
            n_pos  = count_active_positions()
            log.info(f"\n-- Cycle #{cycle} {datetime.now().strftime('%H:%M:%S')} | Positions:{n_pos}/{MAX_CONCURRENT} | Paires:{len(active_pairs)} --")

            if AUTO_SCAN and (time.time() - last_rescan) > AUTO_SCAN_INTERVAL:
                active_pairs = get_active_pairs(AUTO_SCAN)
                last_rescan  = time.time()
                log.info(f"Re-scan volumes : {len(active_pairs)} paires")

            try:
                balance = api.get_account().get('balance', _start_balance)
            except Exception:
                balance = _start_balance

            if check_risk_limits(balance):
                save_state()
                time.sleep(3600)
                continue

            monitor_positions()

            if count_active_positions() >= MAX_CONCURRENT:
                log.info(f"  1 trade actif — surveillance (scan dans {UPDATE_INTERVAL_SEC}s)")
                save_state()
                time.sleep(UPDATE_INTERVAL_SEC)
                continue

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
                log.info(f"  Aucun signal — prochain scan dans {UPDATE_INTERVAL_SEC}s")

            wr = (_win_count / _trade_count * 100) if _trade_count > 0 else 0
            log.info(f"  Stats: Trades={_trade_count} WR={wr:.0f}% PnL_Jour={_daily_pnl:+.2f}$ Balance={balance:.2f}")
            save_state()

            time.sleep(UPDATE_INTERVAL_SEC)

        except KeyboardInterrupt:
            log.info("Bot arrete par l'utilisateur (Ctrl+C)")
            save_state()
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
