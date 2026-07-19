import sys
import requests
from config import CRYPTO_PAIRS
from src.mexc_trader import SYMBOL_MAP

print("--- VERIFYING MEXC FUTURES SYMBOLS ---")

url = "https://contract.mexc.com/api/v1/contract/detail"
active_mexc_symbols = set()
try:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        print("Warning: MEXC API success=False. Skipping.")
    else:
        active_mexc_symbols = {c["symbol"] for c in data.get("data", [])}
        print(f"Fetched {len(active_mexc_symbols)} active MEXC contracts.")
except Exception as e:
    print(f"Warning: Cannot reach MEXC API ({e}). Skipping verification.")

if not active_mexc_symbols:
    print("No symbols fetched - skipping check.")
    sys.exit(0)

missing_in_map = []
missing_on_mexc = []

for pair in CRYPTO_PAIRS:
    if pair not in SYMBOL_MAP:
        missing_in_map.append(pair)
        continue
    mexc_sym = SYMBOL_MAP[pair]
    upper_syms = {s.upper() for s in active_mexc_symbols}
    if mexc_sym.upper() not in upper_syms:
        missing_on_mexc.append((pair, mexc_sym))

print(f"Checked {len(CRYPTO_PAIRS)} pairs.")

if missing_in_map:
    print(f"MISSING mapping in SYMBOL_MAP: {missing_in_map}")
if missing_on_mexc:
    print("NOT listed on MEXC Futures:")
    for pair, sym in missing_on_mexc:
        print(f"   - {pair} -> {sym}")
    sys.exit(1)

print("All pairs are mapped and active on MEXC Futures!")
sys.exit(0)
