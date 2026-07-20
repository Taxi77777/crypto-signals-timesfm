"""
src/smc_filter.py — Filtre Smart Money Concepts (SMC) & Optimal Trade Entry (OTE) pour la Crypto
Calcule les zones Premium/Discount et le retracement de Fibonacci OTE (61.8% - 79%)
"""

import pandas as pd
import logging

logger = logging.getLogger(__name__)

def get_smc_ote_status(df: pd.DataFrame, signal_dir: str, lookback: int = 50) -> dict | None:
    """
    Calcule la structure SMC et OTE sur la jambe de prix récente.
    """
    if len(df) < lookback:
        return None

    recent_df = df.iloc[-lookback:]
    
    # Trouver le swing high et swing low de la période (colonnes en minuscule pour crypto)
    low_val = float(recent_df["low"].min())
    high_val = float(recent_df["high"].max())
    
    range_val = high_val - low_val
    if range_val == 0:
        return None
        
    current_price = float(df.iloc[-1]["close"])
    
    # Retracement en % depuis le sommet (pour un BUY) ou le creux (pour un SELL)
    if signal_dir == "BUY":
        retracement_pct = (high_val - current_price) / range_val * 100
        # Discount zone = plus de 50% de retracement
        zone = "Discount 🟢" if retracement_pct >= 50.0 else "Premium 🔴 (Achat cher)"
        # OTE = entre 61.8% et 79.0%
        is_ote = 61.8 <= retracement_pct <= 79.0
        
        # Niveaux clés en prix
        fib_618 = high_val - 0.618 * range_val
        fib_705 = high_val - 0.705 * range_val
        fib_790 = high_val - 0.790 * range_val
    else:
        # SELL
        retracement_pct = (current_price - low_val) / range_val * 100
        # Discount zone pour un short = plus de 50% de hausse depuis le bas (donc plus cher, avantageux pour short)
        zone = "Premium 🟢 (Short cher/favorable)" if retracement_pct >= 50.0 else "Discount 🔴 (Short bas/risqué)"
        is_ote = 61.8 <= retracement_pct <= 79.0
        
        # Niveaux clés
        fib_618 = low_val + 0.618 * range_val
        fib_705 = low_val + 0.705 * range_val
        fib_790 = low_val + 0.790 * range_val

    return {
        "swing_low": low_val,
        "swing_high": high_val,
        "retracement_pct": retracement_pct,
        "zone": zone,
        "is_ote": is_ote,
        "fib_618": fib_618,
        "fib_705": fib_705,
        "fib_790": fib_790,
    }
