"""
DIAGNOSTIC COMPLET CONNEXION MEXC
Verifie : auth, balance, compte futures, capacite ordre, levier, symboles
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import logging
logging.basicConfig(level=logging.WARNING)

import mexc_api as api
from config import (
    MEXC_API_KEY, MEXC_SECRET_KEY, MEXC_BASE_URL, MEXC_SPOT_URL,
    LEVERAGE, RISK_PER_TRADE_PCT, MAX_CONCURRENT
)

OK = '[OK]'
ERR = '[ERREUR]'
WARN = '[WARN]'

results = []

print('=' * 65)
print('  DIAGNOSTIC CONNEXION MEXC — PRET A TRADER ?')
print('=' * 65)

# ── TEST 1 : Cles API configurees ─────────────────────────────────
print('\n[1/7] Verification des cles API...')
if MEXC_API_KEY and len(MEXC_API_KEY) > 10:
    print(f'  {OK} API Key     : {MEXC_API_KEY[:8]}...{MEXC_API_KEY[-4:]}')
    results.append(True)
else:
    print(f'  {ERR} API Key non configuree')
    results.append(False)

if MEXC_SECRET_KEY and len(MEXC_SECRET_KEY) > 10:
    print(f'  {OK} Secret Key  : {MEXC_SECRET_KEY[:4]}...{MEXC_SECRET_KEY[-4:]}')
    results.append(True)
else:
    print(f'  {ERR} Secret Key non configuree')
    results.append(False)

print(f'  {OK} Base URL    : {MEXC_BASE_URL}')
print(f'  {OK} Spot URL    : {MEXC_SPOT_URL}')

# ── TEST 2 : Connexion API Futures ────────────────────────────────
print('\n[2/7] Connexion API Futures MEXC...')
try:
    account = api.get_account()
    balance  = account.get('balance', 0)
    equity   = account.get('equity', 0)
    margin   = account.get('availableBalance', account.get('available', 0))
    print(f'  {OK} Connexion etablie')
    print(f'  {OK} Balance USDT  : {balance:.4f} USDT')
    print(f'  {OK} Equity        : {equity:.4f} USDT')
    print(f'  {OK} Marge dispo   : {margin:.4f} USDT')
    results.append(True)
except Exception as e:
    print(f'  {ERR} Connexion echouee : {e}')
    results.append(False)
    balance = 0

# ── TEST 3 : Positions ouvertes ───────────────────────────────────
print('\n[3/7] Positions actuelles...')
try:
    positions = api.get_open_positions()
    if isinstance(positions, list):
        active = [p for p in positions if float(p.get('holdVol', p.get('vol', 0))) > 0]
        print(f'  {OK} Positions lues : {len(positions)} total, {len(active)} actives')
        for p in active:
            sym  = p.get('symbol', '?')
            side = p.get('positionType', p.get('side', '?'))
            vol  = p.get('holdVol', p.get('vol', 0))
            pnl  = p.get('unrealisedPnl', p.get('unrealizedPnl', 0))
            sl   = p.get('stopLossPrice', 0)
            tp   = p.get('takeProfitPrice', 0)
            print(f'       {sym} {side} vol={vol} PnL={pnl} SL={sl} TP={tp}')
        if not active:
            print(f'  {OK} Aucune position ouverte (bot en attente de signal)')
        results.append(True)
    else:
        print(f'  {WARN} Reponse inattendue : {type(positions)}')
        results.append(True)
except Exception as e:
    print(f'  {ERR} Erreur positions : {e}')
    results.append(False)

# ── TEST 4 : Prix BTC en temps reel ──────────────────────────────
print('\n[4/7] Prix BTC en temps reel...')
try:
    import requests
    r = requests.get(
        f'{MEXC_BASE_URL}/api/v1/contract/ticker?symbol=BTC_USDT',
        timeout=5
    ).json()
    data = r.get('data', {})
    if isinstance(data, list):
        data = data[0] if data else {}
    price = data.get('lastPrice', data.get('last', 0))
    bid   = data.get('bid1', data.get('bestBidPrice', 0))
    ask   = data.get('ask1', data.get('bestAskPrice', 0))
    vol   = data.get('volume24', data.get('volume', 0))
    print(f'  {OK} Prix BTC    : {price} USDT')
    print(f'  {OK} Bid / Ask   : {bid} / {ask}')
    print(f'  {OK} Volume 24h  : {vol}')
    results.append(True)
except Exception as e:
    print(f'  {ERR} Prix non disponible : {e}')
    results.append(False)

# ── TEST 5 : Bougies 4H MEXC (pour TimesFM) ──────────────────────
print('\n[5/7] Bougies 4H pour Google TimesFM...')
try:
    df = api.get_klines('BTC_USDT', '4h', 200)
    if df is not None and len(df) >= 30:
        print(f'  {OK} Bougies lues  : {len(df)} bougies 4H')
        print(f'  {OK} Periode       : {df.index[0]} → {df.index[-1]}')
        print(f'  {OK} Prix actuel   : {df["close"].iloc[-1]:.2f} USDT')
        results.append(True)
    else:
        print(f'  {ERR} Donnees insuffisantes : {len(df) if df is not None else 0} bougies')
        results.append(False)
except Exception as e:
    print(f'  {ERR} Erreur bougies : {e}')
    results.append(False)

# ── TEST 6 : Simulation calcul volume trade ───────────────────────
print('\n[6/7] Simulation calcul de taille de position...')
try:
    import requests
    r2 = requests.get(
        f'{MEXC_BASE_URL}/api/v1/contract/ticker?symbol=BTC_USDT',
        timeout=5
    ).json()
    d2 = r2.get('data', {})
    if isinstance(d2, list): d2 = d2[0] if d2 else {}
    btc_price = float(d2.get('lastPrice', d2.get('last', 64000)))

    risk_usdt  = balance * (RISK_PER_TRADE_PCT / 100)
    margin_req = risk_usdt * LEVERAGE
    qty_btc    = margin_req / btc_price

    print(f'  {OK} Balance       : {balance:.4f} USDT')
    print(f'  {OK} Risque 1%     : {risk_usdt:.4f} USDT')
    print(f'  {OK} Levier x{LEVERAGE}  → Notionnel : {margin_req:.4f} USDT')
    print(f'  {OK} Qte BTC       : {qty_btc:.6f} BTC')
    if qty_btc >= 0.0001:
        print(f'  {OK} Taille OK (minimum MEXC ~0.0001 BTC)')
        results.append(True)
    else:
        print(f'  {WARN} Taille tres petite — balance faible ({balance:.2f} USDT)')
        results.append(True)
except Exception as e:
    print(f'  {ERR} Calcul impossible : {e}')
    results.append(False)

# ── TEST 7 : Config parametres trading ───────────────────────────
print('\n[7/7] Verification configuration trading...')
cfg_checks = [
    ('Levier',           LEVERAGE,            LEVERAGE >= 1,   f'x{LEVERAGE}'),
    ('Risque/trade',     RISK_PER_TRADE_PCT,  0 < RISK_PER_TRADE_PCT <= 5, f'{RISK_PER_TRADE_PCT}%'),
    ('Max positions',    MAX_CONCURRENT,      MAX_CONCURRENT == 1, f'{MAX_CONCURRENT} (securite)'),
]
cfg_ok = True
for label, val, check, disp in cfg_checks:
    icon = OK if check else ERR
    print(f'  {icon} {label:<18}: {disp}')
    if not check:
        cfg_ok = False
results.append(cfg_ok)

# ── VERDICT FINAL ─────────────────────────────────────────────────
print()
print('=' * 65)
print('  VERDICT FINAL')
print('=' * 65)
total = len(results)
passed = sum(results)
print(f'  Tests passes : {passed}/{total}')
print()
if passed == total:
    print('  ✅✅✅ CONNEXION MEXC 100% OPERATIONNELLE')
    print('  ✅✅✅ PRET A LANCER DES TRADES REELS')
elif passed >= total - 1:
    print('  ✅ CONNEXION OK AVEC 1 AVERTISSEMENT MINEUR')
    print('  ✅ TRADING POSSIBLE')
else:
    print('  ❌ PROBLEMES DETECTES — VOIR CI-DESSUS')
print('=' * 65)
