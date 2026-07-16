"""
src/data_fetcher.py — Téléchargement des données crypto via yfinance
"""

import logging
import pandas as pd
import numpy as np
import yfinance as yf
import config

logger = logging.getLogger(__name__)


def fetch_all_pairs(period: str = None, interval: str = None) -> dict:
    """Télécharge les données OHLCV pour toutes les cryptos."""
    all_data = {}
    period = period or config.DATA_PERIOD
    interval = interval or config.DATA_INTERVAL
    for symbol in config.CRYPTO_PAIRS:
        try:
            df = yf.download(
                symbol,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty or len(df) < 50:
                logger.warning(f"{symbol}: données insuffisantes ({len(df) if df is not None else 0} bougies)")
                continue

            # Normaliser les colonnes (yfinance peut retourner MultiIndex)
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)

            df = df.rename(columns={
                "Open": "open", "High": "high",
                "Low": "low",  "Close": "close", "Volume": "volume"
            })
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            all_data[symbol] = df
            logger.info(f"{symbol}: {len(df)} bougies chargées ({interval})")
        except Exception as e:
            logger.error(f"Erreur fetch {symbol}: {e}")
    return all_data


def prepare_timesfm_input(df: pd.DataFrame) -> np.ndarray:
    """Prépare la série de prix pour TimesFM (dernières CONTEXT_LENGTH bougies)."""
    prices = df["close"].values.astype(np.float32)
    if len(prices) > config.CONTEXT_LENGTH:
        prices = prices[-config.CONTEXT_LENGTH:]
    return prices
