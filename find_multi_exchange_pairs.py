"""
STRATEGIE DE SELECTION RAPIDE ET RIGOUREUSE :
- Volume reference : Binance Futures >= 3M USDT/24h
- Present sur multi-exchanges (Bybit, OKX, Bitget, Kraken)
- Verification ultra-rapide en parallele des bougies 4H sur MEXC Spot (pas de deliste)
- Blacklist complete des tokens non-crypto
"""
import sys
sys.stdout.reconfigure(encoding='utf-8')
import requests, logging
from concurrent.futures import ThreadPoolExecutor, as_completed
logging.basicConfig(level=logging.WARNING)
from doh_patch import apply_doh_patch
apply_doh_patch()

HEADERS = {'User-Agent': 'Mozilla/5.0'}
MIN_VOL_BINANCE = 3_000_000   # 3M USDT/24h sur Binance Futures

BLACKLIST_BASES = {
    'USDC','BUSD','TUSD','USDD','FDUSD','USDP','GUSD','HUSD','DAI','USDT',
    'LUNA','LUNC','UST','USTC','LUNA2',
    'BTCUP','BTCDOWN','ETHUP','ETHDOWN','BNBUP','BNBDOWN',
    'XAU','XAUT','SILVER','USOIL','UKOIL','GOLD','OIL',
    'SPX500','NAS100','US30','NDX',
    'SAMSUNG','SAMSUNGSTOCK','PUMPFUN','NVIDIA','SNDKSTOCK',
    'SPCXSTOCK','MUSTOCK','SOXL','SKHYNIX','KORU',
}
BLACKLIST_SUFFIXES = ['STOCK','BEAR','BULL','3L','3S','UP','DOWN']

def is_blacklisted(sym):
    base = sym.replace('_USDT','')
    if base in BLACKLIST_BASES: return True
    return any(base.endswith(s) for s in BLACKLIST_SUFFIXES)

# ── ETAPE 1 : Binance Futures — volume ──────────────────────────
print("Etape 1: Recupération du volume Binance Futures...")
try:
    r = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=10)
    binance_vol = {}
    for t in r.json():
        sym = t.get('symbol','')
        if not sym.endswith('USDT') or 'USDT' == sym: continue
        key = sym[:-4] + '_USDT'
        try:
            vol = float(t.get('quoteVolume', 0))
            if vol >= MIN_VOL_BINANCE:
                binance_vol[key] = vol
        except: pass
    print(f"  -> {len(binance_vol)} paires avec volume >= {MIN_VOL_BINANCE/1e6:.0f}M USDT/24h")
except Exception as e:
    print(f"  ERREUR Binance: {e}")
    sys.exit(1)

# ── ETAPE 2 : Exchanges disponibles ────────────────────────────
print("Etape 2: Scan des paires actives sur Bybit, OKX, Bitget, Kraken...")

def get_bybit():
    try:
        r = requests.get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=500", timeout=8).json()
        return {s['symbol'][:-4]+'_USDT' for s in r.get('result',{}).get('list',[])
                if s.get('symbol','').endswith('USDT') and s.get('status')=='Trading'}
    except: return set()

def get_okx():
    try:
        r = requests.get("https://www.okx.com/api/v5/public/instruments?instType=SWAP", timeout=8).json()
        return {s['instId'].replace('-USDT-SWAP','')+'_USDT'
                for s in r.get('data',[]) if s.get('instId','').endswith('-USDT-SWAP') and s.get('state')=='live'}
    except: return set()

def get_bitget():
    try:
        r = requests.get("https://api.bitget.com/api/v2/spot/public/symbols", timeout=8).json()
        return {f"{s['baseCoin']}_USDT" for s in r.get('data',[])
                if s.get('quoteCoin')=='USDT' and s.get('status')=='online'}
    except: return set()

def get_kraken():
    try:
        r = requests.get("https://futures.kraken.com/derivatives/api/v3/instruments", timeout=8).json()
        syms = set()
        for s in r.get('instruments',[]):
            sym = s.get('symbol','')
            if not s.get('tradeable'): continue
            if sym == 'PF_XBTUSD': syms.add('BTC_USDT')
            elif sym.startswith('PF_') and sym.endswith('USD'):
                syms.add(sym.replace('PF_','').replace('USD','')+'_USDT')
        return syms
    except: return set()

bybit  = get_bybit()
okx    = get_okx()
bitget = get_bitget()
kraken = get_kraken()

print(f"  Bybit:  {len(bybit)} paires | OKX: {len(okx)} | Bitget: {len(bitget)} | Kraken: {len(kraken)}")

# ── ETAPE 3 : Croisement candidats ─────────────────────────────
print("\nEtape 3: Croisement et filtrage des tokens...")
candidates = []
for sym, vol in binance_vol.items():
    if is_blacklisted(sym): continue
    present = ['Binance']
    if sym in bybit:   present.append('Bybit')
    if sym in okx:     present.append('OKX')
    if sym in bitget:  present.append('Bitget')
    if sym in kraken:  present.append('Kraken')

    if len(present) >= 3: # Binance + au moins 2 autres plateformes
        candidates.append({'symbol': sym, 'vol_m': vol/1e6, 'exchanges': present, 'n_ex': len(present)})

candidates.sort(key=lambda x: (-x['n_ex'], -x['vol_m']))
print(f"  -> {len(candidates)} paires candidates identifiées")

# ── ETAPE 4 : Vérification klines 4H MEXC Spot en parallèle ────
print(f"\nEtape 4: Verification rapide des bougies 4H MEXC (parallèle)...")

def check_mexc_kline(c):
    spot_sym = c['symbol'].replace('_','')
    try:
        r = requests.get(
            f"https://api.mexc.com/api/v3/klines?symbol={spot_sym}&interval=4h&limit=5",
            headers=HEADERS, timeout=4
        )
        data = r.json()
        if isinstance(data, list) and len(data) >= 3:
            last_close = float(data[-1][4]) if data[-1] else 0
            if last_close > 0:
                c['price'] = last_close
                return c
    except: pass
    return None

valid_pairs = []
with ThreadPoolExecutor(max_workers=20) as executor:
    futures = [executor.submit(check_mexc_kline, c) for c in candidates]
    for fut in as_completed(futures):
        res = fut.result()
        if res:
            valid_pairs.append(res)

valid_pairs.sort(key=lambda x: (-x['n_ex'], -x['vol_m']))

print(f"\n===========================================================================")
print(f"  ✅ {len(valid_pairs)} PAIRES CRYPTO DE CONFIANCE ENTIÈREMENT VALIDÉES")
print(f"===========================================================================")
for i, c in enumerate(valid_pairs, 1):
    exs = '+'.join(c['exchanges'])
    print(f"  {i:3d}. {c['symbol']:<18} {c['vol_m']:>8,.1f}M/24h  {c['n_ex']}/5 exchanges [{exs}]")

print()
print(f"# Total final valide : {len(valid_pairs)} paires crypto")

# Generer le code Python pour config.py
pairs_list = [c['symbol'] for c in valid_pairs]
print("\n# LISTE COMPLETE DES PAIRES POUR CONFIG.PY :")
print("MANUAL_PAIRS = [")
for i in range(0, len(pairs_list), 5):
    chunk = pairs_list[i:i+5]
    print(f"    {', '.join(repr(s) for s in chunk)},")
print("]")
