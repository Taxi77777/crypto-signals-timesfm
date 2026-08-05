"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — CONFIG                            ║
║     Stratégie : Multi-Exchange Order Book Imbalance & Trend      ║
║                                                                  ║
║  ⚠️  AUCUNE CLÉ EN DUR DANS CE FICHIER.                          ║
║      Tout est lu depuis les variables d'environnement.           ║
║      En local : définis-les dans ton shell avant de lancer.      ║
║      Sur GitHub Actions : elles viennent des repository secrets. ║
╚══════════════════════════════════════════════════════════════════╝
"""
import os

# ──────────────────────────────────────────────
#  🔑 CREDENTIALS — VARIABLES D'ENVIRONNEMENT UNIQUEMENT
# ──────────────────────────────────────────────
MEXC_API_KEY    = os.environ.get("MEXC_API_KEY", "").strip()
MEXC_SECRET_KEY = os.environ.get("MEXC_SECRET_KEY", "").strip()
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_SPOT_URL   = "https://api.mexc.com"

# ──────────────────────────────────────────────
#  📱 TELEGRAM
# ──────────────────────────────────────────────
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TG_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TG_ENABLED   = bool(TG_BOT_TOKEN and TG_CHAT_ID)

# ──────────────────────────────────────────────
#  🤖 CLÉS API IA OPTIONNELLES
# ──────────────────────────────────────────────
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
KIMI_API_KEY   = os.environ.get("KIMI_API_KEY", "").strip()


def validate_env(require_trading: bool = True) -> list:
    """
    Vérifie que les variables nécessaires sont présentes.
    Retourne la liste des variables manquantes (vide si tout est OK).
    À appeler au démarrage — ne lève pas d'exception, laisse l'appelant décider.
    """
    missing = []
    if require_trading:
        if not MEXC_API_KEY:
            missing.append("MEXC_API_KEY")
        if not MEXC_SECRET_KEY:
            missing.append("MEXC_SECRET_KEY")
    if not TG_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not TG_CHAT_ID:
        missing.append("TELEGRAM_CHAT_ID")
    return missing


# ──────────────────────────────────────────────
#  🌐 EXCHANGES POUR CONSENSUS (APIs publiques, sans clé)
# ──────────────────────────────────────────────
EXCHANGES_TO_CHECK = [
    "MEXC",
    "Bitget",
    "Bybit",
    "OKX",
    "Binance",
    "Kraken",
]

# Nombre minimum d'exchanges devant répondre pour qu'un signal soit analysé
MIN_EXCHANGES_OK = 3

# ──────────────────────────────────────────────
#  ⚖️ CARNET D'ORDRES
# ──────────────────────────────────────────────
ORDERBOOK_DEPTH      = 50     # Niveaux de profondeur demandés par exchange
IMBALANCE_RATIO      = 3.0    # Ratio bid/ask par niveau pour marquer un déséquilibre
MIN_STACKED_LEVELS   = 3      # Niveaux consécutifs requis pour une dominance
MIN_CONSENSUS_PCT    = 65.0   # Seuil de consensus multi-exchange
ANTI_MANIP_THRESHOLD = 35.0   # Un exchange opposé au-delà de ce score = manipulation

# ──────────────────────────────────────────────
#  📈 TENDANCE (informatif — filtres désactivés)
# ──────────────────────────────────────────────
TIMEFRAME        = "4h"
KLINE_LIMIT      = 200
USE_TREND_FILTER = False
MA_TREND_FAST    = 21
MA_TREND_SLOW    = 55
USE_VWAP_FILTER  = False

# ──────────────────────────────────────────────
#  📊 SÉLECTION DES PAIRES
# ──────────────────────────────────────────────
AUTO_SCAN          = True
AUTO_SCAN_TOP_N    = 75
AUTO_SCAN_MIN_VOL  = 1_500_000
AUTO_SCAN_INTERVAL = 300

MANUAL_PAIRS = [
    "BTC_USDT", "ETH_USDT", "SOL_USDT", "BNB_USDT", "XRP_USDT",
    "ADA_USDT", "DOGE_USDT", "AVAX_USDT", "LINK_USDT", "DOT_USDT",
    "PEPE_USDT", "SUI_USDT", "NEAR_USDT",
]

# ──────────────────────────────────────────────
#  💰 GESTION DU RISQUE & LEVIER
# ──────────────────────────────────────────────
LEVERAGE = 40      # Levier appliqué à l'ouverture

# ⚠️ STOP-LOSS DÉSACTIVÉ (choix explicite de l'utilisateur).
#    Sans SL, le dimensionnement ne peut PAS se baser sur une distance au stop.
#    Il se base donc sur une fraction fixe du capital engagée en marge.
USE_SL = False

# Fraction du solde disponible engagée en MARGE sur une position.
# Exposition notionnelle = POSITION_MARGIN_PCT % × LEVERAGE.
# Ex : 2.5 % de marge × levier 40 = 100 % du solde en exposition notionnelle.
# ⚠️ Avec un levier x40 et sans SL, une variation d'environ -2,5 % du prix
#    contre la position suffit à liquider la marge engagée.
POSITION_MARGIN_PCT = 2.5

# Plafond de sécurité : la marge engagée ne dépassera jamais ce montant en USDT.
MAX_MARGIN_USDT = 50.0

RISK_PER_TRADE_PCT = 1.0   # Conservé pour compatibilité (utilisé si USE_SL=True)
ATR_SL_MULT        = 2.0
ATR_TP_MULT        = 5.0
MIN_RR             = 2.0
MAX_CONCURRENT     = 1
MAX_DAILY_LOSS_PCT = 5.0
MAX_DRAWDOWN_PCT   = 15.0

# Trailing stop — ne se déclenche QU'EN PROFIT (verrouillage de gain).
# Ne crée jamais de perte : il ne s'active qu'après +TRAILING_TRIGGER %.
USE_TRAILING_SL  = True
TRAILING_TRIGGER = 1.5
TRAILING_STEP    = 0.5

# ──────────────────────────────────────────────
#  🔄 FRÉQUENCE
# ──────────────────────────────────────────────
UPDATE_INTERVAL_SEC = 300
SIGNAL_COOLDOWN_SEC = 3600

# ──────────────────────────────────────────────
#  🪵 LOGS & ÉTAT PERSISTANT
# ──────────────────────────────────────────────
LOG_TRADES      = True
LOG_FILE        = "trades_log.csv"
LOG_SIGNALS     = True
SIGNAL_LOG_FILE = "signals_log.csv"

# Fichier d'état — indispensable en mode GitHub Actions, où le processus
# repart de zéro à chaque run. Sans lui : pas de notification de clôture,
# pas de cooldown, pas de limite de perte journalière.
STATE_FILE = "state.json"
