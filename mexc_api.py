"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT                    ║
║          mexc_api.py — Wrapper API MEXC Futures v1              ║
╚══════════════════════════════════════════════════════════════════╝
"""
import hashlib
import hmac
import time
import requests
import pandas as pd
from doh_patch import apply_doh_patch
from config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_BASE_URL, MEXC_SPOT_URL

# Appliquer le patch DoH
apply_doh_patch()

_session = requests.Session()
_session.headers.update({
    'Content-Type': 'application/json',
    'User-Agent':   'IHP-MEXC-Bot/3.0',
})


def _timestamp() -> int:
    return int(time.time() * 1000)


def _sign(params: dict) -> str:
    """Signature HMAC-SHA256 pour les requêtes authentifiées MEXC."""
    query = '&'.join(f"{k}={v}" for k, v in sorted(params.items()))
    return hmac.new(
        MEXC_SECRET_KEY.encode('utf-8'),
        query.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


def _get_public(endpoint: str, params: dict = None, futures: bool = True) -> dict:
    base = MEXC_BASE_URL if futures else MEXC_SPOT_URL
    try:
        r = _session.get(base + endpoint, params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[API] GET {endpoint} erreur : {e}")
        return {}


def _get_private(endpoint: str, params: dict = None, futures: bool = True) -> dict:
    if not MEXC_API_KEY or not MEXC_SECRET_KEY:
        print("[API] ⚠️ Clés API manquantes — mode simulation")
        return {}
    base = MEXC_BASE_URL if futures else MEXC_SPOT_URL
    p = params or {}
    p['timestamp'] = _timestamp()
    p['signature'] = _sign(p)
    headers = {'Apikey': MEXC_API_KEY}
    try:
        r = _session.get(base + endpoint, params=p, headers=headers, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[API] GET privé {endpoint} erreur : {e}")
        return {}


def _post_private(endpoint: str, body: dict, futures: bool = True) -> dict:
    if not MEXC_API_KEY or not MEXC_SECRET_KEY:
        print("[API] ⚠️ Clés API manquantes — ordre non envoyé")
        return {}
    base = MEXC_BASE_URL if futures else MEXC_SPOT_URL
    body['timestamp'] = _timestamp()
    body['signature'] = _sign(body)
    headers = {'Apikey': MEXC_API_KEY, 'Content-Type': 'application/json'}
    try:
        r = _session.post(base + endpoint, json=body, headers=headers, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[API] POST {endpoint} erreur : {e}")
        return {}


# ══════════════════════════════════════════════════════════════════
#  DONNÉES DE MARCHÉ
# ══════════════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str = "60m", limit: int = 200) -> pd.DataFrame:
    """Récupère les bougies OHLCV depuis MEXC Futures (1h par défaut)."""
    # Conversion format intervalle
    tf = "Min60" if interval in ("1h", "60m") else ("Min15" if interval == "15m" else "Min240")
    data = _get_public(
        f"/api/v1/contract/kline/{symbol}",
        params={'interval': tf, 'limit': limit}
    )
    if not data or 'data' not in data:
        return pd.DataFrame()

    d = data['data']
    try:
        df = pd.DataFrame({
            'open_time': d.get('time',   []),
            'open':      [float(x) for x in d.get('open',   [])],
            'high':      [float(x) for x in d.get('high',   [])],
            'low':       [float(x) for x in d.get('low',    [])],
            'close':     [float(x) for x in d.get('close',  [])],
            'volume':    [float(x) for x in d.get('vol',    [])],
        })
        df['open_time'] = pd.to_numeric(df['open_time'], errors='coerce') * 1000
        df = df.dropna().sort_values('open_time').reset_index(drop=True)
        return df
    except Exception as e:
        print(f"[API] Klines parse erreur ({symbol}): {e}")
        return pd.DataFrame()


def get_ticker(symbol: str) -> dict:
    data = _get_public("/api/v1/contract/ticker", params={'symbol': symbol})
    if not data or 'data' not in data:
        return {}
    d = data['data']
    if isinstance(d, list):
        for item in d:
            if item.get('symbol') == symbol:
                d = item
                break
    try:
        return {
            'symbol':      symbol,
            'last':        float(d.get('lastPrice', 0)),
            'bid':         float(d.get('bid1',      0)),
            'ask':         float(d.get('ask1',      0)),
            'high24':      float(d.get('high24Price', 0)),
            'low24':       float(d.get('lower24Price', 0)),
            'volume24':    float(d.get('amount24', d.get('volume24', 0))),
            'change_pct':  float(d.get('riseFallRate', 0)),
        }
    except Exception as e:
        return {}


def get_all_tickers() -> list:
    data = _get_public("/api/v1/contract/ticker")
    if not data or 'data' not in data:
        return []
    return data['data'] if isinstance(data['data'], list) else []


# ══════════════════════════════════════════════════════════════════
#  COMPTE & ORDRES
# ══════════════════════════════════════════════════════════════════

def get_account() -> dict:
    data = _get_private("/api/v1/private/account/assets")
    if not data or 'data' not in data:
        return {}
    assets = data['data']
    usdt   = next((a for a in assets if a.get('currency') == 'USDT'), {})
    return {
        'balance':        float(usdt.get('availableBalance', 0)),
        'equity':         float(usdt.get('equity', 0)),
        'unrealized_pnl': float(usdt.get('unrealisedPnl', 0)),
    }


def get_open_positions() -> list:
    data = _get_private("/api/v1/private/position/open_positions")
    if not data or 'data' not in data:
        return []
    return data['data'] or []


def place_order(symbol: str, side: int, vol: float,
                order_type: int = 5,
                price: float = None,
                open_type: int = 1,
                leverage: int = 10,
                sl_price: float = None,
                tp_price: float = None) -> dict:
    body = {
        'symbol':    symbol,
        'side':      side,
        'vol':       round(vol, 4),
        'type':      order_type,
        'openType':  open_type,
        'leverage':  leverage,
    }
    if price and order_type == 1:
        body['price'] = price

    result = _post_private("/api/v1/private/order/submit", body)

    if result and sl_price:
        _set_sl_tp(symbol, sl_price, tp_price)

    return result


def _set_sl_tp(symbol: str, sl_price: float, tp_price: float = None):
    body = {'symbol': symbol, 'stopLossPrice': sl_price}
    if tp_price:
        body['takeProfitPrice'] = tp_price
    _post_private("/api/v1/private/position/change_sl_tp", body)


def set_leverage(symbol: str, leverage: int, open_type: int = 1) -> dict:
    return _post_private("/api/v1/private/position/change_margin",
                         {'symbol': symbol, 'leverage': leverage,
                          'openType': open_type})
