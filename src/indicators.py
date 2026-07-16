"""
src/indicators.py — Indicateurs techniques pour les cryptos
"""

import logging
import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


def compute_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule RSI, MACD, Bollinger Bands, EMA, ATR sur le DataFrame."""
    try:
        df = df.copy()

        # RSI (14)
        df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

        # MACD
        macd = ta.trend.MACD(df["close"])
        df["macd"]        = macd.macd()
        df["macd_signal"] = macd.macd_signal()
        df["macd_hist"]   = macd.macd_diff()

        # Bollinger Bands (20, 2)
        bb = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
        df["bb_upper"] = bb.bollinger_hband()
        df["bb_lower"] = bb.bollinger_lband()
        df["bb_mid"]   = bb.bollinger_mavg()

        # EMA 20 / 50
        df["ema20"] = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
        df["ema50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()

        # ATR (14) — volatilité
        df["atr"] = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=14
        ).average_true_range()

        # ADX (14) — force de la tendance
        adx_ind = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
        df["adx"] = adx_ind.adx()

        # SMA Volume (20)
        df["volume_sma"] = df["volume"].rolling(window=20).mean()

        # Fisher Transform (10) — vraie recursion d'Ehlers (lissage progressif)
        # value = 0.33*brut + 0.67*prec ; fisher = 0.5*ln((1+v)/(1-v)) + 0.5*fisher_prec
        # -> montee progressive, asymptote ±7.6 : les paliers ±1.5/2/3/4 deviennent significatifs
        period = 9
        highest_high = df["high"].rolling(window=period).max()
        lowest_low   = df["low"].rolling(window=period).min()
        range_hl     = (highest_high - lowest_low).replace(0, 1e-10)
        raw          = (2 * ((df["close"] - lowest_low) / range_hl) - 1).fillna(0.0)
        fishers      = []
        v_prev, f_prev = 0.0, 0.0
        for x in raw:
            v = 0.33 * float(x) + 0.67 * v_prev
            v = max(min(v, 0.999), -0.999)
            f = 0.5 * np.log((1 + v) / (1 - v)) + 0.5 * f_prev
            fishers.append(f)
            v_prev, f_prev = v, f
        df["fisher"] = fishers
        df["fisher_trigger"] = pd.Series(fishers, index=df.index).shift(1)  # ligne signal (Fisher decale de 1)

        df = df.dropna()
        return df
    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}")
        return pd.DataFrame()
