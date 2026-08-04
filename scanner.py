"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MARKET SCANNER                    ║
║     scanner.py — Sélection automatique des plus gros volumes     ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
from typing import List
import mexc_api as api
from config import AUTO_SCAN_TOP_N, AUTO_SCAN_MIN_VOL, AUTO_SCAN_INTERVAL, MANUAL_PAIRS

log = logging.getLogger('IHP-SCANNER')

BLACKLIST = {
    "BTCUP_USDT", "BTCDOWN_USDT", "ETHUP_USDT", "ETHDOWN_USDT",
    "USDC_USDT", "BUSD_USDT", "TUSD_USDT", "LUNA_USDT", "LUNC_USDT"
}

_cached_pairs: List[str] = []
_last_scan_time: float = 0


def scan_all_futures(force: bool = False) -> List[str]:
    """
    Récupère les contrats USDT Futures et sélectionne les TOP N par volume 24h.
    """
    global _cached_pairs, _last_scan_time

    if not force and _cached_pairs and (time.time() - _last_scan_time) < AUTO_SCAN_INTERVAL:
        return _cached_pairs

    log.info("🔍 Scan des volumes du marché Futures MEXC...")

    try:
        all_tickers = api.get_all_tickers()
        if not all_tickers:
            return MANUAL_PAIRS
    except Exception as e:
        log.error(f"Erreur scan futures : {e}")
        return MANUAL_PAIRS

    pairs_with_vol = []
    for t in all_tickers:
        try:
            symbol = str(t.get('symbol', ''))
            if not symbol.endswith('_USDT') or symbol in BLACKLIST:
                continue

            vol24 = float(t.get('amount24', t.get('volume24', 0)) or 0)
            last  = float(t.get('lastPrice', 0) or 0)

            if last < 0.0001 or vol24 < AUTO_SCAN_MIN_VOL:
                continue

            pairs_with_vol.append((symbol, vol24))
        except (ValueError, TypeError):
            continue

    pairs_with_vol.sort(key=lambda x: x[1], reverse=True)
    top_pairs = [sym for sym, _ in pairs_with_vol[:AUTO_SCAN_TOP_N]]

    log.info(f"✅ {len(top_pairs)} paires sélectionnées par volume :")
    for i, (sym, vol) in enumerate(pairs_with_vol[:AUTO_SCAN_TOP_N], 1):
        vol_m = vol / 1_000_000
        marker = "🔥" if vol_m > 300 else "⭐" if vol_m > 80 else "✅"
        log.info(f"  {i:2d}. {marker} {sym:<18} {vol_m:,.0f}M USDT/24h")

    _cached_pairs   = top_pairs
    _last_scan_time = time.time()
    return top_pairs


def get_active_pairs(auto_scan: bool = True) -> List[str]:
    if auto_scan:
        pairs = scan_all_futures()
        if pairs:
            return pairs
    return MANUAL_PAIRS
