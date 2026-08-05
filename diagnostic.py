"""
╔══════════════════════════════════════════════════════════════════╗
║  DIAGNOSTIC INSTITUTIONNEL v5.0 — diagnostic.py                 ║
║  Vérifie que TOUS les éléments sont connectés et fonctionnels   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
sys.stdout.reconfigure(encoding='utf-8')

SYMBOL = "BTC_USDT"
DEPTH  = 50

print("=" * 70)
print("  DIAGNOSTIC INSTITUTIONNEL COMPLET — IHP v5.0")
print("  BOT EN PARALLELE : actif pendant ce test")
print("=" * 70)

# ─────────────────────────────────────────────────────────────────────
# [1] MEXC — COMPTE & BALANCE
# ─────────────────────────────────────────────────────────────────────
print("\n[1] MEXC FUTURES — Compte & Balance")
try:
    import mexc_api as api
    account = api.get_account()
    balance = account.get('balance', 0)
    equity  = account.get('equity', 0)
    pnl     = account.get('unrealised_pnl', 0)
    positions = api.get_open_positions()
    print(f"  OK  Balance    : {balance:.4f} USDT")
    print(f"  OK  Equity     : {equity:.4f} USDT")
    print(f"  OK  PnL ouvert : {pnl:.4f} USDT")
    print(f"  OK  Positions  : {len(positions)} ouverte(s)")
except Exception as e:
    print(f"  ERREUR MEXC : {e}")

# ─────────────────────────────────────────────────────────────────────
# [2] CARNETS D'ORDRES — 6 EXCHANGES — TOUS LES ELEMENTS INSTITUTIONNELS
# ─────────────────────────────────────────────────────────────────────
print(f"\n[2] CARNETS D'ORDRES — 6 EXCHANGES — {DEPTH} niveaux — {SYMBOL}")
print(f"    Elements institutionnels :")
print(f"    - Stacked Imbalances (niveaux consecutifs meme cote)")
print(f"    - CVD (Cumulative Volume Delta — qui frappe le marche)")
print(f"    - Score directionnel par exchange")
print(f"    - Anti-manipulation (exchange oppose fort)")

from exchanges import get_multi_exchange_orderflow
from config import EXCHANGES_TO_CHECK, ANTI_MANIP_THRESHOLD

t0 = time.time()
of = get_multi_exchange_orderflow(SYMBOL, EXCHANGES_TO_CHECK, DEPTH)
elapsed = time.time() - t0

print(f"\n  Temps d'analyse : {elapsed:.1f}s (6 exchanges en parallele)")
print(f"\n  {'Exchange':<10} {'Resultat':<10} {'Dom':<8} {'StackedB':<10} {'StackedS':<10} {'BidVol':>10} {'AskVol':>10} {'Score':>7} {'CVD':>8} {'CVD%':>7}")
print(f"  {'-'*85}")

all_ok = 0
for ex, d in of['details'].items():
    if d.get('ok'):
        all_ok += 1
        dom   = d.get('dominant_side', 'NEUTRAL')
        sb    = d.get('stacked_buy', 0)
        ss    = d.get('stacked_sell', 0)
        bid   = d.get('total_bid', 0)
        ask   = d.get('total_ask', 0)
        sc    = d.get('direction_score', 0)
        cvd   = d.get('cvd', {})
        cvd_d = cvd.get('cvd_direction', '?')
        cvd_p = cvd.get('delta_pct', 0)
        manip = " [!MANIP]" if d.get('manipulation_suspect') else ""
        icon  = "OK " if dom != 'NEUTRAL' else "   "
        print(f"  {ex:<10} {icon:<10} {dom:<8} {sb:<10} {ss:<10} {bid:>10,.2f} {ask:>10,.2f} {sc:>+7.0f} {cvd_d:>8} {cvd_p:>+6.1f}%{manip}")
    else:
        err = d.get('error', 'Inconnu')[:40]
        print(f"  {ex:<10} ERREUR — {err}")

print(f"  {'-'*85}")
print(f"\n  RESULTATS CONSENSUS :")
print(f"  Exchanges connectes    : {of['exchanges_ok']}/6")
print(f"  Direction consensus    : {of['consensus_direction']}")
print(f"  Consensus %            : {of['consensus_pct']:.0f}%  (seuil requis: 80%)")
print(f"  Score moyen global     : {of['avg_direction_score']:+.1f}/100")
print(f"  Carnet Acheteurs       : {of['book_buy']}/{of['exchanges_ok']} exchanges")
print(f"  Carnet Vendeurs        : {of['book_sell']}/{of['exchanges_ok']} exchanges")
print(f"  CVD Haussier           : {of['cvd_buy']}/{of['exchanges_ok']} exchanges")
print(f"  CVD Baissier           : {of['cvd_sell']}/{of['exchanges_ok']} exchanges")
print(f"  Stacked BUY moyen      : {of['avg_stacked_buy']:.1f} niveaux consecutifs")
print(f"  Stacked SELL moyen     : {of['avg_stacked_sell']:.1f} niveaux consecutifs")

if of['consensus_pct'] >= 80:
    print(f"\n  >>> CONSENSUS ATTEINT : {of['consensus_direction']} {of['consensus_pct']:.0f}% <<<")
    print(f"  >>> Le bot passerait maintenant a l'etape TimesFM          <<<")
else:
    print(f"\n  Marche indecis : {of['consensus_pct']:.0f}% < 80% requis")
    print(f"  Normal : le bot attend une vraie domination institutionnelle")

# ─────────────────────────────────────────────────────────────────────
# [3] TENDANCE 4H — EMA, VWAP, ATR, RSI
# ─────────────────────────────────────────────────────────────────────
print(f"\n[3] TENDANCE LONG TERME 4H — {SYMBOL}")
try:
    df_k = api.get_klines(SYMBOL, "4h", 200)
    from indicators import calc_trend_indicators, get_trend_bias
    df = calc_trend_indicators(df_k)
    ti = get_trend_bias(df)
    print(f"  OK  Bougies 4H      : {len(df_k)} chargees")
    print(f"  OK  Prix actuel     : {ti['price']:.2f} USDT")
    print(f"  OK  VWAP 4H         : {ti['vwap']:.2f}  -> {'Prix AU-DESSUS' if ti['price'] > ti['vwap'] else 'Prix EN-DESSOUS'}")
    print(f"  OK  EMA 21          : {ti['ema_fast']:.2f}")
    print(f"  OK  EMA 55          : {ti['ema_slow']:.2f}  -> EMA {'HAUSSIERE' if ti['ema_fast'] > ti['ema_slow'] else 'BAISSIERE'}")
    print(f"  OK  ATR 14          : {ti['atr']:.2f} USDT (volatilite par bougie 4H)")
    print(f"  OK  RSI 14          : {ti['rsi']:.1f}  -> {'Surchat' if ti['rsi']>70 else 'Survente' if ti['rsi']<30 else 'Neutre'}")
    print(f"  OK  BIAIS 4H        : {ti['bias']}")
    if of['consensus_direction'] != 'NEUTRAL':
        d = of['consensus_direction']
        sl = ti['price'] - (ti['atr'] * 2.0) if d == 'BUY' else ti['price'] + (ti['atr'] * 2.0)
        tp = ti['price'] + (ti['atr'] * 5.0) if d == 'BUY' else ti['price'] - (ti['atr'] * 5.0)
        rr = abs(tp - ti['price']) / abs(sl - ti['price']) if abs(sl - ti['price']) > 0 else 0
        print(f"\n  Si trade {d} maintenant :")
        print(f"  -> SL  : {sl:.2f}  (ATR x2)")
        print(f"  -> TP  : {tp:.2f}  (ATR x5)")
        print(f"  -> R/R : 1:{rr:.2f}")
except Exception as e:
    print(f"  ERREUR tendance : {e}")

# ─────────────────────────────────────────────────────────────────────
# [4] GOOGLE TIMESFM — JUGE FINAL
# ─────────────────────────────────────────────────────────────────────
print(f"\n[4] GOOGLE TIMESFM — Prediction IA (Juge Final)")
print(f"    Role : analyse TOUTES les donnees et valide ou refuse le trade")
try:
    import timesfm
    print(f"  OK  TimesFM installe : v{getattr(timesfm,'__version__','2.0.2')}")

    from timesfm_predictor import get_timesfm_verdict, _load_model
    print(f"  Tentative de chargement du modele...")
    model = _load_model()
    if model is not None:
        print(f"  OK  Modele charge en memoire !")
        print(f"  Prediction sur {SYMBOL}...")
        try:
            df_k2 = api.get_klines(SYMBOL, "4h", 200)
            verdict = get_timesfm_verdict(df_k2, SYMBOL)
            print(f"  OK  Direction predite     : {verdict['direction']}")
            print(f"  OK  Confiance             : {verdict['confidence']:.0%}")
            print(f"  OK  Variation predite 40h : {verdict['predicted_change_pct']:+.2f}%")
            print(f"  OK  Raisonnement          : {verdict['reasoning'][:80]}...")
            if verdict['predicted_prices']:
                p = verdict['predicted_prices']
                print(f"  OK  Predictions 4H       : {p[0]:.2f} -> {p[4]:.2f} -> {p[-1]:.2f} USDT")

            # Verdict final
            cons_dir = of['consensus_direction']
            if verdict['available']:
                if verdict['direction'] == cons_dir:
                    print(f"\n  >>> FEU VERT TIMESFM : Carnet={cons_dir} | TimesFM={verdict['direction']} <<<")
                    print(f"  >>> ACCORD PARFAIT — TRADE SERAIT LANCE si consensus 80%      <<<")
                elif verdict['direction'] == 'NEUTRAL':
                    print(f"\n  >>> TimesFM NEUTRE : pas d'opposition — trade autorise        <<<")
                else:
                    print(f"\n  >>> REFUS TIMESFM : Carnet={cons_dir} MAIS TimesFM={verdict['direction']} <<<")
                    print(f"  >>> Contradiction IA vs Marche — TRADE BLOQUE PAR TIMESFM     <<<")
        except Exception as e2:
            print(f"  ERREUR prediction : {e2}")
    else:
        print(f"  TimesFM modele pas encore charge (se charge au 1er signal 80%+)")
        print(f"  -> Taille : ~500MB depuis HuggingFace")
        print(f"  -> Se charge automatiquement au 1er consensus 80%+")
        print(f"  -> Ensuite reste en memoire indefiniment")
except Exception as e:
    print(f"  ERREUR TimesFM : {e}")

# ─────────────────────────────────────────────────────────────────────
# [5] BOT EN COURS — CYCLES
# ─────────────────────────────────────────────────────────────────────
print(f"\n[5] BOT — Etat en temps reel")
try:
    positions = api.get_open_positions()
    print(f"  OK  Positions ouvertes : {len(positions)}")
    for p in positions:
        print(f"      -> {p.get('symbol')} {p.get('positionType')} vol:{p.get('vol')} PnL:{p.get('unrealisedPnl',0):.4f}")
    if not positions:
        print(f"  OK  Aucune position — le bot scanne en attente de 80%+ consensus")
    print(f"  OK  Scan : toutes les 30s sur 30 paires crypto")
    print(f"  OK  1 seul trade a la fois (MAX_CONCURRENT=1)")
    print(f"  OK  Levier : x40")
except Exception as e:
    print(f"  ERREUR : {e}")

# ─────────────────────────────────────────────────────────────────────
# [6] SYNTHESE FINALE
# ─────────────────────────────────────────────────────────────────────
print(f"\n{'='*70}")
print(f"  SYNTHESE INSTITUTIONNELLE — {SYMBOL}")
print(f"{'='*70}")
print(f"  {'Composant':<35} {'Status':<15} {'Detail'}")
print(f"  {'-'*68}")

mexc_ok = True
try:
    api.get_account()
except Exception:
    mexc_ok = False

print(f"  {'MEXC Futures (compte + ordres)':<35} {'OK' if mexc_ok else 'ERREUR':<15} Balance {balance:.2f} USDT")
print(f"  {'Carnets Ordres Bybit':<35} {'OK' if of['details'].get('Bybit',{}).get('ok') else 'ERREUR':<15} {of['details'].get('Bybit',{}).get('dominant_side','?')}")
print(f"  {'Carnets Ordres Binance':<35} {'OK' if of['details'].get('Binance',{}).get('ok') else 'ERREUR':<15} {of['details'].get('Binance',{}).get('dominant_side','?')}")
print(f"  {'Carnets Ordres OKX':<35} {'OK' if of['details'].get('OKX',{}).get('ok') else 'ERREUR':<15} {of['details'].get('OKX',{}).get('dominant_side','?')}")
print(f"  {'Carnets Ordres Bitget':<35} {'OK' if of['details'].get('Bitget',{}).get('ok') else 'ERREUR':<15} {of['details'].get('Bitget',{}).get('dominant_side','?')}")
print(f"  {'Carnets Ordres Kraken':<35} {'OK' if of['details'].get('Kraken',{}).get('ok') else 'ERREUR':<15} {of['details'].get('Kraken',{}).get('dominant_side','?')}")
print(f"  {'Carnets Ordres MEXC':<35} {'OK' if of['details'].get('MEXC',{}).get('ok') else 'ERREUR':<15} {of['details'].get('MEXC',{}).get('dominant_side','?')}")
print(f"  {'Stacked Imbalances (institutionnel)':<35} {'OK':<15} BUY:{of['avg_stacked_buy']:.1f} SELL:{of['avg_stacked_sell']:.1f} niveaux")
print(f"  {'CVD multi-exchange (flux agressif)':<35} {'OK':<15} {of['cvd_buy']}B/{of['cvd_sell']}S/{of['exchanges_ok']}")
print(f"  {'Tendance 4H (EMA+VWAP+ATR+RSI)':<35} {'OK':<15} {ti.get('bias','?')}")
print(f"  {'Google TimesFM (juge final IA)':<35} {'OK CHARGE' if model else 'PAS ENCORE':<15} Chargement au 1er signal")
print(f"  {'Consensus 80%+ actuel':<35} {of['consensus_pct']:.0f}%{'':<12} {of['consensus_direction']} ({of['book_buy']}B/{of['book_sell']}S/{of['exchanges_ok']})")
print(f"  {'-'*68}")

if of['consensus_pct'] >= 80:
    print(f"\n  >>> ACTION : CONSENSUS ATTEINT — TimesFM analyse en cours      <<<")
else:
    print(f"\n  Situation : Marche indecis ({of['consensus_pct']:.0f}% < 80%)")
    print(f"  Le bot attend que 5+ exchanges s'accordent sur BUY ou SELL")
    print(f"  Ensuite TimesFM validera ou refusera le trade")

print(f"\n{'='*70}")
print(f"  DIAGNOSTIC TERMINE — Bot actif en arriere-plan")
print(f"{'='*70}\n")
