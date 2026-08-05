"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.0 — LONG TERM IMBALANCE ENGINE  ║
║     indicators.py — FVG, Order Blocks, Volume Profile           ║
║                                                                  ║
║  CONCEPTS INSTITUTIONNELS :                                      ║
║  1. Fair Value Gap (FVG) — zones de prix non tradées             ║
║     → Bougie 1 high < Bougie 3 low = FVG Haussier               ║
║     → Bougie 1 low  > Bougie 3 high = FVG Baissier              ║
║  2. Order Block (OB) — dernière bougie opposée avant impulse     ║
║     → Footprint institutionnel (gros joueur a posé ses ordres)  ║
║  3. Volume Profile (LVN/HVN) — zones vides de volume            ║
║  4. Tendance (EMA 50/200, VWAP, ATR, RSI)                       ║
╚══════════════════════════════════════════════════════════════════╝
"""
import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List
from config import MA_TREND_FAST, MA_TREND_SLOW


# ──────────────────────────────────────────────────────────────
#  STRUCTURES DE DONNÉES
# ──────────────────────────────────────────────────────────────

@dataclass
class FairValueGap:
    """Zone de prix laissée sans trading bilatéral."""
    direction:    str    # 'BULLISH' ou 'BEARISH'
    top:          float  # Limite haute de la zone
    bottom:       float  # Limite basse de la zone
    mid:          float  # Point milieu (niveau d'entrée idéal)
    formed_at:    int    # Index de la bougie de formation
    size_pct:     float  # Taille du FVG en % du prix
    fresh:        bool = True  # Le prix n'a pas encore retouché ce FVG
    tested:       bool = False # Le prix est en train de le tester


@dataclass
class OrderBlock:
    """Dernière bougie opposée avant un mouvement institutionnel."""
    direction:    str    # 'BULLISH' (défense acheteurs) ou 'BEARISH' (défense vendeurs)
    top:          float  # Limite haute du bloc
    bottom:       float  # Limite basse du bloc
    mid:          float  # Centre
    formed_at:    int    # Index de formation
    impulse_size: float  # Taille du mouvement qui a suivi (validation)
    volume:       float  # Volume au moment de la formation
    fresh:        bool = True


# ──────────────────────────────────────────────────────────────
#  DÉTECTION DES FAIR VALUE GAPS (FVG)
# ──────────────────────────────────────────────────────────────

def detect_fair_value_gaps(df: pd.DataFrame, min_gap_pct: float = 0.05) -> List[FairValueGap]:
    """
    Identifie tous les Fair Value Gaps (FVG) sur le dataframe.
    
    Un FVG se forme quand le marché bouge si vite qu'il laisse une zone
    sans trading bilatéral — le prix y reviendra toujours un jour.
    
    Bullish FVG : candle[i-2].high < candle[i].low
                  (gap entre le high de 2 bougies avant et le low de la bougie actuelle)
    
    Bearish FVG : candle[i-2].low > candle[i].high
                  (gap entre le low de 2 bougies avant et le high de la bougie actuelle)
    
    Args:
        df: OHLCV DataFrame
        min_gap_pct: Taille minimum du gap en % pour être significatif
    """
    fvgs = []
    n = len(df)
    
    if n < 3:
        return fvgs

    for i in range(2, n):
        c1 = df.iloc[i - 2]  # Bougie 1 (2 bougies avant)
        c2 = df.iloc[i - 1]  # Bougie 2 (bougie du milieu - la bougie impulsive)
        c3 = df.iloc[i]      # Bougie 3 (bougie actuelle)

        # ─── FVG HAUSSIER ────────────────────────────────────────
        # Le high de c1 est INFÉRIEUR au low de c3
        # → Zone laissée entre c1.high et c3.low
        if c3['low'] > c1['high']:
            gap_bottom = float(c1['high'])
            gap_top    = float(c3['low'])
            gap_size   = gap_top - gap_bottom
            gap_pct    = (gap_size / float(c2['close'])) * 100
            
            if gap_pct >= min_gap_pct:
                fvgs.append(FairValueGap(
                    direction = 'BULLISH',
                    top       = gap_top,
                    bottom    = gap_bottom,
                    mid       = (gap_top + gap_bottom) / 2,
                    formed_at = i,
                    size_pct  = round(gap_pct, 3)
                ))

        # ─── FVG BAISSIER ─────────────────────────────────────────
        # Le low de c1 est SUPÉRIEUR au high de c3
        # → Zone laissée entre c3.high et c1.low
        elif c3['high'] < c1['low']:
            gap_top    = float(c1['low'])
            gap_bottom = float(c3['high'])
            gap_size   = gap_top - gap_bottom
            gap_pct    = (gap_size / float(c2['close'])) * 100
            
            if gap_pct >= min_gap_pct:
                fvgs.append(FairValueGap(
                    direction = 'BEARISH',
                    top       = gap_top,
                    bottom    = gap_bottom,
                    mid       = (gap_top + gap_bottom) / 2,
                    formed_at = i,
                    size_pct  = round(gap_pct, 3)
                ))

    # Marquer les FVG déjà "remplis" (prix a traversé la zone)
    last_close = float(df.iloc[-1]['close'])
    for fvg in fvgs:
        if fvg.direction == 'BULLISH':
            # FVG haussier "frais" si le prix est encore au-dessus
            fvg.fresh  = last_close > fvg.bottom
            fvg.tested = fvg.bottom <= last_close <= fvg.top
        else:
            # FVG baissier "frais" si le prix est encore en-dessous
            fvg.fresh  = last_close < fvg.top
            fvg.tested = fvg.bottom <= last_close <= fvg.top

    return [f for f in fvgs if f.fresh]  # Garder seulement les FVG non remplis


# ──────────────────────────────────────────────────────────────
#  DÉTECTION DES ORDER BLOCKS (OB)
# ──────────────────────────────────────────────────────────────

def detect_order_blocks(df: pd.DataFrame, min_impulse_pct: float = 0.3) -> List[OrderBlock]:
    """
    Identifie les Order Blocks — zones où les institutions ont placé leurs ordres.
    
    Un Order Block HAUSSIER = la dernière bougie BAISSIÈRE avant un mouvement
    haussier significatif (les acheteurs institutionnels ont absorbé les ventes ici).
    
    Un Order Block BAISSIER = la dernière bougie HAUSSIÈRE avant un mouvement
    baissier significatif (les vendeurs institutionnels ont absorbé les achats ici).
    
    Args:
        df: OHLCV DataFrame
        min_impulse_pct: Taille minimum de l'impulse pour valider l'OB (%)
    """
    obs = []
    n = len(df)
    
    if n < 5:
        return obs

    for i in range(2, n - 2):
        c = df.iloc[i]
        
        # Volume moyen des 20 dernières bougies pour filtrer les volumes anormaux
        vol_avg = df['volume'].iloc[max(0, i-20):i].mean()
        
        # ─── ORDER BLOCK HAUSSIER ────────────────────────────────
        # Dernière bougie baissière avant une montée significative
        if float(c['close']) < float(c['open']):  # Bougie baissière
            # Vérifier que les 2 bougies suivantes montent significativement
            next_high  = max(float(df.iloc[i+1]['high']), float(df.iloc[i+2]['high']))
            impulse    = (next_high - float(c['low'])) / float(c['close']) * 100
            
            if impulse >= min_impulse_pct:
                obs.append(OrderBlock(
                    direction    = 'BULLISH',
                    top          = float(c['high']),
                    bottom       = float(c['low']),
                    mid          = (float(c['high']) + float(c['low'])) / 2,
                    formed_at    = i,
                    impulse_size = round(impulse, 3),
                    volume       = float(c['volume'])
                ))

        # ─── ORDER BLOCK BAISSIER ────────────────────────────────
        # Dernière bougie haussière avant une baisse significative
        elif float(c['close']) > float(c['open']):  # Bougie haussière
            next_low   = min(float(df.iloc[i+1]['low']), float(df.iloc[i+2]['low']))
            impulse    = (float(c['high']) - next_low) / float(c['close']) * 100
            
            if impulse >= min_impulse_pct:
                obs.append(OrderBlock(
                    direction    = 'BEARISH',
                    top          = float(c['high']),
                    bottom       = float(c['low']),
                    mid          = (float(c['high']) + float(c['low'])) / 2,
                    formed_at    = i,
                    impulse_size = round(impulse, 3),
                    volume       = float(c['volume'])
                ))

    # Filtrer les OB invalides (déjà traversés de loin)
    last_close = float(df.iloc[-1]['close'])
    valid_obs = []
    for ob in obs:
        # Un OB haussier est valide si le prix est encore au-dessus du bas
        if ob.direction == 'BULLISH' and last_close > ob.bottom * 0.98:
            ob.fresh = last_close > ob.bottom
            valid_obs.append(ob)
        # Un OB baissier est valide si le prix est encore en-dessous du haut
        elif ob.direction == 'BEARISH' and last_close < ob.top * 1.02:
            ob.fresh = last_close < ob.top
            valid_obs.append(ob)

    return valid_obs


# ──────────────────────────────────────────────────────────────
#  DÉTECTION DE LA ZONE D'IMBALANCE ACTIVE
# ──────────────────────────────────────────────────────────────

def find_active_imbalance_zone(df: pd.DataFrame, price: float) -> Optional[dict]:
    """
    Trouve si le prix est actuellement dans ou proche d'une zone d'imbalance
    (FVG ou Order Block) — c'est le setup d'entrée long terme.
    
    Retourne la zone la plus proche et son type.
    """
    fvgs = detect_fair_value_gaps(df, min_gap_pct=0.05)
    obs  = detect_order_blocks(df, min_impulse_pct=0.3)
    
    # Tolérance de proximité : 0.3% du prix
    tolerance = price * 0.003
    
    best_zone = None
    best_dist = float('inf')
    
    # Chercher FVG proche
    for fvg in fvgs:
        # Prix dans le FVG ou à moins de 0.3% du bord
        dist_to_mid = abs(price - fvg.mid)
        if fvg.bottom - tolerance <= price <= fvg.top + tolerance:
            if dist_to_mid < best_dist:
                best_dist = dist_to_mid
                best_zone = {
                    'type':      'FVG',
                    'direction': fvg.direction,
                    'top':       fvg.top,
                    'bottom':    fvg.bottom,
                    'mid':       fvg.mid,
                    'size_pct':  fvg.size_pct,
                    'in_zone':   True,
                    'distance':  0.0,
                    'formed_at': fvg.formed_at
                }
        else:
            # Proche mais pas dedans (retour imminent)
            dist = min(abs(price - fvg.top), abs(price - fvg.bottom))
            if dist < tolerance * 3 and dist < best_dist:
                best_dist = dist
                best_zone = {
                    'type':      'FVG',
                    'direction': fvg.direction,
                    'top':       fvg.top,
                    'bottom':    fvg.bottom,
                    'mid':       fvg.mid,
                    'size_pct':  fvg.size_pct,
                    'in_zone':   False,
                    'distance':  round(dist / price * 100, 3),
                    'formed_at': fvg.formed_at
                }

    # Chercher Order Block proche
    for ob in obs:
        if not ob.fresh:
            continue
        dist_to_mid = abs(price - ob.mid)
        if ob.bottom - tolerance <= price <= ob.top + tolerance:
            if dist_to_mid < best_dist:
                best_dist = dist_to_mid
                best_zone = {
                    'type':         'ORDER_BLOCK',
                    'direction':    ob.direction,
                    'top':          ob.top,
                    'bottom':       ob.bottom,
                    'mid':          ob.mid,
                    'impulse_pct':  ob.impulse_size,
                    'in_zone':      True,
                    'distance':     0.0,
                    'formed_at':    ob.formed_at
                }
        else:
            dist = min(abs(price - ob.top), abs(price - ob.bottom))
            if dist < tolerance * 3 and dist < best_dist:
                best_dist = dist
                best_zone = {
                    'type':         'ORDER_BLOCK',
                    'direction':    ob.direction,
                    'top':          ob.top,
                    'bottom':       ob.bottom,
                    'mid':          ob.mid,
                    'impulse_pct':  ob.impulse_size,
                    'in_zone':      False,
                    'distance':     round(dist / price * 100, 3),
                    'formed_at':    ob.formed_at
                }

    return best_zone


# ──────────────────────────────────────────────────────────────
#  INDICATEURS DE TENDANCE
# ──────────────────────────────────────────────────────────────

def calc_trend_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calcule EMA 50/200, VWAP, ATR, RSI."""
    if df is None or len(df) < 50:
        return df

    df = df.copy()

    df['ema_fast'] = df['close'].ewm(span=MA_TREND_FAST, adjust=False).mean()
    df['ema_slow'] = df['close'].ewm(span=MA_TREND_SLOW, adjust=False).mean() if len(df) >= MA_TREND_SLOW else df['ema_fast']

    df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    df['tp_vol']        = df['typical_price'] * df['volume']
    df['vwap']          = df['tp_vol'].cumsum() / df['volume'].cumsum().replace(0, np.nan)

    high_low    = df['high'] - df['low']
    high_close  = (df['high'] - df['close'].shift()).abs()
    low_close   = (df['low']  - df['close'].shift()).abs()
    tr          = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    df['atr']   = tr.rolling(14).mean().bfill()

    delta       = df['close'].diff()
    gain        = delta.where(delta > 0, 0).rolling(14).mean()
    loss        = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs          = gain / loss.replace(0, np.nan)
    df['rsi']   = 100 - (100 / (1 + rs))

    return df


