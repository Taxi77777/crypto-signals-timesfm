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

BLACKLIST_KEYWORDS = ['STOCK', 'UP_', 'DOWN_', '3L_', '3S_', 'BEAR_', 'BULL_', '1000']
EXCLUDE_EXPLICIT = {
    # Stablecoins
    "USDC_USDT", "BUSD_USDT", "TUSD_USDT", "USDD_USDT", "FDUSD_USDT", "USDP_USDT",
    # Tokens morts / problematiques
    "LUNA_USDT", "LUNC_USDT", "USTC_USDT", "PUMPFUN_USDT",
    # Contrats uniques / pas sur Spot MEXC
    "DRAM_USDT", "SNXX_USDT", "NICKEL_USDT", "MUU_USDT", "TRUMPOFFICIAL_USDT",
    "MVLL_USDT", "LAB_USDT", "CAP_USDT", "NIL_USDT", "LIT_USDT",
    # Synthetiques / Actions / Commodities non dispo sur MEXC Spot
    "NVIDIA_USDT", "TESLA_USDT", "SPY_USDT", "QQQ_USDT", "EWY_USDT", "SOXS_USDT", "SOXL_USDT",
    "XAU_USDT", "XAUT_USDT", "SILVER_USDT", "USOIL_USDT", "UKOIL_USDT", "NGAS_USDT", "XPT_USDT",
    "COPPER_USDT", "ALUMINUM_USDT", "ZINC_USDT", "LEAD_USDT", "PALLADIUM_USDT", "PLATINUM_USDT",
    "UNITREE_USDT", "SPX500_USDT", "NAS100_USDT", "US30_USDT", "NDX_USDT", "KORU_USDT"
}

def is_valid_crypto(symbol: str) -> bool:
    if symbol in EXCLUDE_EXPLICIT:
        return False
    for kw in BLACKLIST_KEYWORDS:
        if kw in symbol:
            return False
    return True


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
            if not symbol.endswith('_USDT') or not is_valid_crypto(symbol):
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
