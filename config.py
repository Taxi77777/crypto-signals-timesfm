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
    "HBAR-USD", "PENDLE-USD", "TON-USD", "KAS-USD", "MKR-USD",
    "FIL-USD", "RENDER-USD",
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
