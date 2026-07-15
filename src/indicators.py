"""
src/indicators.py — Indicateurs techniques pour les cryptos
"""

import logging
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

        df = df.dropna()
        return df
    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}")
        return pd.DataFrame()
