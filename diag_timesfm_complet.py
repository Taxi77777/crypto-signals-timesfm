"""
Test COMPLET : verification que TimesFM recoit bien TOUTES les infos
On trace chaque donnee transmise et le verdict final
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

from exchanges import get_multi_exchange_orderflow
from config import EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH
from timesfm_predictor import preload_model, get_timesfm_verdict
import mexc_api as api

print('=' * 70)
print('  TEST COMPLET - TIMESFM RECOIT-IL TOUTES LES INFOS ?')
print('=' * 70)

# STEP 0 - Charger TimesFM
print('\n[ETAPE 0] Chargement Google TimesFM...')
model = preload_model()
print(f'  Modele charge : {model is not None}')
print(f'  Modele type   : {type(model).__name__}')

# STEP 1 - Exchanges
print('\n[ETAPE 1] Lecture 6 exchanges...')
of = get_multi_exchange_orderflow('BTC_USDT', EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH)
print(f'  Exchanges OK  : {of["exchanges_ok"]}/6')
print(f'  Consensus     : {of["consensus_direction"]} {of["consensus_pct"]}%')

# STEP 2 - Bougies
print('\n[ETAPE 2] Chargement 200 bougies 4H...')
df = api.get_klines('BTC_USDT', '4h', 200)
print(f'  Bougies lues  : {len(df)}')
print(f'  Prix actuel   : {df["close"].iloc[-1]:.2f} USDT')

# STEP 3 - Transmission a TimesFM avec trace complete
print('\n[ETAPE 3] TRANSMISSION A TIMESFM...')
print('  Donnees envoyees :')
print(f'    - {len(df)} bougies de prix historiques 4H')
print(f'    - consensus_direction  = {of["consensus_direction"]}')
print(f'    - consensus_pct        = {of["consensus_pct"]}%')
print(f'    - avg_stacked_buy      = {of["avg_stacked_buy"]}')
print(f'    - avg_stacked_sell     = {of["avg_stacked_sell"]}')
print(f'    - cvd_buy (exchanges)  = {of["cvd_buy"]}')
print(f'    - cvd_sell (exchanges) = {of["cvd_sell"]}')
print(f'    - avg_direction_score  = {of["avg_direction_score"]}')
print(f'    - double_confirm_buy   = {of["double_confirm_buy"]}')
print(f'    - double_confirm_sell  = {of["double_confirm_sell"]}')
print(f'    - exchanges_ok         = {of["exchanges_ok"]}')

verdict = get_timesfm_verdict(df, 'BTC_USDT', exchange_data=of)

print()
print('=' * 70)
print('  VERDICT GOOGLE TIMESFM')
print('=' * 70)
print(f'  Modele disponible    : {verdict["available"]}')
print(f'  Donnees exchanges    : {verdict.get("exchange_data_used", False)}')
print(f'  Prix actuel analyse  : {verdict["current_price"]:.2f} USDT')
print(f'  Prix predit (40h)    : {verdict["predicted_prices"][-1]:.2f} USDT' if verdict.get("predicted_prices") else '  Predictions          : aucune')
print(f'  Variation 40h        : {verdict["predicted_change_pct"]:+.3f}%')
print(f'  Direction IA         : {verdict["direction"]}')
print(f'  Confiance IA         : {verdict["confidence"]:.0%}')
print(f'  Raisonnement         : {verdict["reasoning"][:150]}')
print()
print('  VERIFICATION COMMUNICATION :')
ok_model    = verdict["available"]
ok_exdata   = verdict.get("exchange_data_used", False)
ok_prix     = len(verdict.get("predicted_prices", [])) > 0
ok_reasoning= 'Exchanges:' in verdict.get("reasoning", "")

checks = [
    ('Modele TimesFM charge et compile', ok_model),
    ('Donnees exchanges bien recues',    ok_exdata),
    ('Predictions de prix generees',     ok_prix),
    ('Exchanges mentionnes dans verdict',ok_reasoning),
]
for label, ok in checks:
    icon = 'OK' if ok else 'PROBLEME'
    print(f'  [{icon}] {label}')

print()
all_ok = all(ok for _, ok in checks)
if all_ok:
    print('  CONCLUSION : TimesFM recoit TOUTES les infos sans probleme !')
else:
    print('  CONCLUSION : PROBLEME DETECTE - voir les points marques PROBLEME')
print('=' * 70)
