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
MIN_CONFIDENCE           = 75      # Seuil signal fort (%)
MAX_EMA_EXTENSION_PCT    = 0.0     # Écart max toléré avec EMA20 5m (%) (0.0 = Forcer le pullback strict)

# ── Guards de Marché (Filtres de Tendance) ───────────────────────────────────
ENABLE_BTC_GUARD         = False   # Bloque les Altcoins BUY si le BTC 1H est baissier, SELL si le BTC est haussier (Désactivé pour avoir plus de signaux)
ENABLE_DXY_GUARD         = False   # Bloque les BUY si le Dollar Index est haussier (désactivé car très restrictif)
ENABLE_NASDAQ_GUARD      = False   # Bloque les BUY si le Nasdaq est baissier (désactivé car très restrictif)
ENABLE_ETH_BTC_GUARD     = False   # Bloque les Altcoins BUY si la force relative des Altcoins (ETH/BTC) est faible (Désactivé pour avoir plus de signaux)
ENABLE_MTF_FILTER        = True    # Bloque les BUY 5m si la tendance 1H (EMA/Supertrend) est baissière (Activé pour suivre la tendance de fond)

# ── Cryptos surveillées (100 cryptos qualitatives et liquides) ──────────────────
CRYPTO_PAIRS = [
    # Top 20 Majeures
    "BTC-USD",  "ETH-USD",  "BNB-USD",  "SOL-USD",  "XRP-USD",
    "ADA-USD",  "AVAX-USD", "LINK-USD", "DOT-USD",  "LTC-USD",
    "BCH-USD",  "NEAR-USD", "ICP-USD",  "TIA-USD",  "INJ-USD",
    "AAVE-USD", "OP-USD",   "ARB11841-USD", "PEPE-USD",     "SUI20947-USD",
    # 30 Altcoins Qualitatifs
    "APT21794-USD", "SEI-USD", "FET-USD", "RUNE-USD", "IMX10603-USD",
    "LDO-USD", "GRT6719-USD", "STX4847-USD", "JUP29210-USD", "EGLD-USD",
    "TRX-USD", "PYTH-USD", "THETA-USD", "FLOW-USD", "AXS-USD",
    "SAND-USD", "MANA-USD", "ATOM-USD", "ALGO-USD", "VET-USD",
    "HBAR-USD", "KAVA-USD", "GALA-USD", "DYDX-USD", "MINA-USD",
    "WOO-USD", "CHZ-USD", "CRV-USD", "ENS-USD", "PENDLE-USD",
    # 50 Nouveaux Altcoins Qualitatifs et Liquides
    "DOGE-USD", "FLOKI-USD", "ONDO-USD", "AR-USD", "ETC-USD",
    "NEO-USD", "SHIB-USD", "IOTA-USD", "DASH-USD", "BAT-USD",
    "ZIL-USD", "ENJ-USD", "KNC-USD", "LRC-USD", "ANKR-USD",
    "STORJ-USD", "YFI-USD", "SUSHI-USD", "UNI7083-USD", "1INCH-USD",
    "CELO-USD", "ZRX-USD", "IOST-USD", "BAND-USD", "JST-USD",
    "KSM-USD", "AUDIO-USD", "CTSI-USD", "LPT-USD",
    "CELR-USD", "OGN-USD", "COTI-USD", "RLC-USD", "STRK-USD",
    "MTL-USD", "POL-USD", "CKB-USD", "GMT18069-USD", "APE-USD",
    "WLD-USD", "ARKM-USD", "ALT29073-USD", "MANTA-USD", "ETHFI-USD",
    "NOT-USD", "RVN-USD", "QTUM-USD", "JASMY-USD", "WIF-USD"
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
    "EGLD-USD":     "MultiversX (EGLD)",
    "TRX-USD":      "TRON (TRX)",
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
    # 50 Nouveaux Altcoins
    "DOGE-USD":     "Dogecoin (DOGE)",
    "FLOKI-USD":    "Floki (FLOKI)",
    "ONDO-USD":     "Ondo Finance (ONDO)",
    "AR-USD":       "Arweave (AR)",
    "ETC-USD":      "Ethereum Classic (ETC)",
    "NEO-USD":      "Neo (NEO)",
    "SHIB-USD":     "Shiba Inu (SHIB)",
    "IOTA-USD":     "IOTA (IOTA)",
    "DASH-USD":     "Dash (DASH)",
    "BAT-USD":      "Basic Attention Token (BAT)",
    "ZIL-USD":      "Zilliqa (ZIL)",
    "ENJ-USD":      "Enjin Coin (ENJ)",
    "KNC-USD":      "Kyber Network (KNC)",
    "LRC-USD":      "Loopring (LRC)",
    "ANKR-USD":     "Ankr (ANKR)",
    "STORJ-USD":    "Storj (STORJ)",
    "YFI-USD":      "yearn.finance (YFI)",
    "SUSHI-USD":    "SushiSwap (SUSHI)",
    "UNI7083-USD":  "Uniswap (UNI)",
    "1INCH-USD":    "1inch (1INCH)",
    "CELO-USD":     "Celo (CELO)",
    "ZRX-USD":      "0x (ZRX)",
    "IOST-USD":     "IOST (IOST)",
    "BAND-USD":     "Band Protocol (BAND)",
    "JST-USD":      "JUST (JST)",
    "KSM-USD":      "Kusama (KSM)",
    "AUDIO-USD":    "Audius (AUDIO)",
    "CTSI-USD":     "Cartesi (CTSI)",
    "LPT-USD":      "Livepeer (LPT)",
    "CELR-USD":     "Celer Network (CELR)",
    "OGN-USD":      "Origin Protocol (OGN)",
    "COTI-USD":     "COTI (COTI)",
    "RLC-USD":      "iExec RLC (RLC)",
    "STRK-USD":     "Starknet (STRK)",
    "MTL-USD":      "Metal DAO (MTL)",
    "POL-USD":      "POL (POL)",
    "CKB-USD":      "Nervos Network (CKB)",
    "GMT18069-USD": "STEPN (GMT)",
    "APE-USD":      "ApeCoin (APE)",
    "WLD-USD":      "Worldcoin (WLD)",
    "ARKM-USD":     "Arkham (ARKM)",
    "ALT29073-USD": "Altlayer (ALT)",
    "MANTA-USD":    "Manta Network (MANTA)",
    "ETHFI-USD":    "ether.fi (ETHFI)",
    "NOT-USD":      "Notcoin (NOT)",
    "RVN-USD":      "Ravencoin (RVN)",
    "QTUM-USD":     "Qtum (QTUM)",
    "JASMY-USD":    "JasmyCoin (JASMY)",
    "WIF-USD":      "dogwifhat (WIF)",
}
