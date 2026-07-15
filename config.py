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
FORECAST_HORIZON  = 24   # Prédire 24 périodes en avance
CONTEXT_LENGTH    = 512  # Nombre de bougies historiques utilisées

# ── Données ───────────────────────────────────────────────────────────────────
DATA_INTERVAL            = os.getenv("DATA_INTERVAL", "1h")
DATA_PERIOD              = "60d"   # 60 jours d'historique
SIGNAL_FREQUENCY_HOURS   = int(os.getenv("SIGNAL_FREQUENCY_HOURS", "1"))
MIN_CONFIDENCE           = 70      # Seuil signal fort (%)

# ── Cryptos surveillées (Top 25 par capitalisation) ───────────────────────────
CRYPTO_PAIRS = [
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "DOGE-USD", "DOT-USD",  "MATIC-USD",
    "LINK-USD", "UNI-USD",  "LTC-USD",  "ATOM-USD", "BCH-USD",
    "ALGO-USD", "NEAR-USD", "FTM-USD",  "SAND-USD", "MANA-USD",
    "APE-USD",  "AXS-USD",  "THETA-USD","ICP-USD",  "ETC-USD",
]

PAIR_NAMES = {
    "BTC-USD":   "Bitcoin (BTC)",
    "ETH-USD":   "Ethereum (ETH)",
    "BNB-USD":   "BNB Chain (BNB)",
    "SOL-USD":   "Solana (SOL)",
    "XRP-USD":   "Ripple (XRP)",
    "ADA-USD":   "Cardano (ADA)",
    "AVAX-USD":  "Avalanche (AVAX)",
    "DOGE-USD":  "Dogecoin (DOGE)",
    "DOT-USD":   "Polkadot (DOT)",
    "MATIC-USD": "Polygon (MATIC)",
    "LINK-USD":  "Chainlink (LINK)",
    "UNI-USD":   "Uniswap (UNI)",
    "LTC-USD":   "Litecoin (LTC)",
    "ATOM-USD":  "Cosmos (ATOM)",
    "BCH-USD":   "Bitcoin Cash (BCH)",
    "ALGO-USD":  "Algorand (ALGO)",
    "NEAR-USD":  "NEAR Protocol (NEAR)",
    "FTM-USD":   "Fantom (FTM)",
    "SAND-USD":  "The Sandbox (SAND)",
    "MANA-USD":  "Decentraland (MANA)",
    "APE-USD":   "ApeCoin (APE)",
    "AXS-USD":   "Axie Infinity (AXS)",
    "THETA-USD": "Theta Network (THETA)",
    "ICP-USD":   "Internet Computer (ICP)",
    "ETC-USD":   "Ethereum Classic (ETC)",
}
