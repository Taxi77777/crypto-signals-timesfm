"""
config.py — Configuration du Bot Crypto Signals TimesFM
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

# ── TimesFM ───────────────────────────────────────────────────────────────────
USE_TIMESFM       = os.getenv("USE_TIMESFM", "true").lower() == "true"
FORECAST_HORIZON  = 4    # Prédire 4 périodes en avance (20 min pour bougies 5m)
CONTEXT_LENGTH    = 512  # Nombre de bougies historiques utilisées

# ── Données ───────────────────────────────────────────────────────────────────
DATA_INTERVAL            = os.getenv("DATA_INTERVAL", "5m")
DATA_PERIOD              = "30d"   # 30 jours d'historique (max 60j pour 5m)
SIGNAL_FREQUENCY_HOURS   = int(os.getenv("SIGNAL_FREQUENCY_HOURS", "1"))
MIN_CONFIDENCE           = 55      # Seuil signal fort (%)

# ── Cryptos surveillées (Majeures et solides uniquement) ───────────────────────
CRYPTO_PAIRS = [
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "LINK-USD", "DOT-USD",  "LTC-USD",
    "BCH-USD",  "NEAR-USD", "ICP-USD",  "TIA-USD",  "INJ-USD",
    "AAVE-USD", "OP-USD",   "ARB11841-USD", "TON11419-USD", "SUI20947-USD"
]

PAIR_NAMES = {
    "BTC-USD":   "Bitcoin (BTC)",
    "ETH-USD":   "Ethereum (ETH)",
    "BNB-USD":   "BNB Chain (BNB)",
    "SOL-USD":   "Solana (SOL)",
    "XRP-USD":   "Ripple (XRP)",
    "ADA-USD":   "Cardano (ADA)",
    "AVAX-USD":  "Avalanche (AVAX)",
    "LINK-USD":  "Chainlink (LINK)",
    "DOT-USD":   "Polkadot (DOT)",
    "LTC-USD":   "Litecoin (LTC)",
    "BCH-USD":   "BCH Chain (BCH)",
    "NEAR-USD":  "NEAR Protocol (NEAR)",
    "ICP-USD":   "Internet Computer (ICP)",
    "TIA-USD":   "Celestia (TIA)",
    "INJ-USD":   "Injective (INJ)",
    "AAVE-USD":  "Aave (AAVE)",
    "OP-USD":    "Optimism (OP)",
    "ARB11841-USD": "Arbitrum (ARB)",
    "TON11419-USD": "Toncoin (TON)",
    "SUI20947-USD": "Sui (SUI)",
}
