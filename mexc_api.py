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
from config import MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_BASE_URL, MEXC_SPOT_URL

# ══════════════════════════════════════════════════════════════════
#  SESSION HTTP — Réutilisation des connexions pour la rapidité
# ══════════════════════════════════════════════════════════════════
_session = requests.Session()
_session.headers.update({
    'Content-Type': 'application/json',
    'User-Agent':   'IHP-MEXC-Bot/2.0',
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
    """Requête publique (pas d'authentification)."""
    base = MEXC_BASE_URL if futures else MEXC_SPOT_URL
    try:
        r = _session.get(base + endpoint, params=params, timeout=8)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        print(f"[API] GET {endpoint} erreur : {e}")
        return {}


def _get_private(endpoint: str, params: dict = None, futures: bool = True) -> dict:
    """Requête privée avec signature MEXC."""
    if not MEXC_API_KEY or not MEXC_SECRET_KEY:
        print("[API] ⚠️  Clés API manquantes — mode lecture seule")
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
    """POST signé pour créer/gérer des ordres."""
    if not MEXC_API_KEY or not MEXC_SECRET_KEY:
        print("[API] ⚠️  Clés API manquantes — ordre non envoyé")
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
#  DONNÉES DE MARCHÉ — PUBLIQUES
# ══════════════════════════════════════════════════════════════════

def get_klines(symbol: str, interval: str = "Min15", limit: int = 200) -> pd.DataFrame:
    """
    Récupère les bougies OHLCV depuis MEXC Futures.
    Endpoint : GET /api/v1/contract/kline/{symbol}
    Retourne un DataFrame pandas.
    """
    data = _get_public(
        f"/api/v1/contract/kline/{symbol}",
        params={'interval': interval, 'limit': limit}
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
    """
    Ticker temps réel : lastPrice, bid, ask, volume 24h.
    Endpoint : GET /api/v1/contract/ticker?symbol={symbol}
    """
    data = _get_public("/api/v1/contract/ticker", params={'symbol': symbol})
    if not data or 'data' not in data:
        return {}
    d = data['data']
    if isinstance(d, list):
        # Si plusieurs symbols retournés
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
            'volume24':    float(d.get('volume24',   0)),
            'change_pct':  float(d.get('riseFallRate', 0)),
            'funding_rate':float(d.get('fundingRate', 0)),
        }
    except Exception as e:
        print(f"[API] Ticker parse erreur ({symbol}): {e}")
        return {}


def get_order_book(symbol: str, limit: int = 20) -> dict:
    """
    Order book : meilleur bid/ask + murs d'ordres.
    Endpoint : GET /api/v1/contract/depth/{symbol}
    """
    data = _get_public(f"/api/v1/contract/depth/{symbol}", params={'limit': limit})
    if not data or 'data' not in data:
        return {}
    d = data['data']
    bids = [(float(b[0]), float(b[1])) for b in d.get('bids', [])[:limit]]
    asks = [(float(a[0]), float(a[1])) for a in d.get('asks', [])[:limit]]

    total_bid = sum(q for _, q in bids)
    total_ask = sum(q for _, q in asks)
    imbalance = total_bid / total_ask if total_ask > 0 else 1.0

    bid_wall = max(bids, key=lambda x: x[1])[0] if bids else 0
    ask_wall = min(asks, key=lambda x: x[1])[0] if asks else 0

    return {
        'best_bid':  bids[0][0] if bids else 0,
        'best_ask':  asks[0][0] if asks else 0,
        'bid_wall':  bid_wall,
        'ask_wall':  ask_wall,
        'imbalance': imbalance,   # >1.2 = pression achat, <0.8 = pression vente
    }


def get_all_tickers() -> list:
    """Tous les tickers MEXC Futures pour scanner les volumes."""
    data = _get_public("/api/v1/contract/ticker")
    if not data or 'data' not in data:
        return []
    return data['data'] if isinstance(data['data'], list) else []


# ══════════════════════════════════════════════════════════════════
#  COMPTE — AUTHENTIFIÉ
# ══════════════════════════════════════════════════════════════════

def get_account() -> dict:
    """Solde et infos du compte MEXC Futures."""
    data = _get_private("/api/v1/private/account/assets")
    if not data or 'data' not in data:
        return {}
    assets = data['data']
    usdt   = next((a for a in assets if a.get('currency') == 'USDT'), {})
    return {
        'balance':        float(usdt.get('availableBalance', 0)),
        'equity':         float(usdt.get('equity', 0)),
        'unrealized_pnl': float(usdt.get('unrealisedPnl', 0)),
        'margin_used':    float(usdt.get('positionMargin', 0)),
    }


def get_open_positions() -> list:
    """Positions ouvertes sur tous les symboles."""
    data = _get_private("/api/v1/private/position/open_positions")
    if not data or 'data' not in data:
        return []
    return data['data'] or []


def get_open_orders(symbol: str = None) -> list:
    """Ordres ouverts (optionnellement filtrés par symbole)."""
    params = {}
    if symbol:
        params['symbol'] = symbol
    data = _get_private("/api/v1/private/order/list/open_orders", params)
    if not data or 'data' not in data:
        return []
    return data['data'].get('resultList', [])


# ══════════════════════════════════════════════════════════════════
#  ORDRES — AUTHENTIFIÉ
# ══════════════════════════════════════════════════════════════════

def place_order(symbol: str, side: int, vol: float,
                order_type: int = 5,
                price: float = None,
                open_type: int = 1,
                leverage: int = 5,
                sl_price: float = None,
                tp_price: float = None) -> dict:
    """
    Crée un ordre MEXC Futures.

    Paramètres :
        symbol      : "BTC_USDT"
        side        : 1=BUY Long, 2=CLOSE Long, 3=SELL Short, 4=CLOSE Short
        vol         : Quantité (contrats)
        order_type  : 1=Limit, 5=Market
        price       : Prix limite (None pour Market)
        open_type   : 1=Isolated, 2=Cross margin
        leverage    : Levier (1-125)
        sl_price    : Stop Loss automatique
        tp_price    : Take Profit automatique
    """
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

    # Si l'ordre principal réussit, placer SL/TP
    if result and sl_price:
        _set_sl_tp(symbol, sl_price, tp_price)

    return result


def _set_sl_tp(symbol: str, sl_price: float, tp_price: float = None):
    """Définit le Stop Loss et Take Profit sur une position."""
    body = {'symbol': symbol, 'stopLossPrice': sl_price}
    if tp_price:
        body['takeProfitPrice'] = tp_price
    _post_private("/api/v1/private/position/change_sl_tp", body)


def close_position(symbol: str, side: int, vol: float) -> dict:
    """Ferme une position (side 2=close long, 4=close short)."""
    return place_order(symbol=symbol, side=side, vol=vol, order_type=5)


def cancel_order(order_id: str) -> dict:
    """Annule un ordre ouvert."""
    return _post_private("/api/v1/private/order/cancel",
                         {'orderId': order_id})


def set_leverage(symbol: str, leverage: int, open_type: int = 1) -> dict:
    """Définit le levier pour un symbole."""
    return _post_private("/api/v1/private/position/change_margin",
                         {'symbol': symbol, 'leverage': leverage,
                          'openType': open_type})
