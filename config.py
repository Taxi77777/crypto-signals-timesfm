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

# Décote des ordres éloignés du prix moyen.
# poids = 1 / (1 + DISTANCE_DECAY × distance_relative)
#   collé au mid -> 1.00 | 0.5 % -> 0.44 | 1 % -> 0.29 | 3 % -> 0.12
# Sans cette pondération, un mur posé à 3 % du marché pesait autant
# qu'un ordre collé au prix. Les gros murs lointains sont le plus
# souvent décoratifs et retirés avant d'être touchés.
DISTANCE_DECAY       = 250.0
# Seuil de consensus multi-exchange.
#
# Remis à 80 % (valeur d'origine) pour deux raisons :
#  1. signals_log.csv prouve que 83,3 % a déjà été atteint sur BTC_USDT
#     le 4 août — le seuil est donc bel et bien franchissable.
#  2. timesfm_predictor.py n'accorde son bonus de confiance "Carnet ALIGNE"
#     (+0.12) QUE si cons_pct >= 80. Avec un seuil à 65, les signaux entre
#     65 et 79 % passaient la porte mais perdaient ce bonus, et échouaient
#     ensuite au seuil de confiance de TimesFM. Les deux valeurs doivent
#     rester alignées.
MIN_CONSENSUS_PCT    = 80.0
ANTI_MANIP_THRESHOLD = 35.0   # Un exchange opposé au-delà de ce score = manipulation

# ──────────────────────────────────────────────
#  📊 CONFIRMATION PAR LE VOLUME DES BOUGIES
#
#  Le carnet d'ordres dit QUI pousse en ce moment.
#  Le volume des bougies dit si le mouvement a du CORPS.
#  Ce sont deux informations différentes, et les deux comptent.
#
#  Un déséquilibre de carnet sur une bougie à volume famélique
#  est très souvent du bruit : peu de participants, spread large,
#  mouvement qui se retourne. On exige donc que le volume de la
#  bougie en cours atteigne au moins MIN_VOLUME_RATIO fois sa
#  moyenne sur VOLUME_MA_PERIOD bougies.
#
#  Le ratio est TOUJOURS calculé et journalisé, même si le filtre
#  est désactivé — pour pouvoir mesurer après coup s'il aide.
# ──────────────────────────────────────────────
USE_VOLUME_CONFIRMATION = True
MIN_VOLUME_RATIO        = 1.0    # 1.0 = volume au moins égal à sa moyenne
VOLUME_MA_PERIOD        = 20

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
#  🧪 PAPER TRADING — MESURE AVANT ARGENT RÉEL
#
#  Tant que PAPER_MODE est True, AUCUN ordre n'est envoyé à MEXC.
#  Les signaux sont enregistrés, l'exécution est simulée sur les
#  bougies qui suivent, et les statistiques réelles sont calculées.
#
#  Pourquoi c'est nécessaire : la stratégie repose sur le carnet
#  d'ordres et le CVD. Ces données n'existent pas en historique,
#  donc un backtest classique est impossible. Le forward test est
#  la seule mesure honnête disponible.
#
#  Pour passer en réel : mettre PAPER_MODE = False, ou définir
#  la variable d'environnement PAPER_MODE=false.
# ──────────────────────────────────────────────
PAPER_MODE = os.environ.get("PAPER_MODE", "true").strip().lower() not in ("false", "0", "no", "off")

MAX_HOLD_HOURS    = 24.0   # Fermeture forcée d'une position simulée après ce délai
TAKER_FEE_PCT     = 0.06   # Frais taker MEXC Futures, appliqués à l'entrée ET à la sortie
LIQUIDATION_BUFFER = 0.90  # Liquidation simulée à 90 % de 100/levier % (frais + marge de maintenance)

PAPER_STATE_FILE  = "paper_state.json"
PAPER_TRADES_FILE = "paper_trades.csv"

# ──────────────────────────────────────────────
#  🧠 TIMESFM — SÉVÉRITÉ DU JUGE
#
#  False : TimesFM NEUTRAL laisse passer le trade (comportement
#          historique — l'IA ne bloque qu'en cas de contradiction).
#  True  : seul un accord explicite de TimesFM autorise le trade,
#          conforme à la description "accord obligatoire de l'IA".
# ──────────────────────────────────────────────
TIMESFM_STRICT = True

# Confiance minimale exigée de TimesFM pour valider un trade.
# C'est ce seuil qui donne enfin un POIDS RÉEL aux données des 6 exchanges :
# le bonus de confiance calculé à partir du consensus, des Stacked Imbalances
# et du CVD était auparavant calculé puis jamais utilisé nulle part.
TIMESFM_MIN_CONFIDENCE = 0.55

# Exiger que les métriques des 6 exchanges aient bien été transmises au modèle.
# Si elles manquent, le signal est refusé plutôt que validé à l'aveugle.
TIMESFM_REQUIRE_EXCHANGE_DATA = True

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
