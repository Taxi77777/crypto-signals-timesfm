"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO — TREND INDICATORS                  ║
║     indicators.py — EMA 50/200, VWAP, ATR, RSI (Trend & Risk)   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import numpy as np
import pandas as pd
from config import MA_TREND_FAST, MA_TREND_SLOW


def calc_trend_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Calcule la tendance Long Terme (EMA 50, EMA 200, VWAP, ATR, RSI).
    """
    if df is None or len(df) < 50:
        return df

    df = df.copy()

    # 1. EMA 50 & EMA 200 (Tendance Long Terme)
    df['ema_fast'] = df['close'].ewm(span=MA_TREND_FAST, adjust=False).mean()
    if len(df) >= MA_TREND_SLOW:
        df['ema_slow'] = df['close'].ewm(span=MA_TREND_SLOW, adjust=False).mean()
    else:
        df['ema_slow'] = df['ema_fast']

    # 2. VWAP (Volume Weighted Average Price)
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    df['tp_vol']        = df['typical_price'] * df['volume']
    
    # VWAP cumulatif sur le dataframe
    cum_vol   = df['volume'].cumsum()
    cum_tpvol = df['tp_vol'].cumsum()
    df['vwap'] = cum_tpvol / cum_vol.replace(0, np.nan)

    # 3. ATR (Average True Range — 14 périodes) pour SL/TP dynamiques
    high_low = df['high'] - df['low']
    high_close = (df['high'] - df['close'].shift()).abs()
    low_close = (df['low'] - df['close'].shift()).abs()
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean().bfill()

    # 4. RSI (14 périodes)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, np.nan)
    df['rsi'] = 100 - (100 / (1 + rs))

    return df


def get_trend_bias(df: pd.DataFrame) -> dict:
    """
    Analyse le biais de tendance long terme sur la dernière bougie fermée.

    Returns:
        dict: {
            'bias': 'BULLISH' | 'BEARISH' | 'NEUTRAL',
            'price': float,
            'vwap': float,
            'ema_fast': float,
            'ema_slow': float,
            'atr': float,
            'rsi': float
        }
    """
    if df is None or len(df) < 20:
        return {'bias': 'NEUTRAL', 'price': 0, 'vwap': 0, 'ema_fast': 0, 'ema_slow': 0, 'atr': 0, 'rsi': 50}

    last = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    price    = float(last['close'])
    vwap     = float(last['vwap']) if 'vwap' in last and not pd.isna(last['vwap']) else price
    ema_fast = float(last['ema_fast']) if 'ema_fast' in last and not pd.isna(last['ema_fast']) else price
    ema_slow = float(last['ema_slow']) if 'ema_slow' in last and not pd.isna(last['ema_slow']) else price
    atr      = float(last['atr']) if 'atr' in last and not pd.isna(last['atr']) else (price * 0.01)
    rsi      = float(last['rsi']) if 'rsi' in last and not pd.isna(last['rsi']) else 50.0

    # Biais Haussier : Prix > VWAP et EMA 50 > EMA 200
    is_bullish = (price >= vwap) and (ema_fast >= ema_slow)
    # Biais Baissier : Prix < VWAP et EMA 50 < EMA 200
    is_bearish = (price <= vwap) and (ema_fast <= ema_slow)

    bias = 'BULLISH' if is_bullish else ('BEARISH' if is_bearish else 'NEUTRAL')

    return {
        'bias': bias,
        'price': price,
        'vwap': vwap,
        'ema_fast': ema_fast,
        'ema_slow': ema_slow,
        'atr': atr,
        'rsi': rsi
    }
