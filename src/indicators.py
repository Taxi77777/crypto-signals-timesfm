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

        # ── SUPERTREND (ATR 10, multiplicateur 3) — suiveur de tendance crypto ──
        st_period, st_mult = 10, 3.0
        atr_st = ta.volatility.AverageTrueRange(
            df["high"], df["low"], df["close"], window=st_period
        ).average_true_range()
        hl2 = (df["high"] + df["low"]) / 2
        upper = hl2 + st_mult * atr_st
        lower = hl2 - st_mult * atr_st
        close_v = df["close"].values
        up_v, lo_v = upper.values, lower.values
        st_line = [0.0] * len(df)
        st_dir  = [1]   * len(df)   # 1 = haussier (BUY), -1 = baissier (SELL)
        for i in range(len(df)):
            if i == 0:
                st_line[i] = lo_v[i]
                continue
            f_up = up_v[i] if (up_v[i] < st_line[i-1] or close_v[i-1] > st_line[i-1]) else st_line[i-1]
            f_lo = lo_v[i] if (lo_v[i] > st_line[i-1] or close_v[i-1] < st_line[i-1]) else st_line[i-1]
            if close_v[i] > f_up:
                st_dir[i], st_line[i] = 1, f_lo
            elif close_v[i] < f_lo:
                st_dir[i], st_line[i] = -1, f_up
            else:
                st_dir[i] = st_dir[i-1]
                st_line[i] = f_lo if st_dir[i] == 1 else f_up
        df["supertrend"]     = st_line
        df["supertrend_dir"] = st_dir
        # Flip = changement de direction sur la derniere bougie (signal fort)
        df["st_flip_up"]   = (pd.Series(st_dir, index=df.index) == 1)  & (pd.Series(st_dir, index=df.index).shift(1) == -1)
        df["st_flip_down"] = (pd.Series(st_dir, index=df.index) == -1) & (pd.Series(st_dir, index=df.index).shift(1) == 1)

        # ── STOCHASTIQUE RSI & CROISEMENT REVERSAL (Zone 20/80) ──
        try:
            rsi_s = df["rsi"]
            rsi_min = rsi_s.rolling(14).min()
            rsi_max = rsi_s.rolling(14).max()
            stoch_rsi_raw = (rsi_s - rsi_min) / (rsi_max - rsi_min + 1e-8) * 100
            df["stoch_rsi_k"] = stoch_rsi_raw.rolling(3).mean()
            df["stoch_rsi_d"] = df["stoch_rsi_k"].rolling(3).mean()
        except Exception as e:
            df["stoch_rsi_k"] = 50.0
            df["stoch_rsi_d"] = 50.0

        # ── BOUGIE D'AVALEMENT SUR EMA20 (Engulfing Candle Reversal) ──
        try:
            o = df["open"].values
            c = df["close"].values
            h = df["high"].values
            l = df["low"].values
            ema20_arr = df["ema20"].values
            
            engulf = ["NONE"] * len(df)
            for i in range(1, len(df)):
                if c[i] > o[i] and c[i-1] < o[i-1] and c[i] >= o[i-1] and l[i] <= ema20_arr[i] * 1.002:
                    engulf[i] = "BULLISH_ENGULFING"
                elif c[i] < o[i] and c[i-1] > o[i-1] and c[i] <= o[i-1] and h[i] >= ema20_arr[i] * 0.998:
                    engulf[i] = "BEARISH_ENGULFING"
            df["engulfing_reversal"] = engulf
        except Exception as e:
            df["engulfing_reversal"] = "NONE"

        df = df.dropna()
        return df
    except Exception as e:
        logger.error(f"Erreur calcul indicateurs: {e}")
        return pd.DataFrame()
