"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC Futures API             ║
║          mexc_api.py — Ordres avec SL + TP intégrés              ║
╚══════════════════════════════════════════════════════════════════╝
"""
import hashlib
import hmac
import time
import logging
import requests
import pandas as pd
from doh_patch import apply_doh_patch
from config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_BASE_URL, MEXC_SPOT_URL, LEVERAGE

apply_doh_patch()
log = logging.getLogger("IHP-MEXC")

adapter = requests.adapters.HTTPAdapter(pool_connections=30, pool_maxsize=30)
_session = requests.Session()
_session.mount('https://', adapter)
_session.mount('http://', adapter)
_session.headers.update({'Content-Type': 'application/json', 'User-Agent': 'IHP-Bot/5.0'})


# ──────────────────────────────────────────────────────────────
#  SIGNATURE & REQUÊTES PRIVÉES MEXC FUTURES v1
# ──────────────────────────────────────────────────────────────

def _sign_headers() -> dict:
    ts  = str(int(time.time() * 1000))
    sig = hmac.new(
        MEXC_SECRET_KEY.encode('utf-8'),
        (MEXC_API_KEY + ts).encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    return {'ApiKey': MEXC_API_KEY, 'Request-Time': ts, 'Signature': sig, 'Content-Type': 'application/json'}


def _get_public(endpoint: str, params: dict = None, futures: bool = True) -> dict:
    base = MEXC_BASE_URL if futures else MEXC_SPOT_URL
    try:
        r = _session.get(base + endpoint, params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"[GET PUBLIC] {endpoint}: {e}")
        return {}


def _get_private(endpoint: str, params: dict = None) -> dict:
    try:
        r = _session.get(MEXC_BASE_URL + endpoint, params=params or {},
                         headers=_sign_headers(), timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"[GET PRIVATE] {endpoint}: {e}")
        return {}


def _post_private(endpoint: str, body: dict) -> dict:
    if not MEXC_API_KEY or not MEXC_SECRET_KEY:
        log.warning("[POST] Clés API manquantes — ordre non envoyé")
        return {}
    try:
        r = _session.post(MEXC_BASE_URL + endpoint, json=body,
                          headers=_sign_headers(), timeout=10)
        resp = r.json()
        if resp.get('code') != 200 and resp.get('success') is not True:
            log.error(f"[POST] {endpoint} → Erreur API: {resp}")
        return resp
    except Exception as e:
        log.error(f"[POST] {endpoint}: {e}")
        return {}


# ──────────────────────────────────────────────────────────────
#  DONNÉES DE MARCHÉ
# ──────────────────────────────────────────────────────────────

def get_klines(symbol: str, interval: str = "4h", limit: int = 200) -> pd.DataFrame:
    """Bougies OHLCV via MEXC Spot (api.mexc.com) — DNS stable."""
    # Spot API fonctionne toujours (pas de problème DNS)
    interval_map = {
        '1m':'1m','3m':'3m','5m':'5m','15m':'15m','30m':'30m',
        '1h':'1h','2h':'2h','4h':'4h','6h':'6h','8h':'8h','12h':'12h',
        '1d':'1d','3d':'3d','1w':'1w','1M':'1M'
    }
    iv = interval_map.get(interval, '4h')
    # Symbole format spot : BTC_USDT -> BTCUSDT
    spot_symbol = symbol.replace('_', '')
    try:
        r = _session.get(
            MEXC_SPOT_URL + "/api/v3/klines",
            params={'symbol': spot_symbol, 'interval': iv, 'limit': limit},
            timeout=8
        )
        r.raise_for_status()
        raw = r.json()
        if not raw or not isinstance(raw, list):
            return pd.DataFrame()
        # MEXC Spot renvoie [open_time, open, high, low, close, volume, close_time, quote_vol]
        cols_available = len(raw[0]) if raw else 0
        base_cols = ['open_time','open','high','low','close','volume','close_time','quote_vol']
        cols = base_cols[:cols_available]
        df = pd.DataFrame(raw, columns=cols)
        for col in ['open','high','low','close','volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        df['open_time'] = pd.to_numeric(df['open_time'], errors='coerce')
        return df[['open_time','open','high','low','close','volume']].dropna().reset_index(drop=True)
    except Exception as e:
        log.error(f"[Klines Spot] {symbol} {interval}: {e}")
        return pd.DataFrame()



def get_ticker(symbol: str) -> dict:
    data = _get_public("/api/v1/contract/ticker", params={'symbol': symbol})
    if not data or 'data' not in data:
        return {}
    d = data['data']
    if isinstance(d, list):
        d = next((x for x in d if x.get('symbol') == symbol), {})
    try:
        return {
            'symbol':    symbol,
            'last':      float(d.get('lastPrice', 0)),
            'bid':       float(d.get('bid1', 0)),
            'ask':       float(d.get('ask1', 0)),
            'volume24':  float(d.get('amount24', d.get('volume24', 0))),
            'change_pct':float(d.get('riseFallRate', 0)),
        }
    except Exception:
        return {}


def get_all_tickers() -> list:
    data = _get_public("/api/v1/contract/ticker")
    return data.get('data', []) if data else []


# ──────────────────────────────────────────────────────────────
#  COMPTE & POSITIONS
# ──────────────────────────────────────────────────────────────

def get_account() -> dict:
    data = _get_private("/api/v1/private/account/assets")
    if not data or 'data' not in data:
        return {}
    usdt = next((a for a in data['data'] if a.get('currency') == 'USDT'), {})
    return {
        'balance':        float(usdt.get('availableBalance', 0)),
        'equity':         float(usdt.get('equity', 0)),
        'unrealized_pnl': float(usdt.get('unrealisedPnl', 0)),
    }


def get_open_positions() -> list:
    data = _get_private("/api/v1/private/position/open_positions")
    return data.get('data', []) or []


def get_open_orders(symbol: str) -> list:
    data = _get_private("/api/v1/private/order/list/open_orders/" + symbol)
    if not data:
        return []
    d = data.get('data', {})
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get('resultList', []) or []
    return []


# ──────────────────────────────────────────────────────────────
#  LEVIER
# ──────────────────────────────────────────────────────────────

def set_leverage(symbol: str, leverage: int = None) -> dict:
    """Définit le levier pour un symbole."""
    lev = leverage or LEVERAGE
    body = {'symbol': symbol, 'leverage': lev, 'openType': 1}
    result = _post_private("/api/v1/private/position/change_leverage", body)
    log.info(f"[LEVIER] {symbol} → x{lev} | Réponse: {result}")
    return result


# ──────────────────────────────────────────────────────────────
#  PLACE ORDRE AVEC SL + TP GARANTIS
# ──────────────────────────────────────────────────────────────

def place_order_with_sl_tp(
    symbol:     str,
    side:       int,      # 1=BUY_OPEN, 2=SELL_OPEN, 3=BUY_CLOSE, 4=SELL_CLOSE
    vol:        float,    # Volume en contrats
    sl_price:   float,    # Stop Loss — OBLIGATOIRE
    tp_price:   float,    # Take Profit — OBLIGATOIRE
    leverage:   int = None,
    order_type: int = 5,  # 5 = Market Order
) -> dict:
    """
    Place un ordre marché sur MEXC Futures avec SL et TP intégrés.

    ÉTAPE 1 : Définir le levier
    ÉTAPE 2 : Placer l'ordre marché
    ÉTAPE 3 : Attacher immédiatement SL + TP via change_sl_tp
    ÉTAPE 4 : Vérifier que la position est ouverte avec SL/TP actifs

    Returns: dict avec order_id, sl confirmé, tp confirmé
    """
    lev = leverage or LEVERAGE
    result = {'order_id': None, 'sl': sl_price, 'tp': tp_price, 'sl_set': False, 'tp_set': False}

    # ── ÉTAPE 1 : Levier ────────────────────────────────────────
    set_leverage(symbol, lev)
    time.sleep(0.3)

    # ── ÉTAPE 2 : Ordre Marché ───────────────────────────────────
    body = {
        'symbol':   symbol,
        'side':     side,
        'vol':      round(vol, 4),
        'type':     order_type,    # 5 = Market
        'openType': 1,             # 1 = Isolated Margin
        'leverage': lev,
    }
    log.info(f"[ORDER] {symbol} | Side={side} | Vol={vol} | Levier=x{lev} | SL={sl_price} | TP={tp_price}")
    order_resp = _post_private("/api/v1/private/order/submit", body)

    order_id = order_resp.get('data')
    result['order_id']   = order_id
    result['order_resp'] = order_resp

    if not order_id:
        log.error(f"[ORDER] Échec placement ordre : {order_resp}")
        return result

    log.info(f"[ORDER] ✅ Ordre placé | ID: {order_id}")
    time.sleep(0.8)  # Attendre que la position soit créée

    # ── ÉTAPE 3 : Attacher SL + TP ──────────────────────────────
    sl_tp_body = {'symbol': symbol}
    if sl_price and sl_price > 0:
        sl_tp_body['stopLossPrice'] = round(sl_price, 6)
    if tp_price and tp_price > 0:
        sl_tp_body['takeProfitPrice'] = round(tp_price, 6)

    sl_tp_resp = _post_private("/api/v1/private/position/change_sl_tp", sl_tp_body)

    if sl_tp_resp.get('code') == 200 or sl_tp_resp.get('success') is True:
        result['sl_set'] = True if (sl_price and sl_price > 0) else False
        result['tp_set'] = True if (tp_price and tp_price > 0) else False
        log.info(f"[SL/TP] ✅ TP={tp_price:.6f} | SL={'SANS' if not sl_price else f'{sl_price:.6f}'} — Confirmé sur MEXC")
    else:
        # Retry une fois si échec
        log.warning(f"[SL/TP] Retry SL/TP... Réponse: {sl_tp_resp}")
        time.sleep(1.0)
        retry_resp = _post_private("/api/v1/private/position/change_sl_tp", sl_tp_body)
        if retry_resp.get('code') == 200 or retry_resp.get('success') is True:
            result['sl_set'] = True
            result['tp_set'] = True
            log.info(f"[SL/TP] ✅ SL/TP confirmés après retry")
        else:
            log.error(f"[SL/TP] ❌ SL/TP non confirmés : {retry_resp}")

    # ── ÉTAPE 4 : Vérification position ouverte ──────────────────
    time.sleep(1.0)
    positions = get_open_positions()
    pos = next((p for p in positions if p.get('symbol') == symbol), None)
    if pos:
        sl_live = float(pos.get('stopLossPrice', 0))
        tp_live = float(pos.get('takeProfitPrice', 0))
        result['position_confirmed'] = True
        result['sl_live'] = sl_live
        result['tp_live'] = tp_live
        log.info(f"[POSITION] ✅ {symbol} ouverte | SL live={sl_live} | TP live={tp_live} | Vol={pos.get('vol')}")
    else:
        result['position_confirmed'] = False
        log.warning(f"[POSITION] Position {symbol} non visible (peut prendre quelques secondes)")

    return result


def close_position(symbol: str, side: int, vol: float) -> dict:
    """Ferme une position existante au marché."""
    # side: 3=close long (sell to close), 4=close short (buy to close)
    body = {
        'symbol':   symbol,
        'side':     side,
        'vol':      round(vol, 4),
        'type':     5,    # Market
        'openType': 1,
    }
    result = _post_private("/api/v1/private/order/submit", body)
    log.info(f"[CLOSE] {symbol} side={side} vol={vol} → {result}")
    return result


def set_sl_tp(symbol: str, sl_price: float, tp_price: float = None) -> dict:
    """Met à jour SL et TP d'une position existante."""
    body = {'symbol': symbol, 'stopLossPrice': round(sl_price, 6)}
    if tp_price:
        body['takeProfitPrice'] = round(tp_price, 6)
    return _post_private("/api/v1/private/position/change_sl_tp", body)
