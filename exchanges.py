"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MULTI-EXCHANGE DATA ENGINE        ║
║     exchanges.py — Carnet d'ordres & OBI multi-échange           ║
║                                                                  ║
║  Interroge en parallèle 6 grands échanges mondiaux :             ║
║  MEXC, Bitget, Bybit, OKX, Binance, Kraken                       ║
╚══════════════════════════════════════════════════════════════════╝
"""
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from doh_patch import apply_doh_patch

# Appliquer le patch DoH
apply_doh_patch()

log = logging.getLogger('IHP-EXCHANGES')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}


def clean_symbol(symbol: str) -> str:
    """Transforme 'BTC_USDT' ou 'BTCUSDT' en symbole de base ('BTC')."""
    sym = symbol.upper().replace('_', '').replace('-', '')
    if sym.endswith('USDT'):
        return sym[:-4]
    return sym


# ──────────────────────────────────────────────────────────────
#  FONCTIONS DE RÉCUPÉRATION DU CARNET D'ORDRES (0 API KEY REQUISE)
# ──────────────────────────────────────────────────────────────

def fetch_mexc_depth(base: str, depth: int = 20) -> dict:
    url = f"https://api.mexc.com/api/v3/depth?symbol={base}USDT&limit={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    bids = sum(float(b[1]) for b in r.get('bids', []))
    asks = sum(float(a[1]) for a in r.get('asks', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'MEXC', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}

def fetch_bitget_depth(base: str, depth: int = 20) -> dict:
    url = f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={base}USDT&limit={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    data = r.get('data', {})
    bids = sum(float(b[1]) for b in data.get('bids', []))
    asks = sum(float(a[1]) for a in data.get('asks', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'Bitget', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}

def fetch_bybit_depth(base: str, depth: int = 20) -> dict:
    url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={base}USDT&limit={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    res = r.get('result', {})
    bids = sum(float(b[1]) for b in res.get('b', []))
    asks = sum(float(a[1]) for a in res.get('a', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'Bybit', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}

def fetch_okx_depth(base: str, depth: int = 20) -> dict:
    url = f"https://www.okx.com/api/v5/market/books?instId={base}-USDT-SWAP&sz={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    data = r.get('data', [{}])[0]
    bids = sum(float(b[1]) for b in data.get('bids', []))
    asks = sum(float(a[1]) for a in data.get('asks', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'OKX', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}

def fetch_binance_depth(base: str, depth: int = 20) -> dict:
    url = f"https://data-api.binance.vision/api/v3/depth?symbol={base}USDT&limit={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    bids = sum(float(b[1]) for b in r.get('bids', []))
    asks = sum(float(a[1]) for a in r.get('asks', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'Binance', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}

def fetch_kraken_depth(base: str, depth: int = 20) -> dict:
    pair = "XBTUSDT" if base == "BTC" else f"{base}USDT"
    url = f"https://api.kraken.com/0/public/Depth?pair={pair}&count={depth}"
    r = requests.get(url, headers=HEADERS, timeout=4).json()
    res = r.get('result', {})
    pair_data = list(res.values())[0] if res else {}
    bids = sum(float(b[1]) for b in pair_data.get('bids', []))
    asks = sum(float(a[1]) for a in pair_data.get('asks', []))
    obi = bids / (bids + asks) if (bids + asks) > 0 else 0.5
    return {'exchange': 'Kraken', 'obi': obi, 'bids': bids, 'asks': asks, 'ok': True}


EXCHANGE_FETCHERS = {
    'MEXC': fetch_mexc_depth,
    'Bitget': fetch_bitget_depth,
    'Bybit': fetch_bybit_depth,
    'OKX': fetch_okx_depth,
    'Binance': fetch_binance_depth,
    'Kraken': fetch_kraken_depth,
}

# ──────────────────────────────────────────────────────────────
#  MOTEUR MULTI-EXCHANGE CONSENSUS
# ──────────────────────────────────────────────────────────────

def get_multi_exchange_obi(symbol: str, target_exchanges: list = None, depth: int = 20) -> dict:
    """
    Interroge les échanges cibles en parallèle et calcule le consensus OBI.

    Returns:
        dict: {
            'symbol': str,
            'base': str,
            'consensus_direction': 'BUY' | 'SELL' | 'NEUTRAL',
            'consensus_pct': float (0 à 100),
            'avg_obi': float (0 à 1),
            'exchanges_ok': int,
            'exchanges_buy': int,
            'exchanges_sell': int,
            'details': dict
        }
    """
    base = clean_symbol(symbol)
    exchanges = target_exchanges or list(EXCHANGE_FETCHERS.keys())
    
    details = {}
    futures_map = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        for ex in exchanges:
            fetcher = EXCHANGE_FETCHERS.get(ex)
            if fetcher:
                futures_map[executor.submit(fetcher, base, depth)] = ex

        for fut in as_completed(futures_map, timeout=6):
            ex_name = futures_map[fut]
            try:
                res = fut.result()
                details[ex_name] = res
            except Exception as e:
                details[ex_name] = {'exchange': ex_name, 'obi': 0.5, 'ok': False, 'error': str(e)}

    # Calcul des métriques de consensus
    valid_exchanges = [d for d in details.values() if d.get('ok')]
    if not valid_exchanges:
        return {
            'symbol': symbol, 'base': base,
            'consensus_direction': 'NEUTRAL', 'consensus_pct': 0.0,
            'avg_obi': 0.5, 'exchanges_ok': 0, 'exchanges_buy': 0, 'exchanges_sell': 0,
            'details': details
        }

    total_ok = len(valid_exchanges)
    total_obi = sum(d['obi'] for d in valid_exchanges)
    avg_obi = total_obi / total_ok

    # Nombre d'échanges acheteurs (>0.55) et vendeurs (<0.45)
    buy_count  = sum(1 for d in valid_exchanges if d['obi'] >= 0.55)
    sell_count = sum(1 for d in valid_exchanges if d['obi'] <= 0.45)

    pct_buy  = (buy_count / total_ok) * 100.0
    pct_sell = (sell_count / total_ok) * 100.0

    consensus_direction = 'NEUTRAL'
    consensus_pct = 0.0

    if pct_buy >= 60.0 and avg_obi >= 0.55:
        consensus_direction = 'BUY'
        consensus_pct = pct_buy
    elif pct_sell >= 60.0 and avg_obi <= 0.45:
        consensus_direction = 'SELL'
        consensus_pct = pct_sell

    return {
        'symbol': symbol,
        'base': base,
        'consensus_direction': consensus_direction,
        'consensus_pct': round(consensus_pct, 1),
        'avg_obi': round(avg_obi, 3),
        'exchanges_ok': total_ok,
        'exchanges_buy': buy_count,
        'exchanges_sell': sell_count,
        'details': details
    }
