"""Test communication Exchanges -> Google TimesFM"""
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')

import sys
sys.stdout.reconfigure(encoding='utf-8')

import mexc_api as api
from exchanges import get_multi_exchange_orderflow
from config import EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH
from timesfm_predictor import get_timesfm_verdict, preload_model

SYMBOL = 'BTC_USDT'

print()
print('=' * 65)
print('  TEST COMMUNICATION EXCHANGES -> GOOGLE TIMESFM')
print('=' * 65)

# -- PRE-CHARGEMENT TimesFM en premier (evite la race condition)
print()
print('[0] Pre-chargement Google TimesFM...')
model_ok = preload_model() is not None
print(f'    {"MODELE PRET" if model_ok else "ERREUR CHARGEMENT"}')

# -- ETAPE 1 : Carnets d'ordres 6 exchanges
print()
print('[1] Lecture carnets 6 exchanges...')
of = get_multi_exchange_orderflow(SYMBOL, EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH)
ex_ok    = of['exchanges_ok']
cons_dir = of['consensus_direction']
cons_pct = of['consensus_pct']
stack_b  = of['avg_stacked_buy']
stack_s  = of['avg_stacked_sell']
cvd_b    = of['cvd_buy']
cvd_s    = of['cvd_sell']
score    = of['avg_direction_score']
print(f'    OK -> {ex_ok}/6 connectes | {cons_dir} {cons_pct:.0f}%')
print(f'    Stacked BUY:{stack_b:.1f}  SELL:{stack_s:.1f}')
print(f'    CVD {cvd_b}B/{cvd_s}S | Score:{score:+.0f}')

for ex, d in of['details'].items():
    status = 'OK' if d.get('ok') else 'ERREUR'
    side   = d.get('dominant_side', '?')
    sc     = d.get('direction_score', 0)
    print(f'    {ex:<10} {status} {side} score:{sc:+.0f}')

# -- ETAPE 2 : Bougies 4H
print()
print('[2] Chargement bougies 4H...')
df = api.get_klines(SYMBOL, '4h', 200)
current_price = float(df['close'].iloc[-1])
print(f'    OK -> {len(df)} bougies | Prix actuel : {current_price:.2f} USDT')

# -- ETAPE 3 : Transmission a TimesFM
print()
print('[3] TRANSMISSION vers Google TimesFM...')
print('    -> Envoi des donnees de TOUTES les plateformes + prix')
print('    -> Carnet BUY/SELL par exchange, Stacked Imbalances, CVD, Score')
verdict = get_timesfm_verdict(df, SYMBOL, exchange_data=of)

# -- VERDICT
print()
print('=' * 65)
print('  VERDICT GOOGLE TIMESFM — JUGE FINAL')
print('=' * 65)
available       = verdict['available']
ex_data_used    = verdict.get('exchange_data_used', False)
tfm_dir         = verdict['direction']
tfm_conf        = verdict['confidence']
tfm_delta       = verdict['predicted_change_pct']
tfm_reasoning   = verdict['reasoning']
tfm_prices      = verdict.get('predicted_prices', [])

print(f'  Modele charge        : {available}')
print(f'  Donnees exchanges    : {ex_data_used}')
print(f'  Direction predite    : {tfm_dir}')
print(f'  Confiance IA         : {tfm_conf:.0%}')
print(f'  Variation 40h        : {tfm_delta:+.2f}%')
if tfm_prices:
    print(f'  Pred bougies 4H      : {tfm_prices[0]:.2f} -> {tfm_prices[4]:.2f} -> {tfm_prices[-1]:.2f} USDT')
print(f'  Raisonnement         : {tfm_reasoning[:120]}')

print()
print('  COMPARAISON FINALE :')
print(f'  Carnet 6 exchanges   : {cons_dir} {cons_pct:.0f}%')
print(f'  Google TimesFM dit   : {tfm_dir}')
accord = tfm_dir == cons_dir
neutre = tfm_dir == 'NEUTRAL'

if accord:
    print('  RESULTAT: ACCORD PARFAIT => TRADE SERAIT LANCE')
elif neutre:
    print('  RESULTAT: TimesFM NEUTRE => TRADE AUTORISE (pas d opposition)')
else:
    print('  RESULTAT: DESACCORD => TRADE BLOQUE PAR TIMESFM')

print()
print('  CONCLUSION :')
if not available:
    print('  [ATTENTION] TimesFM non disponible — bug de chargement')
elif not ex_data_used:
    print('  [ATTENTION] Les donnees exchanges n ont PAS ete transmises a TimesFM')
else:
    print('  [OK] Communication etablie : exchanges -> TimesFM -> verdict')
print('=' * 65)
