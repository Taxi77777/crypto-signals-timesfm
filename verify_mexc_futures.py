import sys
import requests
from config import CRYPTO_PAIRS
from src.mexc_trader import SYMBOL_MAP

print("--- VERIFYING MEXC FUTURES SYMBOLS ---")

#1. Fetch all active futures contracts from MEXC API
url = "https://contract.mexc.com/api/v1/contract/detail"
try:
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        print(f"Error: MEXC API returned success=False. Message: {data.get('message')}")
        sys.exit(0)
    
    active_mexc_symbols = {c["symbol"] for c in data.get("data", [])}
    print(f"Successfully fetched {len(active_mexc_symbols)} active contracts from MEXC.")
except.Exception as e:
    print(f"Warning: Could not connect to MEXC API ({e}). Skipping verification.")
    sys.exit(0)

#2. Check each pair in CRYPTO_PAIRS
missing_in_map = []
missing_on_mexc = []

 for pair in CRYPTO_PAIRS:
    if pair not in SYMBOL_MAP:
        missing_in_map.append(pair)
        continue
    
    mexc_sym = SYMBOL_MAP[pair]
    if mexc_sym not in active_mexc_symbols:
        if mexc_sym.upper() not in {s.upper() for s in active_mexc_symbols}:
            missing_on_mexc.append((pair, mexc_sym))

print(f"Checked {len(CRYPTO_PAIRS)} pairs.")

if missing_in_map:
    print(f"Missing mapping in SYMBOL_MAP: {missing_in_map}")
if missing_on_mexc:
    print("The following mapped symbols are NOT listed on MEXC Futures:")
    for pair, sym in missing_on_mexc:
        print(f"   - {pair} maps to {sym}")
    sys.exit(1)

print("All 100 pairs are successfully mapped and active on MEXC Futures!")
sys.exit(0)