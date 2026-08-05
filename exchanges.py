"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.1 — ORDER FLOW ENGINE           ║
║     exchanges.py — Desequilibre PRIX + VOLUME multi-exchange     ║
║                                                                  ║
║  CHANGEMENT MAJEUR v5.1 :                                        ║
║                                                                  ║
║  L'ancienne version ne comparait que les QUANTITES (bid_vol      ║
║  contre ask_vol). Le prix de chaque niveau etait recupere puis   ║
║  jete. Consequence : 1 BTC pose a 3 % du prix moyen pesait       ║
║  autant que 1 BTC colle au marche. Ce n'etait pas un             ║
║  desequilibre prix+volume, mais un desequilibre de volume seul.  ║
║                                                                  ║
║  Desormais :                                                     ║
║   1. NOTIONNEL — chaque niveau vaut prix x volume. C'est de      ║
║      l'argent reel engage, pas un nombre d'unites.               ║
║   2. PONDERATION PAR LA DISTANCE — un ordre colle au mid compte  ║
║      plein pot ; un ordre lointain est fortement decote. Les     ║
║      gros murs poses loin sont le plus souvent decoratifs et     ║
║      retires avant d'etre touches.                               ║
║   3. Le ratio de desequilibre et le nombre de niveaux empiles    ║
║      viennent maintenant de config.py (ils etaient codes en dur  ║
║      et les valeurs du fichier de config n'etaient jamais lues). ║
╚══════════════════════════════════════════════════════════════════╝
"""
import requests
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from doh_patch import apply_doh_patch

try:
    from config import IMBALANCE_RATIO, MIN_STACKED_LEVELS, DISTANCE_DECAY
except Exception:      # garde-fou si config est incomplet
    IMBALANCE_RATIO   = 3.0
    MIN_STACKED_LEVELS = 3
    DISTANCE_DECAY     = 250.0

apply_doh_patch()

log = logging.getLogger('IHP-EXCHANGES')
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}


# ──────────────────────────────────────────────────────────────
#  ANALYSE DU CARNET — PRIX x VOLUME, PONDERE PAR LA DISTANCE
# ──────────────────────────────────────────────────────────────

def _distance_weight(price: float, mid: float) -> float:
    """
    Poids d'un niveau selon son eloignement du prix moyen.

    poids = 1 / (1 + DISTANCE_DECAY x distance_relative)

    Avec DISTANCE_DECAY = 250 :
       colle au mid  (0.00 %) -> 1.00
       a 0.10 %               -> 0.80
       a 0.50 %               -> 0.44
       a 1.00 %               -> 0.29
       a 3.00 %               -> 0.12

    Un mur de liquidite pose loin du marche ne vaut donc presque
    rien : il est rarement touche, et souvent retire avant.
    """
    if mid <= 0:
        return 1.0
    dist = abs(price - mid) / mid
    return 1.0 / (1.0 + DISTANCE_DECAY * dist)


def analyze_orderbook_levels(bids: list, asks: list,
                             imbalance_threshold: float = None) -> dict:
    """
    Analyse le carnet niveau par niveau en PRIX x VOLUME.

    Args:
        bids: [[prix, volume], ...] tries DESC (meilleur bid en premier)
        asks: [[prix, volume], ...] tries ASC  (meilleur ask en premier)
        imbalance_threshold: ratio mini pour marquer un niveau desequilibre
                             (defaut : IMBALANCE_RATIO de config.py)

    Returns dict : obi, obi_qty, direction_score, stacked_buy/sell,
                   dominant_side, notionnels et spread.
    """
    if not bids or not asks:
        return _neutral_result()

    threshold = imbalance_threshold if imbalance_threshold is not None else IMBALANCE_RATIO

    best_bid = float(bids[0][0])
    best_ask = float(asks[0][0])
    mid      = (best_bid + best_ask) / 2.0
    spread_pct = ((best_ask - best_bid) / mid * 100.0) if mid > 0 else 0.0

    # ── Notionnel pondere par la distance ────────────────────────
    bid_vals, ask_vals = [], []
    total_bid_qty = total_ask_qty = 0.0

    for p, v in ((float(b[0]), float(b[1])) for b in bids):
        total_bid_qty += v
        bid_vals.append(p * v * _distance_weight(p, mid))

    for p, v in ((float(a[0]), float(a[1])) for a in asks):
        total_ask_qty += v
        ask_vals.append(p * v * _distance_weight(p, mid))

    total_bid_notional = sum(bid_vals)
    total_ask_notional = sum(ask_vals)
    denom = total_bid_notional + total_ask_notional
    obi   = (total_bid_notional / denom) if denom > 0 else 0.5

    # OBI historique en quantite pure — conserve pour comparaison
    denom_qty = total_bid_qty + total_ask_qty
    obi_qty   = (total_bid_qty / denom_qty) if denom_qty > 0 else 0.5

    # ── Comparaison niveau par niveau, en valeur ─────────────────
    n_levels = min(len(bid_vals), len(ask_vals))
    level_scores = []
    buyer_dominated = seller_dominated = 0

    for i in range(n_levels):
        b_val = bid_vals[i]
        a_val = ask_vals[i]

        if a_val > 0 and b_val / a_val >= threshold:
            level_scores.append(1)
            buyer_dominated += 1
        elif b_val > 0 and a_val / b_val >= threshold:
            level_scores.append(-1)
            seller_dominated += 1
        else:
            level_scores.append(0)

    stacked_buy  = _count_max_consecutive(level_scores, 1)
    stacked_sell = _count_max_consecutive(level_scores, -1)

    # ── Direction dominante ──────────────────────────────────────
    dominant_side = 'NEUTRAL'
    if stacked_buy >= MIN_STACKED_LEVELS and buyer_dominated > seller_dominated:
        dominant_side = 'BUY'
    elif stacked_sell >= MIN_STACKED_LEVELS and seller_dominated > buyer_dominated:
        dominant_side = 'SELL'

    # ── Score composite (-100 a +100) ────────────────────────────
    # Moyenne du desequilibre de niveaux et du desequilibre notionnel,
    # pour que le score reflete a la fois la structure et la valeur.
    level_score    = ((buyer_dominated - seller_dominated) / n_levels * 100.0) if n_levels else 0.0
    notional_score = (obi - 0.5) * 200.0
    direction_score = int(round((level_score + notional_score) / 2.0))

    return {
        'obi':               round(obi, 4),
        'obi_qty':           round(obi_qty, 4),
        'n_levels':          n_levels,
        'buyer_dominated':   buyer_dominated,
        'seller_dominated':  seller_dominated,
        'stacked_buy':       stacked_buy,
        'stacked_sell':      stacked_sell,
        'direction_score':   direction_score,
        'dominant_side':     dominant_side,
        'total_bid_vol':     round(total_bid_qty, 2),
        'total_ask_vol':     round(total_ask_qty, 2),
        'bid_notional':      round(total_bid_notional, 2),
        'ask_notional':      round(total_ask_notional, 2),
        'mid_price':         round(mid, 8),
        'spread_pct':        round(spread_pct, 5),
    }


def _count_max_consecutive(sequence: list, target: int) -> int:
    """Nombre maximum de valeurs consecutives egales a target."""
    max_streak = streak = 0
    for v in sequence:
        if v == target:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return max_streak


def _neutral_result() -> dict:
    return {
        'obi': 0.5, 'obi_qty': 0.5, 'n_levels': 0,
        'buyer_dominated': 0, 'seller_dominated': 0,
        'stacked_buy': 0, 'stacked_sell': 0, 'direction_score': 0,
        'dominant_side': 'NEUTRAL', 'total_bid_vol': 0, 'total_ask_vol': 0,
        'bid_notional': 0, 'ask_notional': 0, 'mid_price': 0, 'spread_pct': 0,
    }


# ──────────────────────────────────────────────────────────────
#  FLUX DE TRADES — CVD EN VALEUR
# ──────────────────────────────────────────────────────────────

def analyze_trade_flow(trades: list) -> dict:
    """
    CVD (Cumulative Volume Delta) : agressifs acheteurs - agressifs vendeurs.

    v5.1 : pondere par le PRIX quand il est disponible. Un achat
    agressif de 10 000 USDT et un achat de 10 USDT ne portent pas
    la meme information ; l'ancienne version les comptait pareil
    des lors qu'ils portaient sur la meme quantite.
    """
    if not trades:
        return {'cvd': 0, 'buy_vol': 0, 'sell_vol': 0, 'delta_pct': 0, 'cvd_direction': 'NEUTRAL'}

    buy_val = sell_val = 0.0
    for t in trades:
        try:
            qty   = float(t.get('vol', t.get('qty', 0)) or 0)
            price = float(t.get('price', 0) or 0)
            val   = qty * price if price > 0 else qty
            side  = str(t.get('side', '')).lower()
            if side in ('buy', 'b', '1'):
                buy_val += val
            elif side in ('sell', 's', '2'):
                sell_val += val
        except (TypeError, ValueError):
            continue

    cvd   = buy_val - sell_val
    total = buy_val + sell_val
    delta_pct = (cvd / total * 100.0) if total > 0 else 0.0
    cvd_direction = 'BUY' if delta_pct > 20 else ('SELL' if delta_pct < -20 else 'NEUTRAL')

    return {
        'cvd':           round(cvd, 4),
        'buy_vol':       round(buy_val, 4),
        'sell_vol':      round(sell_val, 4),
        'delta_pct':     round(delta_pct, 2),
        'cvd_direction': cvd_direction,
    }


def clean_symbol(symbol: str) -> str:
    sym = symbol.upper().replace('_', '').replace('-', '')
    return sym[:-4] if sym.endswith('USDT') else sym


# ──────────────────────────────────────────────────────────────
#  RECUPERATION PAR EXCHANGE
# ──────────────────────────────────────────────────────────────

def fetch_mexc_orderflow(base: str, depth: int = 50) -> dict:
    depth_url  = f"https://api.mexc.com/api/v3/depth?symbol={base}USDT&limit={depth}"
    trades_url = f"https://api.mexc.com/api/v3/trades?symbol={base}USDT&limit=50"

    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    bids = [[float(b[0]), float(b[1])] for b in r.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in r.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [
        {'side': 'buy' if not t.get('isBuyerMaker') else 'sell',
         'vol': float(t.get('qty', 0)), 'price': float(t.get('price', 0) or 0)}
        for t in (trades_raw if isinstance(trades_raw, list) else [])
    ]
    cvd = analyze_trade_flow(trades)
    return {'exchange': 'MEXC', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_bitget_orderflow(base: str, depth: int = 50) -> dict:
    depth_url = f"https://api.bitget.com/api/v2/spot/market/orderbook?symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    data = r.get('data', {})
    bids = [[float(b[0]), float(b[1])] for b in data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.bitget.com/api/v2/spot/market/fills?symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [
        {'side': str(t.get('side', '')).lower(),
         'vol': float(t.get('baseVolume', t.get('size', 0)) or 0),
         'price': float(t.get('price', 0) or 0)}
        for t in trades_raw.get('data', []) or []
    ]
    cvd = analyze_trade_flow(trades)
    return {'exchange': 'Bitget', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_bybit_orderflow(base: str, depth: int = 50) -> dict:
    depth_url = f"https://api.bybit.com/v5/market/orderbook?category=linear&symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    res = r.get('result', {})
    bids = [[float(b[0]), float(b[1])] for b in res.get('b', [])]
    asks = [[float(a[0]), float(a[1])] for a in res.get('a', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [
        {'side': str(t.get('side', '')).lower(),
         'vol': float(t.get('size', 0) or 0),
         'price': float(t.get('price', 0) or 0)}
        for t in trades_raw.get('result', {}).get('list', []) or []
    ]
    cvd = analyze_trade_flow(trades)
    return {'exchange': 'Bybit', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_okx_orderflow(base: str, depth: int = 50) -> dict:
    depth_url = f"https://www.okx.com/api/v5/market/books?instId={base}-USDT-SWAP&sz={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    rows = r.get('data') or []
    data = rows[0] if rows else {}
    bids = [[float(b[0]), float(b[1])] for b in data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://www.okx.com/api/v5/market/trades?instId={base}-USDT-SWAP&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [
        {'side': str(t.get('side', '')).lower(),
         'vol': float(t.get('sz', 0) or 0),
         'price': float(t.get('px', 0) or 0)}
        for t in trades_raw.get('data', []) or []
    ]
    cvd = analyze_trade_flow(trades)
    return {'exchange': 'OKX', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_binance_orderflow(base: str, depth: int = 50) -> dict:
    depth_url = f"https://data-api.binance.vision/api/v3/depth?symbol={base}USDT&limit={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    bids = [[float(b[0]), float(b[1])] for b in r.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in r.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://data-api.binance.vision/api/v3/trades?symbol={base}USDT&limit=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades = [
        {'side': 'buy' if not t.get('isBuyerMaker') else 'sell',
         'vol': float(t.get('qty', 0) or 0),
         'price': float(t.get('price', 0) or 0)}
        for t in (trades_raw if isinstance(trades_raw, list) else [])
    ]
    cvd = analyze_trade_flow(trades)
    return {'exchange': 'Binance', 'ok': True, **book_analysis, 'cvd': cvd}


def fetch_kraken_orderflow(base: str, depth: int = 50) -> dict:
    pair = "XBTUSDT" if base == "BTC" else f"{base}USDT"
    depth_url = f"https://api.kraken.com/0/public/Depth?pair={pair}&count={depth}"
    r = requests.get(depth_url, headers=HEADERS, timeout=4).json()
    res = r.get('result') or {}
    if not res:
        raise ValueError(f"Kraken : paire {pair} inconnue")
    pair_data = list(res.values())[0]
    bids = [[float(b[0]), float(b[1])] for b in pair_data.get('bids', [])]
    asks = [[float(a[0]), float(a[1])] for a in pair_data.get('asks', [])]
    book_analysis = analyze_orderbook_levels(bids, asks)

    trades_url = f"https://api.kraken.com/0/public/Trades?pair={pair}&count=50"
    trades_raw = requests.get(trades_url, headers=HEADERS, timeout=4).json()
    trades_res = trades_raw.get('result') or {}
    trades_list = list(trades_res.values())[0] if trades_res else []
    # Format Kraken : [prix, volume, temps, cote('b'/'s'), type, misc, id]
    trades = [
        {'side': 'buy' if t[3] == 'b' else 'sell',
         'vol': float(t[1]), 'price': float(t[0])}
        for t in trades_list if isinstance(t, list) and len(t) >= 4
    ]
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
#  CONSENSUS MULTI-EXCHANGE
# ──────────────────────────────────────────────────────────────

def get_multi_exchange_orderflow(symbol: str, target_exchanges: list = None,
                                 depth: int = 50) -> dict:
    """
    Interroge en parallele tous les exchanges cibles et calcule le consensus
    sur le desequilibre PRIX x VOLUME du carnet et sur le CVD.
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
                    'exchange': ex_name, 'ok': False, 'error': str(e)[:120],
                    'dominant_side': 'NEUTRAL', 'stacked_buy': 0, 'stacked_sell': 0,
                    'direction_score': 0, 'cvd': {'cvd_direction': 'NEUTRAL', 'delta_pct': 0},
                }

    valid = [d for d in details.values() if d.get('ok')]
    if not valid:
        return {
            'symbol': symbol, 'base': base, 'exchanges_ok': 0,
            'consensus_direction': 'NEUTRAL', 'consensus_pct': 0.0,
            'avg_direction_score': 0, 'avg_stacked_buy': 0, 'avg_stacked_sell': 0,
            'book_buy': 0, 'book_sell': 0, 'cvd_buy': 0, 'cvd_sell': 0,
            'avg_obi': 0.5, 'details': details,
        }

    total_ok = len(valid)

    book_buy  = sum(1 for d in valid if d.get('dominant_side') == 'BUY')
    book_sell = sum(1 for d in valid if d.get('dominant_side') == 'SELL')
    cvd_buy   = sum(1 for d in valid if d.get('cvd', {}).get('cvd_direction') == 'BUY')
    cvd_sell  = sum(1 for d in valid if d.get('cvd', {}).get('cvd_direction') == 'SELL')

    double_buy  = sum(1 for d in valid
                      if d.get('dominant_side') == 'BUY'
                      and d.get('cvd', {}).get('cvd_direction') == 'BUY')
    double_sell = sum(1 for d in valid
                      if d.get('dominant_side') == 'SELL'
                      and d.get('cvd', {}).get('cvd_direction') == 'SELL')

    avg_score        = sum(d.get('direction_score', 0) for d in valid) / total_ok
    avg_stacked_buy  = sum(d.get('stacked_buy', 0) for d in valid) / total_ok
    avg_stacked_sell = sum(d.get('stacked_sell', 0) for d in valid) / total_ok
    avg_obi          = sum(d.get('obi', 0.5) for d in valid) / total_ok

    pct_book_buy  = book_buy  / total_ok * 100.0
    pct_book_sell = book_sell / total_ok * 100.0
    pct_cvd_buy   = cvd_buy   / total_ok * 100.0
    pct_cvd_sell  = cvd_sell  / total_ok * 100.0

    consensus_direction = 'NEUTRAL'
    consensus_pct       = 0.0

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
        'avg_obi':             round(avg_obi, 4),
        'book_buy':            book_buy,
        'book_sell':           book_sell,
        'cvd_buy':             cvd_buy,
        'cvd_sell':            cvd_sell,
        'double_confirm_buy':  double_buy,
        'double_confirm_sell': double_sell,
        'details':             details,
    }
