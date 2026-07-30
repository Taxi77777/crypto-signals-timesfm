"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — MEXC BOT CONFIG                  ║
║     Stratégie : 15m LVN ACCELERATION & REJECTION                ║
║     Mode      : TOUS LES FUTURES PAR VOLUME — Levier x40        ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────
#  🔑 MEXC API CREDENTIALS
# ──────────────────────────────────────────────
MEXC_API_KEY    = ""
MEXC_SECRET_KEY = ""
MEXC_BASE_URL   = "https://contract.mexc.com"
MEXC_SPOT_URL   = "https://api.mexc.com"

# ──────────────────────────────────────────────
#  📊 MODE PAIRES — DYNAMIQUE PAR VOLUME
# ──────────────────────────────────────────────
# Si AUTO_SCAN = True  → le bot scanne TOUS les futures MEXC
#                        et prend les TOP N par volume 24h
# Si AUTO_SCAN = False → utilise MANUAL_PAIRS ci-dessous
AUTO_SCAN           = True
AUTO_SCAN_TOP_N     = 40        # Prendre les 40 meilleures par volume
AUTO_SCAN_MIN_VOL   = 5_000_000 # Volume min 24h en USDT (5M minimum)
AUTO_SCAN_INTERVAL  = 3600      # Re-scanner toutes les heures

# Paires manuelles (utilisées si AUTO_SCAN = False)
MANUAL_PAIRS = [
    # ── Crypto Majeurs ──────────────────────
    "BTC_USDT", "ETH_USDT", "BNB_USDT", "SOL_USDT",
    "XRP_USDT", "ADA_USDT", "DOGE_USDT", "AVAX_USDT",
    "LINK_USDT", "MATIC_USDT", "DOT_USDT", "UNI_USDT",
    "LTC_USDT", "BCH_USDT", "ATOM_USDT", "FIL_USDT",
    "NEAR_USDT", "ARB_USDT", "OP_USDT", "SUI_USDT",
    "APT_USDT", "INJ_USDT", "TIA_USDT", "SEI_USDT",
    # ── Matières Premières ───────────────────
    "OIL_USDT",     # WTI Crude Oil     ← TON GRAPHIQUE
    "GOLD_USDT",    # Or
    "SILVER_USDT",  # Argent
    "NATGAS_USDT",  # Gaz Naturel
    # ── Meme Coins (haute volatilité) ────────
    "PEPE_USDT", "FLOKI_USDT", "SHIB_USDT", "WIF_USDT",
    "BONK_USDT", "MEME_USDT",
    # ── DeFi / Layer2 ────────────────────────
    "AAVE_USDT", "CRV_USDT", "SNX_USDT", "GMX_USDT",
    "JUP_USDT", "PYTH_USDT", "JTO_USDT", "STRK_USDT",
    # ── AI Tokens ────────────────────────────
    "FET_USDT", "RNDR_USDT", "WLD_USDT", "TAO_USDT",
]

# ──────────────────────────────────────────────
#  ⚙️ PARAMÈTRES STRATÉGIE LVN M15
# ──────────────────────────────────────────────
TIMEFRAME           = "Min15"
KLINE_LIMIT         = 200
VP_BINS             = 100
LVN_THRESHOLD       = 0.35
HVN_THRESHOLD       = 0.70
MIN_RR              = 1.5
MA_FAST             = 30
MA_SLOW             = 60
FISHER_PERIOD       = 9
VWAP_SESSION_ONLY   = True

# ──────────────────────────────────────────────
#  💰 GESTION DU RISQUE — LEVIER x40
# ──────────────────────────────────────────────
LEVERAGE            = 40       # ⚡ Levier x40

# ⚠️  AVERTISSEMENT LEVIER x40 :
# Un mouvement de 2.5% contre vous = liquidation totale.
# Le risque par trade EST VOLONTAIREMENT BAS pour compenser.
RISK_PER_TRADE_PCT  = 0.25     # 0.25% du capital par trade
                                # Avec x40, exposition réelle = 10%

# Sécurités
MAX_CONCURRENT      = 5        # Max 5 trades simultanés
MAX_DAILY_LOSS_PCT  = 10.0     # Stop journalier -10%
MAX_DRAWDOWN_PCT    = 20.0     # Stop absolu si -20% du capital initial

# Trailing Stop
USE_TRAILING_SL     = True
TRAILING_TRIGGER    = 0.3      # Activer trailing après +0.3% (x40 = +12% effectif)
TRAILING_STEP       = 0.15     # Déplacer le SL de 0.15% à chaque pas

# ──────────────────────────────────────────────
#  🔄 FRÉQUENCES DE MISE À JOUR
# ──────────────────────────────────────────────
UPDATE_INTERVAL_SEC = 15       # Vérification toutes les 15s (plus rapide pour x40)
SIGNAL_COOLDOWN_SEC = 900      # 15 min entre 2 signaux sur la même paire

# ──────────────────────────────────────────────
#  📱 TELEGRAM ALERTES
# ──────────────────────────────────────────────
TG_ENABLED          = False
TG_BOT_TOKEN        = ""
TG_CHAT_ID          = ""

# ──────────────────────────────────────────────
#  🪵 LOGS
# ──────────────────────────────────────────────
LOG_TRADES          = True
LOG_FILE            = "trades_log.csv"
LOG_SIGNALS         = True
SIGNAL_LOG_FILE     = "signals_log.csv"
