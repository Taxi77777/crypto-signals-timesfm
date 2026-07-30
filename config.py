"""
╔══════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MEXC BOT CONFIG          ║
║     Stratégie : 15m LVN ACCELERATION & REJECTION        ║
╚══════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────
#  🔑 MEXC API CREDENTIALS
#  Créer sur : https://www.mexc.com/user/openapi
# ──────────────────────────────────────────────
MEXC_API_KEY    = ""   # Coller votre clé API MEXC ici
MEXC_SECRET_KEY = ""   # Coller votre clé secrète MEXC ici
MEXC_BASE_URL   = "https://contract.mexc.com"   # Futures Perpetual
MEXC_SPOT_URL   = "https://api.mexc.com"        # Spot

# ──────────────────────────────────────────────
#  📊 PAIRES À TRADER — Sélection par volume
#  (Bot tourne sur TOUTES ces paires en parallèle)
# ──────────────────────────────────────────────
TRADING_PAIRS = [
    # Paires Tier 1 — Volume extrême (>500M USDT/24h)
    "BTC_USDT",       # Bitcoin
    "ETH_USDT",       # Ethereum

    # Paires Tier 2 — Très liquides (>100M USDT/24h)
    "OIL_USDT",       # WTI Crude Oil  ← IDENTIFIÉ SUR TON GRAPHIQUE
    "SOL_USDT",       # Solana
    "BNB_USDT",       # BNB
    "XRP_USDT",       # XRP

    # Paires Tier 3 — Liquides (>50M USDT/24h)
    "DOGE_USDT",      # Dogecoin
    "ADA_USDT",       # Cardano
    "AVAX_USDT",      # Avalanche
    "LINK_USDT",      # Chainlink
    "MATIC_USDT",     # Polygon

    # Matières premières (haute volatilité strategy-friendly)
    "GOLD_USDT",      # Or
    "SILVER_USDT",    # Argent
]

# ──────────────────────────────────────────────
#  ⚙️ PARAMÈTRES STRATÉGIE LVN M15
# ──────────────────────────────────────────────
TIMEFRAME           = "Min15"   # M15 obligatoire
KLINE_LIMIT         = 200       # Bougies analysées (≈ 50h)
VP_BINS             = 100       # Tranches du volume profile
LVN_THRESHOLD       = 0.35      # < 35% du max = LVN
HVN_THRESHOLD       = 0.70      # > 70% du max = HVN
MIN_RR              = 1.5       # Ratio Risk/Reward minimum
MA_FAST             = 30        # MA rapide (comme sur ton graphique)
MA_SLOW             = 60        # MA lente
FISHER_PERIOD       = 9         # Période Fisher
VWAP_SESSION_ONLY   = True      # VWAP depuis l'ouverture de session

# ──────────────────────────────────────────────
#  💰 GESTION DU RISQUE
# ──────────────────────────────────────────────
RISK_PER_TRADE_PCT  = 1.0       # % du capital par trade (1%)
MAX_CONCURRENT      = 3         # Max 3 trades simultanés
MAX_DAILY_LOSS_PCT  = 5.0       # Stop journalier à -5%
LEVERAGE            = 5         # Levier x5 (conservateur)
USE_TRAILING_SL     = True      # Trailing stop actif
TRAILING_TRIGGER    = 0.5       # Activer trailing après +0.5% de profit

# ──────────────────────────────────────────────
#  🔄 FRÉQUENCES DE MISE À JOUR
# ──────────────────────────────────────────────
UPDATE_INTERVAL_SEC = 30        # Vérification toutes les 30s
SIGNAL_COOLDOWN_SEC = 900       # 15 min entre 2 signaux sur la même paire

# ──────────────────────────────────────────────
#  📱 TELEGRAM ALERTES
# ──────────────────────────────────────────────
TG_ENABLED          = False     # Mettre True après config
TG_BOT_TOKEN        = ""        # Token @BotFather
TG_CHAT_ID          = ""        # Votre Chat ID

# ──────────────────────────────────────────────
#  🪵 LOGS
# ──────────────────────────────────────────────
LOG_TRADES          = True
LOG_FILE            = "trades_log.csv"
LOG_SIGNALS         = True
SIGNAL_LOG_FILE     = "signals_log.csv"
