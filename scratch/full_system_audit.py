import os, sys, time
sys.path.insert(0, ".")
import logging
import pandas as pd
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

from config import (
    MEXC_API_KEY, MEXC_SECRET_KEY,
    OBI_MIN_FOR_BUY, OBI_MAX_FOR_SELL, VOL_CLIMAX_MULT
)
from src.mexc_trader import (
    get_usdt_balance, get_open_positions, get_mexc_depth,
    get_current_price, SYMBOL_MAP, LEVERAGE, MARGIN_PCT
)
import yfinance as yf

def fetch_pair_data(symbol, period="7d", interval="15m"):
    try:
        df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]].dropna()
    except Exception:
        return pd.DataFrame()
from src.indicators import compute_all_indicators

def main():
    print("=" * 60)
    print("AUDIT COMPLET DU SYSTEME ET DIAGNOSTIC D'EXECUTION")
    print("=" * 60)

    # 1. Audit Compte MEXC
    print("\n1. AUDIT COMPTE & SOLDE MEXC FUTURES")
    bal = get_usdt_balance(MEXC_API_KEY, MEXC_SECRET_KEY)
    positions = get_open_positions(MEXC_API_KEY, MEXC_SECRET_KEY)
    print(f"  * Solde Futures USDT : {bal:.4f} USDT")
    print(f"  * Positions Ouvertes : {len(positions)}")
    print(f"  * Levier Defini     : x{LEVERAGE}")
    print(f"  * Marge par Trade   : {MARGIN_PCT*100}% ({bal * MARGIN_PCT:.2f} USDT)")

    # 2. Audit Tendance Macro Bitcoin
    print("\n2. AUDIT MACRO BITCOIN (1H)")
    df_btc = fetch_pair_data("BTC-USD", period="30d", interval="1h")
    if not df_btc.empty:
        df_btc_ind = compute_all_indicators(df_btc)
        btc_close = float(df_btc_ind["close"].iloc[-1])
        ema_col = "ema200" if "ema200" in df_btc_ind.columns else ("ema_200" if "ema_200" in df_btc_ind.columns else None)
        if ema_col:
            btc_ema200 = float(df_btc_ind[ema_col].iloc[-1])
        else:
            btc_ema200 = float(df_btc_ind["close"].ewm(span=200, adjust=False).mean().iloc[-1])
        btc_bull = btc_close >= btc_ema200 * 0.985
        print(f"  * Bitcoin 1H Close   : ${btc_close:.2f}")
        print(f"  * Bitcoin 1H EMA200  : ${btc_ema200:.2f}")
        print(f"  * Tendance Macro     : {'BULLISH' if btc_bull else 'BEARISH'}")

    # 3. Audit Carnet d'Ordres & OBI sur 10 Cryptos Majeures
    print("\n3. AUDIT CARNET D'ORDRES (OBI +-1.5%)")
    sample_coins = ["ADA-USD", "ONDO-USD", "SOL-USD", "ETH-USD", "NEAR-USD", "AVAX-USD", "LINK-USD", "FET-USD", "RUNE-USD", "WIF-USD"]
    
    obi_buy_ok_count = 0
    obi_sell_ok_count = 0

    for yf_sym in sample_coins:
        mexc_sym = SYMBOL_MAP.get(yf_sym)
        if not mexc_sym:
            continue
        px = get_current_price(mexc_sym)
        bids, asks = get_mexc_depth(mexc_sym, depth_pct=0.015)
        tot = (bids + asks) if (bids + asks) > 0 else 1.0
        obi = bids / tot
        
        buy_ok = obi >= OBI_MIN_FOR_BUY
        sell_ok = obi <= OBI_MAX_FOR_SELL
        if buy_ok: obi_buy_ok_count += 1
        if sell_ok: obi_sell_ok_count += 1

        print(f"  * {mexc_sym:10s} | Prix: ${px:8.4f} | Bids: ${bids:10.0f} | Asks: ${asks:10.0f} | Acheteurs: {obi*100:5.1f}% | BUY OK: {buy_ok} | SELL OK: {sell_ok}")

    # 4. Simulation de Scan et Bilan des Filtres
    print("\n4. SIMULATION DE SCAN SUR 20 PAIRS ET BREAKDOWN FILTRES")
    test_coins = list(SYMBOL_MAP.keys())[:20]
    
    filter_rejections = {
        "rsi": 0,
        "vol_climax": 0,
        "vwap": 0,
        "macro1h": 0,
        "obi": 0,
        "fisher15m": 0
    }
    
    candidates_count = 0
    valid_signals = []

    for yf_sym in test_coins:
        mexc_sym = SYMBOL_MAP.get(yf_sym)
        if not mexc_sym:
            continue

        df_15m = fetch_pair_data(yf_sym, period="7d", interval="15m")
        if df_15m.empty or len(df_15m) < 30:
            continue

        df_15m = compute_all_indicators(df_15m)
        cur_price = float(df_15m["close"].iloc[-1])
        rsi_15m = float(df_15m["rsi"].iloc[-1])
        fish_15m_curr = float(df_15m["fisher"].iloc[-1])
        fish_15m_prev = float(df_15m["fisher"].iloc[-2])
        
        # Vol Climax
        vol_curr = float(df_15m["volume"].iloc[-2])
        vol_mean = float(df_15m["volume"].iloc[-22:-2].mean())
        has_vol_climax = (vol_curr >= VOL_CLIMAX_MULT * vol_mean)

        # VWAP
        typical_price = (df_15m["high"] + df_15m["low"] + df_15m["close"]) / 3.0
        pv = (typical_price * df_15m["volume"]).cumsum()
        vv = df_15m["volume"].cumsum()
        vwap_series = pv / vv.replace(0, 1)
        vwap_curr = float(vwap_series.iloc[-1])
        vwap_discount = (cur_price <= vwap_curr * 1.010)
        vwap_premium  = (cur_price >= vwap_curr * 0.990)

        # OBI
        bids, asks = get_mexc_depth(mexc_sym, depth_pct=0.015)
        tot = (bids + asks) if (bids + asks) > 0 else 1.0
        obi_score = bids / tot
        obi_buy_ok = (obi_score >= OBI_MIN_FOR_BUY)
        obi_sell_ok = (obi_score <= OBI_MAX_FOR_SELL)

        # Conditions BUY
        buy_rsi_ok = (rsi_15m <= 52.0)
        buy_fish_ok = (fish_15m_curr > fish_15m_prev or fish_15m_curr >= -1.5)
        
        is_buy = buy_rsi_ok and buy_fish_ok and has_vol_climax and vwap_discount and obi_buy_ok and btc_bull

        candidates_count += 1

        if is_buy:
            valid_signals.append((yf_sym, "BUY", cur_price))
        else:
            if not buy_rsi_ok: filter_rejections["rsi"] += 1
            if not has_vol_climax: filter_rejections["vol_climax"] += 1
            if not vwap_discount: filter_rejections["vwap"] += 1
            if not btc_bull: filter_rejections["macro1h"] += 1
            if not obi_buy_ok: filter_rejections["obi"] += 1
            if not buy_fish_ok: filter_rejections["fisher15m"] += 1

    print(f"  * Candidates Evaluees : {candidates_count}")
    print(f"  * Signaux Valides     : {len(valid_signals)}")
    print("  * Bilan des Rejets par Filtre :")
    for f_name, f_count in sorted(filter_rejections.items(), key=lambda x: x[1], reverse=True):
        print(f"    - {f_name:12s}: {f_count} candidat(s) bloque(s)")

    print("\n=" * 60)
    print("AUDIT COMPLET TERMINE")
    print("=" * 60)

if __name__ == "__main__":
    main()
