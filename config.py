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
FORECAST_HORIZON  = 4    # Prédire 4 périodes en avance (60 min pour bougies 15m)
CONTEXT_LENGTH    = 512  # Nombre de bougies historiques utilisées

# ── Données ───────────────────────────────────────────────────────────────────
DATA_INTERVAL            = os.getenv("DATA_INTERVAL", "15m")
DATA_PERIOD              = "60d"   # 60 jours d'historique (parfait pour bougies 15m)
SIGNAL_FREQUENCY_HOURS   = int(os.getenv("SIGNAL_FREQUENCY_HOURS", "1"))
MIN_CONFIDENCE           = 55      # Seuil signal fort (%)

# ── Cryptos surveillées (50 cryptos qualitatives et liquides) ──────────────────
CRYPTO_PAIRS = [
    # Top 20 Majeures
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "LINK-USD", "DOT-USD",  "LTC-USD",
    "BCH-USD",  "NEAR-USD", "ICP-USD",  "TIA-USD",  "INJ-USD",
    "AAVE-USD", "OP-USD",   "ARB11841-USD", "TON11419-USD", "SUI20947-USD",
    # 30 Altcoins Qualitatifs
    "APT21794-USD", "SEI-USD", "FET-USD", "RUNE-USD", "IMX10603-USD",
    "LDO-USD", "GRT6719-USD", "STX4847-USD", "JUP29210-USD", "EGLD-USD",
    "FIL-USD", "PYTH-USD", "THETA-USD", "FLOW-USD", "AXS-USD",
    "SAND-USD", "MANA-USD", "ATOM-USD", "ALGO-USD", "VET-USD",
    "HBAR-USD", "KAVA-USD", "GALA-USD", "DYDX-USD", "MINA-USD",
    "WOO-USD", "CHZ-USD", "CRV-USD", "ENS-USD", "PENDLE-USD"
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
    "TON11419-USD": "Toncoin (TON)",
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
    "EGLD-USD":     "MultiversX (EGLD)",
    "FIL-USD":      "Filecoin (FIL)",
    "PYTH-USD":     "Pyth Network (PYTH)",
    "THETA-USD":    "Theta Network (THETA)",
    "FLOW-USD":     "Flow (FLOW)",
    "AXS-USD":      "Axie Infinity (AXS)",
    "SAND-USD":     "The Sandbox (SAND)",
    "MANA-USD":     "Decentraland (MANA)",
    "ATOM-USD":     "Cosmos (ATOM)",
    "ALGO-USD":     "Algorand (ALGO)",
    "VET-USD":      "VeChain (VET)",
    "HBAR-USD":     "Hedera (HBAR)",
    "KAVA-USD":     "Kava (KAVA)",
    "GALA-USD":     "Gala (GALA)",
    "DYDX-USD":     "dYdX (DYDX)",
    "MINA-USD":     "Mina Protocol (MINA)",
    "WOO-USD":      "WOO Network (WOO)",
    "CHZ-USD":      "Chiliz (CHZ)",
    "CRV-USD":      "Curve DAO (CRV)",
    "ENS-USD":      "ENS (ENS)",
    "PENDLE-USD":   "Pendle (PENDLE)",
}