def get_trend_bias(df: pd.DataFrame) -> dict:
    """Retourne le biais de tendance et les métriques actuelles."""
    if df is None or len(df) < 20:
        return {'bias': 'NEUTRAL', 'price': 0, 'vwap': 0, 'ema_fast': 0, 'ema_slow': 0, 'atr': 0, 'rsi': 50}

    last     = df.iloc[-2] if len(df) >= 2 else df.iloc[-1]
    price    = float(last['close'])
    vwap     = float(last['vwap']) if 'vwap' in last and not pd.isna(last['vwap']) else price
    ema_fast = float(last['ema_fast']) if 'ema_fast' in last and not pd.isna(last['ema_fast']) else price
    ema_slow = float(last['ema_slow']) if 'ema_slow' in last and not pd.isna(last['ema_slow']) else price
    atr      = float(last['atr'])  if 'atr'  in last and not pd.isna(last['atr'])  else price * 0.01
    rsi      = float(last['rsi'])  if 'rsi'  in last and not pd.isna(last['rsi'])  else 50.0

    is_bullish = (price >= vwap) and (ema_fast >= ema_slow)
    is_bearish = (price <= vwap) and (ema_fast <= ema_slow)
    bias = 'BULLISH' if is_bullish else ('BEARISH' if is_bearish else 'NEUTRAL')

    return {
        'bias': bias, 'price': price, 'vwap': vwap,
        'ema_fast': ema_fast, 'ema_slow': ema_slow, 'atr': atr, 'rsi': rsi
    }
