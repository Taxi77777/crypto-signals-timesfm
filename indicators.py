"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT                    ║
║          indicators.py — Fisher, VWAP, MA, Volume Profile       ║
║                                                                  ║
║  Stratégie : Fisher crossover + HVN rejection + LVN acceleration║
╚══════════════════════════════════════════════════════════════════╝
"""
import numpy as np
import pandas as pd
from config import (VP_BINS, LVN_THRESHOLD, HVN_THRESHOLD,
                    MA_FAST, MA_SLOW, FISHER_PERIOD, VWAP_SESSION_ONLY)


# ══════════════════════════════════════════════════════════════════
#  FISHER TRANSFORM — Déclencheur principal de la stratégie
#  Logique : Fisher croise à la baisse depuis >1.5 = SELL
#            Fisher croise à la hausse depuis <-1.5 = BUY
# ══════════════════════════════════════════════════════════════════
def calc_fisher(df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
    """
    Fisher Transform (identique à MEXC/TradingView).
    Retourne les colonnes 'fisher' et 'fisher_signal'.
    """
    high  = df['high']
    low   = df['low']

    highest_high = high.rolling(period).max()
    lowest_low   = low.rolling(period).min()

    hlrange = highest_high - lowest_low
    hlrange = hlrange.replace(0, 1e-10)   # Eviter division par zéro

    value = 2.0 * ((df['close'] - lowest_low) / hlrange) - 1.0
    value = value.clip(-0.999, 0.999)     # Clamp pour éviter inf

    # Fisher = 0.5 * ln((1 + value) / (1 - value))
    fisher = 0.5 * np.log((1.0 + value) / (1.0 - value))
    fisher = fisher.ewm(span=3, adjust=False).mean()  # Lissage léger
    fisher_signal = fisher.shift(1)                    # Signal = Fisher décalé d'1

    df['fisher']        = fisher
    df['fisher_signal'] = fisher_signal
    return df


def fisher_crossover(df: pd.DataFrame):
    """
    Détecte un croisement Fisher sur la dernière bougie FERMÉE.
    Retourne: 'SELL', 'BUY', ou None
    """
    if len(df) < 3:
        return None

    # Bougie [−2] = dernière fermée, [−3] = avant-dernière fermée
    f_now  = df['fisher'].iloc[-2]
    fs_now = df['fisher_signal'].iloc[-2]
    f_prev = df['fisher'].iloc[-3]
    fs_prev= df['fisher_signal'].iloc[-3]

    crossed_down = (f_prev >= fs_prev) and (f_now < fs_now)
    crossed_up   = (f_prev <= fs_prev) and (f_now > fs_now)

    # Filtre surachat/survente
    overbought  = (f_prev > 1.5)   # Fisher était haut → croisement baissier fort
    oversold    = (f_prev < -1.5)  # Fisher était bas  → croisement haussier fort

    if crossed_down and overbought:
        return 'SELL'
    if crossed_up and oversold:
        return 'BUY'
    # Croisement sans filtre extrême (signal plus faible mais valide)
    if crossed_down:
        return 'SELL_WEAK'
    if crossed_up:
        return 'BUY_WEAK'
    return None


# ══════════════════════════════════════════════════════════════════
#  VWAP — Volume Weighted Average Price
# ══════════════════════════════════════════════════════════════════
def calc_vwap(df: pd.DataFrame, session_only: bool = True) -> pd.DataFrame:
    """
    VWAP depuis le début de la session (00:00 UTC) ou sur tout l'historique.
    Identique au VWAP affiché sur MEXC/TradingView.
    """
    df = df.copy()
    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3.0

    if session_only and 'open_time' in df.columns:
        df['date'] = pd.to_datetime(df['open_time'], unit='ms').dt.date
        df['cumvol']      = df.groupby('date')['volume'].cumsum()
        df['cumtpvol']    = df.groupby('date').apply(
            lambda g: (g['typical_price'] * g['volume']).cumsum()
        ).reset_index(level=0, drop=True)
    else:
        df['cumvol']   = df['volume'].cumsum()
        df['cumtpvol'] = (df['typical_price'] * df['volume']).cumsum()

    df['vwap'] = df['cumtpvol'] / df['cumvol'].replace(0, np.nan)
    return df


# ══════════════════════════════════════════════════════════════════
#  MOYENNES MOBILES SIMPLES (30 / 60 — comme sur le graphique)
# ══════════════════════════════════════════════════════════════════
def calc_moving_averages(df: pd.DataFrame) -> pd.DataFrame:
    df['ma_fast'] = df['close'].rolling(MA_FAST).mean()
    df['ma_slow'] = df['close'].rolling(MA_SLOW).mean()
    return df


# ══════════════════════════════════════════════════════════════════
#  VOLUME PROFILE (VPVR) — Cœur de la stratégie
# ══════════════════════════════════════════════════════════════════
class VolumeProfile:
    """
    Calcule le Volume Profile sur les klines M15.
    Détecte : LVN (creux), HVN (pics), POC (prix dominant).
    """

    def __init__(self, df: pd.DataFrame, bins: int = VP_BINS,
                 lvn_thresh: float = LVN_THRESHOLD,
                 hvn_thresh: float = HVN_THRESHOLD):
        self.df         = df
        self.bins       = bins
        self.lvn_thresh = lvn_thresh
        self.hvn_thresh = hvn_thresh
        self.result     = None   # DataFrame bins
        self.poc        = None
        self.lvns       = []
        self.hvns       = []

    def compute(self):
        df = self.df.copy()
        highest = df['high'].max()
        lowest  = df['low'].min()
        if highest <= lowest:
            return self

        price_range = highest - lowest
        bin_size    = price_range / self.bins

        # Initialiser les bins
        bin_prices  = [lowest + (i + 0.5) * bin_size for i in range(self.bins)]
        bin_volumes = [0.0] * self.bins

        # Distribuer le volume dans les bins
        for _, row in df.iterrows():
            bar_range = row['high'] - row['low']
            if bar_range <= 0:
                continue
            bar_vol = row['volume']
            for b in range(self.bins):
                b_low  = lowest + b * bin_size
                b_high = b_low + bin_size
                overlap = min(row['high'], b_high) - max(row['low'], b_low)
                if overlap > 0:
                    bin_volumes[b] += bar_vol * (overlap / bar_range)

        max_vol = max(bin_volumes) if bin_volumes else 1.0
        if max_vol == 0:
            return self

        # Construire le DataFrame résultat
        result = pd.DataFrame({
            'price':   bin_prices,
            'volume':  bin_volumes,
            'vol_pct': [v / max_vol for v in bin_volumes],
        })
        result['is_lvn'] = result['vol_pct'] <= self.lvn_thresh
        result['is_hvn'] = result['vol_pct'] >= self.hvn_thresh
        result['is_poc'] = result['volume'] == result['volume'].max()

        self.result   = result
        self.poc      = float(result.loc[result['is_poc'], 'price'].iloc[0])
        self.lvns     = result.loc[result['is_lvn'], 'price'].tolist()
        self.hvns     = result.loc[result['is_hvn'], 'price'].tolist()
        return self

    def nearest(self, current_price: float):
        """
        Retourne les niveaux LVN/HVN les plus proches au-dessus et en dessous.
        """
        if self.result is None:
            return {'lvn_above': None, 'lvn_below': None,
                    'hvn_above': None, 'hvn_below': None, 'poc': self.poc}

        above = self.result[self.result['price'] > current_price]
        below = self.result[self.result['price'] < current_price]

        lvn_above = float(above[above['is_lvn']]['price'].min()) if not above[above['is_lvn']].empty else None
        lvn_below = float(below[below['is_lvn']]['price'].max()) if not below[below['is_lvn']].empty else None
        hvn_above = float(above[above['is_hvn']]['price'].min()) if not above[above['is_hvn']].empty else None
        hvn_below = float(below[below['is_hvn']]['price'].max()) if not below[below['is_hvn']].empty else None

        return {
            'lvn_above': lvn_above,
            'lvn_below': lvn_below,
            'hvn_above': hvn_above,
            'hvn_below': hvn_below,
            'poc':       self.poc,
        }

    def density_at(self, price: float) -> float:
        """Retourne la densité de volume (0-1) au niveau de prix donné."""
        if self.result is None:
            return 0.5
        diffs = (self.result['price'] - price).abs()
        idx   = diffs.idxmin()
        return float(self.result.loc[idx, 'vol_pct'])

    def in_hvn(self, price: float, tol_pct: float = 0.002) -> bool:
        return self.density_at(price) >= self.hvn_thresh

    def in_lvn(self, price: float) -> bool:
        return self.density_at(price) <= self.lvn_thresh
