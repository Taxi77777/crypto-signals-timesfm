"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MULTI-EXCHANGE OBI CONFIG        ║
║     Stratégie : Multi-Exchange Order Book Imbalance & Trend     ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────
#  🔑 MEXC API CREDENTIALS
# ──────────────────────────────────────────────
MEXC_API_KEY    = "mx0vgliPQVUSuDsTAx"
MEXC_SECRET_KEY = "64ee4910041642a2a0d37de4b49ebde6"
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_SPOT_URL   = "https://api.mexc.com"

# ──────────────────────────────────────────────
#  🤖 CLÉS API DES IAS (GRATUIT)
#
#  GEMINI (100% GRATUIT, sans CB) :
#    1. Va sur https://aistudio.google.com
#    2. Clique "Get API Key" → créer une clé
#    3. Colle la clé ici (format: AIzaSy...)
#
#  KIMI (crédits gratuits offerts) :
#    1. Va sur https://platform.moonshot.cn/console/api-keys
#    2. Crée un compte → clé API gratuite
#    3. Colle la clé ici (format: sk-...)
# ──────────────────────────────────────────────
GEMINI_API_KEY  = ""   # ← Colle ta clé Gemini ici (aistudio.google.com)
KIMI_API_KEY    = ""   # ← Colle ta clé Kimi ici (platform.moonshot.cn)


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
#  ⚖️ CARNET D'ORDRES — ANALYSE INSTITUTIONNELLE
# ──────────────────────────────────────────────
ORDERBOOK_DEPTH         = 50      # 50 niveaux de profondeur par exchange
IMBALANCE_RATIO         = 2.0     # Ratio bid/ask par niveau
MIN_STACKED_LEVELS      = 2       # 2 blocs consécutifs minimum
MIN_CONSENSUS_PCT       = 80.0    # 80%+ = 5/6 exchanges dans la MEME direction
ANTI_MANIP_THRESHOLD    = 35.0    # Anti-manipulation seuil

# ──────────────────────────────────────────────
#  📈 TENDANCE LONG TERME (4H)
# ──────────────────────────────────────────────
TIMEFRAME           = "4h"    # 4H pour setup long terme
KLINE_LIMIT         = 200     # Nombre de bougies analysées
USE_TREND_FILTER    = False   # Désactivé — le carnet d'ordres suffit
MA_TREND_FAST       = 21
MA_TREND_SLOW       = 55
USE_VWAP_FILTER     = False   # Désactivé

# ──────────────────────────────────────────────
#  📊 MODE PAIRES (Auto-Scan ou Manuel)
# ──────────────────────────────────────────────
AUTO_SCAN           = True
AUTO_SCAN_TOP_N     = 75       # Sélectionner les 75 plus gros volumes 24h crypto
AUTO_SCAN_MIN_VOL   = 1_500_000 # Minimum 1.5 Million USDT/24h
AUTO_SCAN_INTERVAL  = 300      # Re-scanner le marché toutes les 5 min

MANUAL_PAIRS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "LINK_USDT", "DOT_USDT",
    "OIL_USDT", "GOLD_USDT", "PEPE_USDT", "SUI_USDT", "NEAR_USDT"
]

# ──────────────────────────────────────────────
#  💰 GESTION DU RISQUE & LEVIER (LONG TERME)
# ──────────────────────────────────────────────
LEVERAGE            = 40       # Levier x40 (setup long terme)
RISK_PER_TRADE_PCT  = 1.0      # 1% du capital risqué par trade
USE_SL              = False    # SL Désactivé selon demande utilisateur
ATR_SL_MULT         = 2.0      # SL = ATR x 2.0 (si réactivé)
ATR_TP_MULT         = 5.0      # TP = ATR x 5.0 (ratio R/R 1:2.5)
MIN_RR              = 2.0      # Ratio minimum 1:2.0
MAX_CONCURRENT      = 1        # 1 SEUL trade à la fois — dès que terminé, prochain scan
MAX_DAILY_LOSS_PCT  = 5.0      # Stop journalier à -5% du capital
MAX_DRAWDOWN_PCT    = 15.0     # Stop absolu si drawdown -15%

# Trailing Stop
USE_TRAILING_SL     = True
TRAILING_TRIGGER    = 1.5      # Déclencher après +1.5% de profit
TRAILING_STEP       = 0.5      # Trailing step %

# ──────────────────────────────────────────────
#  🔄 FRÉQUENCE D'ANALYSE
# ──────────────────────────────────────────────
UPDATE_INTERVAL_SEC = 300      # Scan toutes les 5 minutes selon demande utilisateur
SIGNAL_COOLDOWN_SEC = 3600     # 1 heure de pause entre 2 signaux sur la même paire

# ──────────────────────────────────────────────
#  📱 TELEGRAM ALERTES
# ──────────────────────────────────────────────
TG_ENABLED          = True
TG_BOT_TOKEN        = "8347280600:AAGY6UJKbLULT58j1rJpC9TQm_kR0mJsQew"
TG_CHAT_ID          = "375129602"

# ──────────────────────────────────────────────
#  🪵 LOGS CSV
# ──────────────────────────────────────────────
LOG_TRADES          = True
LOG_FILE            = "trades_log.csv"
LOG_SIGNALS         = True
SIGNAL_LOG_FILE     = "signals_log.csv"
