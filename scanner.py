"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT                    ║
║          scanner.py — Scanner automatique de tous les futures    ║
║                                                                  ║
║  Scanne TOUS les contrats MEXC Futures et sélectionne           ║
║  les TOP N par volume 24h avec filtres de qualité.              ║
╚══════════════════════════════════════════════════════════════════╝
"""
import time
import logging
from typing import List
import mexc_api as api
from config import (AUTO_SCAN_TOP_N, AUTO_SCAN_MIN_VOL, AUTO_SCAN_INTERVAL,
                    MANUAL_PAIRS)

log = logging.getLogger('IHP-SCANNER')

# ══════════════════════════════════════════════════════════════════
#  BLACKLIST — Paires à exclure (instables ou trop peu liquides)
# ══════════════════════════════════════════════════════════════════
BLACKLIST = {
    # Leverage tokens
    "BTCUP_USDT", "BTCDOWN_USDT", "ETHUP_USDT", "ETHDOWN_USDT",
    # Stablecoins
    "USDC_USDT", "BUSD_USDT", "TUSD_USDT",
    # Tokens "piège" à très haute volatilité erratique
    "LUNA_USDT", "LUNC_USDT",
}

# Cache des paires sélectionnées
_cached_pairs: List[str] = []
_last_scan_time: float = 0


def scan_all_futures(force: bool = False) -> List[str]:
    """
    Récupère tous les futures MEXC et retourne les TOP N
    triés par volume 24h décroissant.

    Args:
        force: Si True, ignore le cache et re-scanne maintenant.

    Returns:
        Liste de symboles triés par volume (ex: ['BTC_USDT', 'ETH_USDT', ...])
    """
    global _cached_pairs, _last_scan_time

    # Utiliser le cache si récent
    if not force and _cached_pairs and (time.time() - _last_scan_time) < AUTO_SCAN_INTERVAL:
        return _cached_pairs

    log.info("🔍 Scan de TOUS les futures MEXC en cours...")

    try:
        all_tickers = api.get_all_tickers()
        if not all_tickers:
            log.warning("Aucun ticker reçu — utilisation des paires manuelles")
            return MANUAL_PAIRS
    except Exception as e:
        log.error(f"Erreur scan futures : {e}")
        return MANUAL_PAIRS

    # Parser et filtrer
    pairs_with_vol = []
    for t in all_tickers:
        try:
            symbol = str(t.get('symbol', ''))
            if not symbol.endswith('_USDT'):
                continue
            if symbol in BLACKLIST:
                continue

            # Volume 24h en USDT
            vol24 = float(t.get('amount24', t.get('volume24', 0)) or 0)

            # Prix (rejeter les tokens à prix < 0.0001 = trop risqué)
            last = float(t.get('lastPrice', 0) or 0)
            if last < 0.0001:
                continue

            # Volume minimum
            if vol24 < AUTO_SCAN_MIN_VOL:
                continue

            # Variation 24h (rejeter si >50% = manipulation)
            change = abs(float(t.get('riseFallRate', 0) or 0))
            if change > 0.50:
                log.debug(f"[SCANNER] {symbol} ignoré — variation {change*100:.0f}% trop extrême")
                continue

            pairs_with_vol.append((symbol, vol24))

        except (ValueError, TypeError):
            continue

    # Trier par volume décroissant
    pairs_with_vol.sort(key=lambda x: x[1], reverse=True)

    # Prendre les TOP N
    top_pairs = [sym for sym, _ in pairs_with_vol[:AUTO_SCAN_TOP_N]]

    # Log des résultats
    log.info(f"✅ {len(top_pairs)} paires sélectionnées sur {len(pairs_with_vol)} éligibles :")
    for i, (sym, vol) in enumerate(pairs_with_vol[:AUTO_SCAN_TOP_N], 1):
        vol_m = vol / 1_000_000
        marker = "🔥" if vol_m > 500 else "⭐" if vol_m > 100 else "✅"
        log.info(f"  {i:2d}. {marker} {sym:<20} {vol_m:,.0f}M USDT/24h")

    _cached_pairs   = top_pairs
    _last_scan_time = time.time()
    return top_pairs


def get_active_pairs(auto_scan: bool = True) -> List[str]:
    """
    Retourne la liste des paires à trader.
    Si auto_scan=True : scan automatique par volume.
    Sinon : retourne les paires manuelles de config.
    """
    if auto_scan:
        pairs = scan_all_futures()
        if pairs:
            return pairs
    return MANUAL_PAIRS


def print_market_overview():
    """
    Affiche un aperçu du marché : top gainers, top losers, top volume.
    """
    try:
        all_tickers = api.get_all_tickers()
        if not all_tickers:
            return

        usdt_pairs = []
        for t in all_tickers:
            sym   = str(t.get('symbol', ''))
            if not sym.endswith('_USDT') or sym in BLACKLIST:
                continue
            try:
                vol24  = float(t.get('amount24', 0) or 0)
                change = float(t.get('riseFallRate', 0) or 0)
                last   = float(t.get('lastPrice', 0) or 0)
                if vol24 > 0 and last > 0:
                    usdt_pairs.append({
                        'symbol': sym,
                        'vol24':  vol24,
                        'change': change,
                        'last':   last,
                    })
            except (ValueError, TypeError):
                continue

        if not usdt_pairs:
            return

        # Top 5 par volume
        by_vol = sorted(usdt_pairs, key=lambda x: x['vol24'], reverse=True)[:5]
        log.info("📊 TOP 5 VOLUMES 24H :")
        for p in by_vol:
            log.info(f"   {p['symbol']:<20} Vol:{p['vol24']/1e6:,.0f}M | "
                     f"Prix:{p['last']:.4f} | {p['change']*100:+.1f}%")

        # Top 3 gainers
        gainers = sorted(usdt_pairs, key=lambda x: x['change'], reverse=True)[:3]
        log.info("🚀 TOP 3 GAINERS :")
        for p in gainers:
            log.info(f"   {p['symbol']:<20} {p['change']*100:+.1f}%")

        # Top 3 losers
        losers = sorted(usdt_pairs, key=lambda x: x['change'])[:3]
        log.info("📉 TOP 3 LOSERS :")
        for p in losers:
            log.info(f"   {p['symbol']:<20} {p['change']*100:+.1f}%")

    except Exception as e:
        log.debug(f"Market overview erreur : {e}")
