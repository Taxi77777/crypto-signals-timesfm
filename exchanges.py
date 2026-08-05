"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v4.0 — ORDER FLOW ENGINE           ║
║     exchanges.py — Analyse Order Flow Multi-Exchange Profonde    ║
║                                                                  ║
║  STRATÉGIE RÉELLE :                                              ║
║  1. Carnet d'ordres niveau par niveau (50 niveaux)               ║
║  2. Détection de déséquilibre de PRIX par niveau                 ║
║     → Bid/Ask ratio > 3.0 par niveau = zone d'absorption         ║
║  3. Stacked Imbalances : blocs consécutifs acheteurs/vendeurs    ║
║  4. CVD (Cumulative Volume Delta) via le flux de trades récents  ║
║  5. Consensus cross-exchange sur 6 bourses mondiales             ║
╚══════════════════════════════════════════════════════════════════╝
"""
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from doh_patch import apply_doh_patch

apply_doh_patch()

log = logging.getLogger('IHP-EXCHANGES')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}

# ──────────────────────────────────────────────────────────────
#  OUTILS D'ANALYSE DE FLUX D'ORDRES
# ──────────────────────────────────────────────────────────────

def analyze_orderbook_levels(bids: list, asks: list, imbalance_threshold: float = 3.0) -> dict:
    """
    Analyse le carnet d'ordres niveau par niveau.
    
    Détecte :
    - Les zones d'absorption : niveaux où bid_vol/ask_vol > seuil (acheteurs dominent)
    - Les stacked imbalances : blocs de niveaux consécutifs dominés par un seul côté
    - L'OBI global (somme totale)
    - Le score directionnel : positif = acheteurs, négatif = vendeurs
    
    Args:
        bids: Liste de [prix, volume] triée DESC (meilleur bid en premier)
        asks: Liste de [prix, volume] triée ASC (meilleur ask en premier)
        imbalance_threshold: Ratio minimum pour considérer un niveau "déséquilibré"
    
    Returns dict avec score, stacked_buy, stacked_sell, obi, dominant_side
    """
    if not bids or not asks:
        return _neutral_result()

    total_bid = sum(float(b[1]) for b in bids)
    total_ask = sum(float(a[1]) for a in asks)
    obi = total_bid / (total_bid + total_ask) if (total_bid + total_ask) > 0 else 0.5

    # Aligner les niveaux par index pour comparaison niveau par niveau
    n_levels = min(len(bids), len(asks))
    
    # Score par niveau : positif = acheteur domine ce niveau, négatif = vendeur
    level_scores = []
    buyer_dominated = 0   # niveaux où acheteur domine clairement
    seller_dominated = 0  # niveaux où vendeur domine clairement
    
    for i in range(n_levels):
        bid_vol = float(bids[i][1])
        ask_vol = float(asks[i][1])
        
        if ask_vol > 0 and bid_vol / ask_vol >= imbalance_threshold:
            level_scores.append(1)    # acheteur domine ce niveau
            buyer_dominated += 1
        elif bid_vol > 0 and ask_vol / bid_vol >= imbalance_threshold:
            level_scores.append(-1)   # vendeur domine ce niveau
            seller_dominated += 1
        else:
            level_scores.append(0)    # équilibré
    
    # Détecter les "stacked imbalances" = blocs consécutifs de même côté (institutionnel)
    stacked_buy  = _count_max_consecutive(level_scores, 1)
    stacked_sell = _count_max_consecutive(level_scores, -1)
    
    # Score directionnel global (pondéré par les volumes)
    total_buy_pressure  = sum(float(b[1]) for i, b in enumerate(bids[:n_levels])  if i < len(level_scores) and level_scores[i] == 1)
    total_sell_pressure = sum(float(a[1]) for i, a in enumerate(asks[:n_levels]) if i < len(level_scores) and level_scores[i] == -1)
    
    # Direction dominante
    dominant_side = 'NEUTRAL'
    if stacked_buy >= 3 and buyer_dominated > seller_dominated:
        dominant_side = 'BUY'
    elif stacked_sell >= 3 and seller_dominated > buyer_dominated:
        dominant_side = 'SELL'
    
    # Score composite (-100 à +100)
    total_dominated = buyer_dominated + seller_dominated
    if total_dominated > 0:
        direction_score = int(((buyer_dominated - seller_dominated) / n_levels) * 100)
    else:
        direction_score = 0

    return {
        'obi':             round(obi, 4),
        'n_levels':        n_levels,
        'buyer_dominated': buyer_dominated,
        'seller_dominated':seller_dominated,
        'stacked_buy':     stacked_buy,
        'stacked_sell':    stacked_sell,
        'direction_score': direction_score,  # -100 (pure sell) à +100 (pure buy)
        'dominant_side':   dominant_side,
        'total_bid_vol':   round(total_bid, 2),
        'total_ask_vol':   round(total_ask, 2),
    }


def _count_max_consecutive(sequence: list, target: int) -> int:
    """Compte le maximum de valeurs consécutives égales à target dans la liste."""
    max_streak = 0
    streak = 0
    for v in sequence:
        if v == target:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _neutral_result() -> dict:
    return {
        'obi': 0.5, 'n_levels': 0, 'buyer_dominated': 0, 'seller_dominated': 0,
        'stacked_buy': 0, 'stacked_sell': 0, 'direction_score': 0,
        'dominant_side': 'NEUTRAL', 'total_bid_vol': 0, 'total_ask_vol': 0
    }


def analyze_trade_flow(trades: list) -> dict:
    """
    Analyse le flux de trades récents pour calculer le CVD.
    
    CVD (Cumulative Volume Delta) = volume des achats agressifs - volume des ventes agressives.
    Un achat agressif = un acheteur frappe l'ask (market buy).
    Une vente agressive = un vendeur frappe le bid (market sell).
    
    Args:
        trades: Liste de trades [{'side':'buy'|'sell', 'vol':float, 'price':float}]
    
    Returns:
        dict avec cvd, buy_vol, sell_vol, delta_pct, cvd_direction
    """
    if not trades:
        return {'cvd': 0, 'buy_vol': 0, 'sell_vol': 0, 'delta_pct': 0, 'cvd_direction': 'NEUTRAL'}
    
    buy_vol  = sum(float(t.get('vol', t.get('qty', 0))) for t in trades if t.get('side', '').lower() in ('buy', 'b', '1', 1))
    sell_vol = sum(float(t.get('vol', t.get('qty', 0))) for t in trades if t.get('side', '').lower() in ('sell', 's', '2', 2))
    
    cvd = buy_vol - sell_vol
    total = buy_vol + sell_vol
    delta_pct = (cvd / total * 100) if total > 0 else 0
    
    cvd_direction = 'BUY' if delta_pct > 20 else ('SELL' if delta_pct < -20 else 'NEUTRAL')
    
    return {
        'cvd':           round(cvd, 4),
        'buy_vol':       round(buy_vol, 4),
        'sell_vol':      round(sell_vol, 4),
        'delta_pct':     round(delta_pct, 2),
        'cvd_direction': cvd_direction
    }


def clean_symbol(symbol: str) -> str:
    sym = symbol.upper().replace('_', '').replace('-', '')
    return sym[:-4] if sym.endswith('USDT') else sym


# ──────────────────────────────────────────────────────────────
#  FONCTIONS DE RÉCUPÉRATION PAR ÉCHANGE (50 NIVEAUX)
# ──────────────────────────────────────────────────────────────

def fetch_mexc_orderflow(base: str, depth: int = 50) -> dict:
    """MEXC : Carnet + trades récents"""
    depth_url  = f"https://api.mexc.com/api/v3/depth?symbol={base}USDT&limit={depth}"
    trades_url = f"https://api.mexc.com/api/v3/trades?symbol={base}USDT&limit=50"
    
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    bids = [[float(b[0]), float(b[1])] for b in r.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in r.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)
    
    # Trades récents
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [{'side': 'buy' if not t.get('isBuyerMaker') else 'sell', 'vol': float(t.get('qty', 0))} for t in (trades_raw if isinstance(trades_raw, list) else [])]
    cvd = analyze_trade_flow(trades)
    
    return {'exchange': 'MEXC', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_bitget_orderflow(base: str, depth: int = 50) -> dict:
    depth_url  = f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    data = r.get('data', {})
    bids = [[float(b[0]), float(b[1])] for b in data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.bitget.com/api/v2/spot/market/fills?symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades_list = trades_raw.get('data', [])
    trades = [{'side': t.get('side', '').lower(), 'vol': float(t.get('baseVolume', t.get('size', 0)))} for t in trades_list]
    cvd = analyze_trade_flow(trades)

    return {'exchange': 'Bitget', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_bybit_orderflow(base: str, depth: int = 50) -> dict:
    depth_url  = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    res = r.get('result', {})
    bids = [[float(b[0]), float(b[1])] for b in res.get('b', [])]
    asks = [[float(a[0]), float(a[1])] for a in res.get('a', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades_list = trades_raw.get('result', {}).get('list', [])
    trades = [{'side': t.get('side', '').lower(), 'vol': float(t.get('size', 0))} for t in trades_list]
    cvd = analyze_trade_flow(trades)

    return {'exchange': 'Bybit', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_okx_orderflow(base: str, depth: int = 50) -> dict:
    depth_url  = f"https://www.okx.com/api/v5/market/books?instId={base}-USDT-SWAP&sz={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    data = r.get('data', [{}])[0]
    bids = [[float(b[0]), float(b[1])] for b in data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://www.okx.com/api/v5/market/trades?instId={base}-USDT-SWAP&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades_list = trades_raw.get('data', [])
    trades = [{'side': t.get('side', '').lower(), 'vol': float(t.get('sz', 0))} for t in trades_list]
    cvd = analyze_trade_flow(trades)

    return {'exchange': 'OKX', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_binance_orderflow(base: str, depth: int = 50) -> dict:
    depth_url  = f"https://data-api.binance.vision/api/v3/depth?symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    bids = [[float(b[0]), float(b[1])] for b in r.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in r.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://data-api.binance.vision/api/v3/trades?symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [{'side': 'buy' if not t.get('isBuyerMaker') else 'sell', 'vol': float(t.get('qty', 0))} for t in (trades_raw if isinstance(trades_raw, list) else [])]
    cvd = analyze_trade_flow(trades)

    return {'exchange': 'Binance', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_kraken_orderflow(base: str, depth: int = 50) -> dict:
    pair = "XBTUSDT" if base == "BTC" else f"{base}USDT"
    depth_url  = f"https://api.kraken.com/0/public/Depth?pair={pair}&count={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    res = r.get('result', {})
    pair_data = list(res.values())[0] if res else {}
    bids = [[float(b[0]), float(b[1])] for b in pair_data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in pair_data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.kraken.com/0/public/Trades?pair={pair}&count=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades_res = trades_raw.get('result', {})
    trades_list = list(trades_res.values())[0] if trades_res else []
    # Kraken trade format: [price, volume, time, side('b'/'s'), type, misc, id]
    trades = [{'side': 'buy' if t[3] == 'b' else 'sell', 'vol': float(t[1])} for t in trades_list if isinstance(t, list) and len(t) >= 4]
    cvd = analyze_trade_flow(trades)

    return {'exchange': 'Kraken', 'ok': True, **book_analysis, 'cvd': cvd}


EXCHANGE_FETCHERS = {
    'MEXC':    fetch_mexc_orderflow,
    'Bitget':  fetch_bitget_orderflow,
    'Bybit':   fetch_bybit_orderflow,
    'OKX':     fetch_okx_orderflow,
    'Binance': fetch_binance_orderflow,
    'Kraken':  fetch_kraken_orderflow,
}


# ──────────────────────────────────────────────────────────────
#  MOTEUR MULTI-EXCHANGE ORDER FLOW CONSENSUS
# ──────────────────────────────────────────────────────────────

def get_multi_exchange_orderflow(symbol: str, target_exchanges: list = None, depth: int = 50) -> dict:
    """
    Analyse le flux d'ordres en parallèle sur tous les exchanges cibles.
    
    Calcule le CONSENSUS basé sur :
    1. Dominant side (BUY/SELL) par carnet d'ordres niveau par niveau
    2. CVD direction (flux de trades récents)
    3. Stacked imbalances (blocs institutionnels)
    
    Returns dict complet avec consensus, scores, et détails par exchange.
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

        for fut in as_completed(futures_map, timeout=25):
            ex_name = futures_map[fut]
            try:
                details[ex_name] = fut.result()
            except Exception as e:
                details[ex_name] = {
                    'exchange': ex_name, 'ok': False, 'error': str(e),
                    'dominant_side': 'NEUTRAL', 'stacked_buy': 0, 'stacked_sell': 0,
                    'direction_score': 0, 'cvd': {'cvd_direction': 'NEUTRAL', 'delta_pct': 0}
                }

    valid = [d for d in details.values() if d.get('ok')]
    if not valid:
        return {
            'symbol': symbol, 'base': base, 'exchanges_ok': 0,
            'consensus_direction': 'NEUTRAL', 'consensus_pct': 0.0,
            'avg_direction_score': 0, 'avg_stacked_buy': 0, 'avg_stacked_sell': 0,
            'book_buy': 0, 'book_sell': 0, 'cvd_buy': 0, 'cvd_sell': 0,
            'details': details
        }

    total_ok = len(valid)

    # Comptage : combien d'échanges disent BUY ou SELL via le carnet
    book_buy  = sum(1 for d in valid if d.get('dominant_side') == 'BUY')
    book_sell = sum(1 for d in valid if d.get('dominant_side') == 'SELL')

    # Comptage : combien d'échanges disent BUY ou SELL via le CVD (flux de trades)
    cvd_buy  = sum(1 for d in valid if d.get('cvd', {}).get('cvd_direction') == 'BUY')
    cvd_sell = sum(1 for d in valid if d.get('cvd', {}).get('cvd_direction') == 'SELL')

    # Double confirmation : carnet + CVD doivent s'accorder
    double_buy  = sum(1 for d in valid if d.get('dominant_side') == 'BUY'  and d.get('cvd', {}).get('cvd_direction') == 'BUY')
    double_sell = sum(1 for d in valid if d.get('dominant_side') == 'SELL' and d.get('cvd', {}).get('cvd_direction') == 'SELL')

    avg_score       = sum(d.get('direction_score', 0) for d in valid) / total_ok
    avg_stacked_buy  = sum(d.get('stacked_buy', 0) for d in valid) / total_ok
    avg_stacked_sell = sum(d.get('stacked_sell', 0) for d in valid) / total_ok

    # CONSENSUS FINAL
    pct_book_buy  = book_buy  / total_ok * 100
    pct_book_sell = book_sell / total_ok * 100
    pct_cvd_buy   = cvd_buy   / total_ok * 100
    pct_cvd_sell  = cvd_sell  / total_ok * 100

    consensus_direction = 'NEUTRAL'
    consensus_pct       = 0.0

    # Critère d'entrée : >= 60% du carnet ET >= 50% du CVD dans le même sens
    if pct_book_buy >= 60.0 and pct_cvd_buy >= 40.0 and avg_score >= 20:
        consensus_direction = 'BUY'
        consensus_pct       = (pct_book_buy + pct_cvd_buy) / 2.0
    elif pct_book_sell >= 60.0 and pct_cvd_sell >= 40.0 and avg_score <= -20:
        consensus_direction = 'SELL'
        consensus_pct       = (pct_book_sell + pct_cvd_sell) / 2.0

    return {
        'symbol':              symbol,
        'base':                base,
        'exchanges_ok':        total_ok,
        'consensus_direction': consensus_direction,
        'consensus_pct':       round(consensus_pct, 1),
        'avg_direction_score': round(avg_score, 1),
        'avg_stacked_buy':     round(avg_stacked_buy, 1),
        'avg_stacked_sell':    round(avg_stacked_sell, 1),
        'book_buy':            book_buy,
        'book_sell':           book_sell,
        'cvd_buy':             cvd_buy,
        'cvd_sell':            cvd_sell,
        'double_confirm_buy':  double_buy,
        'double_confirm_sell': double_sell,
        'details':             details
    }
