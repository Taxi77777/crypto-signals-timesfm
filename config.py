"""
config.py — Configuration du Bot Crypto Signals TimesFM
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── MEXC API ──────────────────────────────────────────────────────────────────
MEXC_API_KEY       = os.getenv("MEXC_API_KEY", "")
MEXC_SECRET_KEY    = os.getenv("MEXC_SECRET_KEY", "")

# ── TimesFM ───────────────────────────────────────────────────────────────────
USE_TIMESFM       = os.getenv("USE_TIMESFM", "true").lower() == "true"
FORECAST_HORIZON  = 4    # Prédire 4 périodes en avance (20 min pour bougies 5m)
CONTEXT_LENGTH    = 512  # Nombre de bougies historiques utilisées

# ── Données ───────────────────────────────────────────────────────────────────
DATA_INTERVAL            = os.getenv("DATA_INTERVAL", "5m")
DATA_PERIOD              = "30d"   # 30 jours d'historique (max 60j pour 5m)
SIGNAL_FREQUENCY_HOURS   = int(os.getenv("SIGNAL_FREQUENCY_HOURS", "1"))

# ── Stratégie Scalp 15m (timeframe de trading unique) ────────────────────────
TRADING_TIMEFRAME        = "15m"   # Timeframe d'exécution des signaux
MACRO_TIMEFRAME          = "1h"    # Timeframe de confirmation de tendance macro
TP_SCALP_PCT             = 0.012   # Take Profit scalp = ±1.2% de mouvement de prix

# ── Order Book Imbalance (OBI) — SEUILS UNIQUES ──────────────────────────────
# 0.50 = carnet équilibré | > 0.50 = acheteurs dominants | < 0.50 = vendeurs dominants
OBI_MIN_FOR_BUY          = 0.50   # Exige au moins 50% d'Acheteurs (domination acheteuse) pour un BUY
OBI_MAX_FOR_SELL         = 0.50   # Exige au moins 50% de Vendeurs (domination vendeuse) pour un SELL

# ── Plancher de liquidité (volume 24h) ───────────────────────────────────────
# Le code triait déjà les paires par volume 24h et affichait "PRIORITÉ HAUTE"
# au-delà de 5 M$, mais ce seuil n'excluait RIEN : une paire à 200 k$ de volume
# pouvait être tradée avec un notionnel de plus de 1 000 USD → slippage énorme.
# Ce plancher exclut réellement les paires trop illiquides.
ENABLE_VOLUME_FLOOR      = True
MIN_VOLUME_24H_USDT      = 1_000_000   # 1M$ USDT volume floor (permits all major liquid altcoins)

# ── Volume Climax : bougie clôturée ou bougie en cours ? ─────────────────────
# yfinance renvoie la bougie 15m EN FORMATION comme dernière ligne. Comparer son
# volume partiel à des bougies complètes rend le filtre quasi inatteignable.
# True  = compare la dernière bougie CLÔTURÉE (iloc[-2]) — comparaison à périmètre égal.
# Seuil de hausse de volume vs moyenne 20 bougies pour valider une bougie de climax (+5%)
VOL_CLIMAX_MULT              = 1.05    # volume >= 1.15x la moyenne des 20 précédentes

# ── Filtre Fibonacci en confluence avec le VWAP ──────────────────────────────
# Desactivé par défaut pour éviter d'étouffer les opportunités d'impulsion
ENABLE_VWAP_FIBO         = False
FIBO_ZONE_LOW            = 0.236   # début de la zone de retracement
FIBO_ZONE_HIGH           = 0.786   # fin de la zone (golden pocket élargie)
FIBO_MIN_RANGE_PCT       = 0.004   # range de session mini 0.4% (sinon Fibo = bruit)

# ── Bandes d'écart-type VWAP (version institutionnelle quantifiée) ───────────
# Remplace le test binaire "prix < VWAP" par "prix < VWAP − N×σ".
# σ = dispersion réelle du prix autour du VWAP sur la session.
# Laissé à False : c'est une suggestion, pas ta demande. Passe à True pour comparer.
ENABLE_VWAP_SIGMA_BANDS  = False
VWAP_SIGMA_MULT          = 1.0     # 1.0 = ~16% des bougies passent ; 0.5 = plus permissif

# ── Stop Loss catastrophe (protection anti-liquidation) ──────────────────────
# Le design d'origine n'ouvre AUCUN stop loss : seul le trailing software protège,
# et il ne s'active qu'à +0.5% de profit. Entre l'entrée et +0.5%, la position est
# donc nue. Mettre True pour poser un SL dur dès l'ouverture.
ENABLE_CATASTROPHE_SL    = False
CATASTROPHE_SL_PCT       = 0.009   # 0.9% de mouvement adverse max
MIN_CONFIDENCE           = 75      # Seuil signal fort (%)
MAX_EMA_EXTENSION_PCT    = 0.0     # Écart max toléré avec EMA20 5m (%) (0.0 = Forcer le pullback strict)
ENABLE_WALLS_IN_SIGNAL   = True    # Affiche temporairement les gros murs de carnet d'ordres dans les signaux Telegram


# ── Guards de Marché (Filtres de Tendance) ───────────────────────────────────
ENABLE_BTC_GUARD         = False   # Bloque les Altcoins BUY si le BTC 1H est baissier, SELL si le BTC est haussier (Désactivé pour avoir plus de signaux)
ENABLE_DXY_GUARD         = False   # Bloque les BUY si le Dollar Index est haussier (désactivé car très restrictif)
ENABLE_NASDAQ_GUARD      = False   # Bloque les BUY si le Nasdaq est baissier (désactivé car très restrictif)
ENABLE_ETH_BTC_GUARD     = False   # Bloque les Altcoins BUY si la force relative des Altcoins (ETH/BTC) est faible (Désactivé pour avoir plus de signaux)
ENABLE_MTF_FILTER        = True    # Bloque les BUY 5m si la tendance 1H (EMA/Supertrend) est baissière (Activé pour suivre la tendance de fond)

# ── Cryptos surveillées (Sélection qualitative à forte capitalisation et liquidité) ──
CRYPTO_PAIRS = [
    # Top 20 Majeures
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "LINK-USD", "DOT-USD",  "LTC-USD",
    "BCH-USD",  "NEAR-USD", "ICP-USD",  "TIA-USD",  "INJ-USD",
    "AAVE-USD", "OP-USD",   "ARB11841-USD", "PEPE-USD", "SUI20947-USD",
    # Altcoins Majeurs / Qualitatifs
    "APT21794-USD", "SEI-USD", "FET-USD", "RUNE-USD", "IMX10603-USD",
    "LDO-USD", "GRT6719-USD", "STX4847-USD", "JUP29210-USD", "TRX-USD",
    "PYTH-USD", "THETA-USD", "ATOM-USD", "ALGO-USD", "VET-USD",
    "HBAR-USD", "PENDLE-USD", "KAS-USD", "RENDER-USD",
    # Altcoins à forte liquidité & Memes leaders
    "DOGE-USD", "FLOKI-USD", "ONDO-USD", "AR-USD", "ETC-USD",
    "SHIB-USD", "UNI7083-USD", "STRK-USD", "POL-USD", "CKB-USD",
    "WLD-USD", "ARKM-USD", "NOT-USD", "JASMY-USD", "WIF-USD"
]

PAIR_NAMES = {
    # Majeures
    "BTC-USD":      "Bitcoin (BTC)",
    "ETH-USD":      "Ethereum (ETH)",
    "BNB-USD":      "BNB Chain (BNB)",
    "SOL-USD":      "Solana (SOL)",
    "XRP-USD":      "Ripple (XRP)",
    "ADA-USD":      "Cardano (ADA)",
    "AVAX-USD":     "Avalanche (AVAX)",
    "LINK-USD":     "Chainlink (LINK)",
    "DOT-USD":      "Polkadot (DOT)",
    "LTC-USD":      "Litecoin (LTC)",
    "BCH-USD":      "BCH Chain (BCH)",
    "NEAR-USD":     "NEAR Protocol (NEAR)",
    "ICP-USD":      "Internet Computer (ICP)",
    "TIA-USD":      "Celestia (TIA)",
    "INJ-USD":      "Injective (INJ)",
    "AAVE-USD":     "Aave (AAVE)",
    "OP-USD":       "Optimism (OP)",
    "ARB11841-USD": "Arbitrum (ARB)",
    "PEPE-USD":     "Pepe (PEPE)",
    "SUI20947-USD": "Sui (SUI)",
    # Altcoins
    "APT21794-USD": "Aptos (APT)",
    "SEI-USD":      "Sei (SEI)",
    "FET-USD":      "Artificial Superintelligence (FET)",
    "RUNE-USD":     "THORChain (RUNE)",
    "IMX10603-USD": "Immutable (IMX)",
    "LDO-USD":      "Lido DAO (LDO)",
    "GRT6719-USD":  "The Graph (GRT)",
    "STX4847-USD":  "Stacks (STX)",
    "JUP29210-USD": "Jupiter (JUP)",
    "TRX-USD":      "TRON (TRX)",
    "PYTH-USD":     "Pyth Network (PYTH)",
    "THETA-USD":    "Theta Network (THETA)",
    "ATOM-USD":     "Cosmos (ATOM)",
    "ALGO-USD":     "Algorand (ALGO)",
    "VET-USD":      "VeChain (VET)",
    "HBAR-USD":     "Hedera (HBAR)",
    "PENDLE-USD":   "Pendle (PENDLE)",
    "TON-USD":      "Toncoin (TON)",
    "KAS-USD":      "Kaspa (KAS)",
    "MKR-USD":      "Maker (MKR)",
    "FIL-USD":      "Filecoin (FIL)",
    "RENDER-USD":   "Render (RENDER)",
    # Liquides & Memes
    "DOGE-USD":     "Dogecoin (DOGE)",
    "FLOKI-USD":    "Floki (FLOKI)",
    "ONDO-USD":     "Ondo Finance (ONDO)",
    "AR-USD":       "Arweave (AR)",
    "ETC-USD":      "Ethereum Classic (ETC)",
    "SHIB-USD":     "Shiba Inu (SHIB)",
    "UNI7083-USD":  "Uniswap (UNI)",
    "STRK-USD":     "Starknet (STRK)",
    "POL-USD":      "POL (POL)",
    "CKB-USD":      "Nervos Network (CKB)",
    "WLD-USD":      "Worldcoin (WLD)",
    "ARKM-USD":     "Arkham (ARKM)",
    "NOT-USD":      "Notcoin (NOT)",
    "JASMY-USD":    "JasmyCoin (JASMY)",
    "WIF-USD":      "dogwifhat (WIF)",
}
