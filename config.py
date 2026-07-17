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

# ── Cryptos surveillées (Top 50 par capitalisation) ───────────────────────────
CRYPTO_PAIRS = [
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "DOGE-USD", "DOT-USD",  "JASMY-USD",
    "LINK-USD", "TRX-USD",  "LTC-USD",  "ATOM-USD", "BCH-USD",
    "ALGO-USD", "NEAR-USD", "FIL-USD",  "SAND-USD", "MANA-USD",
    "APE-USD",  "AXS-USD",  "THETA-USD","ICP-USD",  "ETC-USD",
    "SHIB-USD", "TON11419-USD", "SUI20947-USD", "PEPE24478-USD", "WIF-USD",
    "RENDER-USD","APT21794-USD", "TIA-USD", "GRT6719-USD", "OP-USD",
    "ARB11841-USD", "STX4847-USD", "VET-USD", "LDO-USD", "JUP29210-USD",
    "SEI-USD",  "FLOKI-USD","FET-USD",  "AAVE-USD", "MKR-USD",
    "RUNE-USD", "GALA-USD", "FLOW-USD", "WLD-USD",  "IMX10603-USD",
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
    "JASMY-USD": "Jasmy (JASMY)",
    "LINK-USD":  "Chainlink (LINK)",
    "TRX-USD":   "Tron (TRX)",
    "LTC-USD":   "Litecoin (LTC)",
    "ATOM-USD":  "Cosmos (ATOM)",
    "BCH-USD":   "Bitcoin Cash (BCH)",
    "ALGO-USD":  "Algorand (ALGO)",
    "NEAR-USD":  "NEAR Protocol (NEAR)",
    "FIL-USD":   "Filecoin (FIL)",
    "SAND-USD":  "The Sandbox (SAND)",
    "MANA-USD":  "Decentraland (MANA)",
    "APE-USD":   "ApeCoin (APE)",
    "AXS-USD":   "Axie Infinity (AXS)",
    "THETA-USD": "Theta Network (THETA)",
    "ICP-USD":   "Internet Computer (ICP)",
    "ETC-USD":   "Ethereum Classic (ETC)",
    "SHIB-USD":  "Shiba Inu (SHIB)",
    "TON11419-USD": "Toncoin (TON)",
    "SUI20947-USD": "Sui (SUI)",
    "PEPE24478-USD": "Pepe (PEPE)",
    "WIF-USD":   "Dogwifhat (WIF)",
    "RENDER-USD":"Render (RENDER)",
    "APT21794-USD": "Aptos (APT)",
    "TIA-USD":   "Celestia (TIA)",
    "GRT6719-USD": "The Graph (GRT)",
    "OP-USD":    "Optimism (OP)",
    "ARB11841-USD": "Arbitrum (ARB)",
    "STX4847-USD": "Stacks (STX)",
    "VET-USD":   "VeChain (VET)",
    "LDO-USD":   "Lido DAO (LDO)",
    "JUP29210-USD": "Jupiter (JUP)",
    "SEI-USD":   "Sei (SEI)",
    "FLOKI-USD": "Floki (FLOKI)",
    "FET-USD":   "Artificial Superintelligence (FET)",
    "AAVE-USD":  "Aave (AAVE)",
    "MKR-USD":   "Maker (MKR)",
    "RUNE-USD":  "THORChain (RUNE)",
    "GALA-USD":  "Gala (GALA)",
    "FLOW-USD":  "Flow (FLOW)",
    "WLD-USD":   "Worldcoin (WLD)",
    "IMX10603-USD": "Immutable (IMX)",
}
