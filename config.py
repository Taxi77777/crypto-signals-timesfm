"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MULTI-EXCHANGE OBI CONFIG        ║
║     Stratégie : Multi-Exchange Order Book Imbalance & Trend     ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────
#  🔑 MEXC API CREDENTIALS (Exécution des ordres)
# ──────────────────────────────────────────────
MEXC_API_KEY    = ""   # Vos clés API MEXC Futures
MEXC_SECRET_KEY = ""
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_SPOT_URL   = "https://api.mexc.com"

# ──────────────────────────────────────────────
#  🌐 EXCHANGES POUR CONSENSUS (100% Gratuit — Pas de clé API requise)
# ──────────────────────────────────────────────
# Le bot va interroger ces 6 échanges majeurs en parallèle
EXCHANGES_TO_CHECK = [
    "MEXC",
    "Bitget",
    "Bybit",
    "OKX",
    "Binance",
    "Kraken"
]

# ──────────────────────────────────────────────
#  ⚖️ SEUILS DE CONSENSUS & CARNET D'ORDRES (OBI)
# ──────────────────────────────────────────────
ORDERBOOK_DEPTH     = 20      # Profondeur du carnet (20 niveaux d'ordres)
OBI_BUY_THRESHOLD   = 0.58    # OBI > 58% = Domination nette des acheteurs
OBI_SELL_THRESHOLD  = 0.42    # OBI < 42% = Domination nette des vendeurs
MIN_CONSENSUS_PCT   = 70.0    # 70% minimum des échanges doivent s'accorder (ex: 4/5 ou 5/6)

# ──────────────────────────────────────────────
#  📈 FILTRE DE TENDANCE LONG TERME
# ──────────────────────────────────────────────
TIMEFRAME           = "1h"    # Unité de temps principale pour la tendance (1H / 4H)
KLINE_LIMIT         = 200     # Nombre de bougies analysées
USE_TREND_FILTER    = True    # Exiger que la tendance Long Terme soit alignée avec l'OBI
MA_TREND_FAST       = 50      # Moyenne mobile rapide 50
MA_TREND_SLOW       = 200     # Moyenne mobile lente 200 (Long Terme)
USE_VWAP_FILTER     = True    # Exiger d'être du bon côté du VWAP

# ──────────────────────────────────────────────
#  📊 MODE PAIRES (Auto-Scan ou Manuel)
# ──────────────────────────────────────────────
AUTO_SCAN           = True
AUTO_SCAN_TOP_N     = 30       # Sélectionner les 30 plus gros volumes 24h
AUTO_SCAN_MIN_VOL   = 5_000_000 # Minimum 5 Millions USDT/24h
AUTO_SCAN_INTERVAL  = 1800     # Re-scanner le marché toutes les 30 min

MANUAL_PAIRS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "LINK_USDT", "DOT_USDT",
    "OIL_USDT", "GOLD_USDT", "PEPE_USDT", "SUI_USDT", "NEAR_USDT"
]

# ──────────────────────────────────────────────
#  💰 GESTION DU RISQUE & LEVIER
# ──────────────────────────────────────────────
LEVERAGE            = 10       # Levier conseillé pour du trend/long terme (ex: 5x à 20x)
RISK_PER_TRADE_PCT  = 1.0      # 1% du capital risqué par trade
MIN_RR              = 1.5      # Ratio Risque/Rendement minimum (1:1.5)
MAX_CONCURRENT      = 4        # Max 4 positions ouvertes simultanément
MAX_DAILY_LOSS_PCT  = 5.0      # Stop journalier à -5% du capital
MAX_DRAWDOWN_PCT    = 15.0     # Stop absolu si drawdown -15%

# Trailing Stop
USE_TRAILING_SL     = True
TRAILING_TRIGGER    = 0.8      # Déclencher le Trailing SL après +0.8% de profit
TRAILING_STEP       = 0.4      # Trailing step %

# ──────────────────────────────────────────────
#  🔄 FRÉQUENCE D'ANALYSE
# ──────────────────────────────────────────────
UPDATE_INTERVAL_SEC = 20       # Scan du carnet toutes les 20 secondes
SIGNAL_COOLDOWN_SEC = 600      # 10 minutes de pause par paire entre 2 signaux

# ──────────────────────────────────────────────
#  📱 TELEGRAM ALERTES
# ──────────────────────────────────────────────
TG_ENABLED          = False    # Passer à True avec vos identifiants
TG_BOT_TOKEN        = ""
TG_CHAT_ID          = ""

# ──────────────────────────────────────────────
#  🪵 LOGS CSV
# ──────────────────────────────────────────────
LOG_TRADES          = True
LOG_FILE            = "trades_log.csv"
LOG_SIGNALS         = True
SIGNAL_LOG_FILE     = "signals_log.csv"
