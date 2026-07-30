"""
╔══════════════════════════════════════════════════════════════════╗
║          INSTITUTIONAL HUNTER PRO — MEXC BOT                    ║
║          strategy.py — Analyse Séquentielle 3 Étapes            ║
║                                                                  ║
║  LOGIQUE SÉQUENTIELLE :                                          ║
║   Étape 1 (Bougie T-2) : Identification du LVN                  ║
║     → Prix arrive près d'un LVN (creux de volume)               ║
║                                                                  ║
║   Étape 2 (Bougie T-1) : Test du niveau                         ║
║     → Prix teste le LVN (mèche, hésitation, volume faible)      ║
║                                                                  ║
║   Étape 3 (Bougie T)   : CONFIRMATION — Déclencheur             ║
║     → BUY  : clôture au-dessus du LVN (rebond validé)           ║
║     → SELL : clôture en-dessous du LVN (cassure validée)        ║
║     → Entrée à la clôture M15 de cette bougie                   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from indicators import (calc_fisher, calc_vwap, calc_moving_averages,
                        VolumeProfile, fisher_crossover)
from config import MIN_RR, KLINE_LIMIT


# ══════════════════════════════════════════════════════════════════
#  STRUCTURE DU SIGNAL 3 ÉTAPES
# ══════════════════════════════════════════════════════════════════
@dataclass
class StepAnalysis:
    """Analyse d'une étape (une bougie M15)."""
    step:        int     # 1, 2, ou 3
    open:        float = 0.0
    high:        float = 0.0
    low:         float = 0.0
    close:       float = 0.0
    volume:      float = 0.0
    fisher:      float = 0.0
    vwap:        float = 0.0
    vp_density:  float = 0.0   # Densité de volume au niveau de clôture (0=LVN, 1=HVN)
    near_lvn:    bool  = False  # Prix proche d'un LVN
    near_hvn:    bool  = False  # Prix proche d'un HVN
    lvn_level:   float = 0.0   # Niveau LVN le plus proche
    is_bullish:  bool  = False  # Bougie haussière
    is_bearish:  bool  = False  # Bougie baissière
    description: str   = ""


@dataclass
class Signal:
    """Signal complet avec rétrospective 3 étapes."""
    symbol:    str
    direction: str          # 'BUY', 'SELL', 'NEUTRAL'
    strength:  str = 'WEAK' # 'STRONG', 'NORMAL', 'WEAK'
    entry:     float = 0.0
    sl:        float = 0.0
    tp:        float = 0.0
    rr:        float = 0.0

    # Niveaux Volume Profile
    lvn_trigger: float = 0.0
    hvn_target:  float = 0.0
    poc:         float = 0.0
    vwap:        float = 0.0
    fisher:      float = 0.0

    # Rétrospective des 3 étapes
    step1: Optional[StepAnalysis] = None
    step2: Optional[StepAnalysis] = None
    step3: Optional[StepAnalysis] = None

    reason:      str  = ""
    invalidation:str  = ""   # Pourquoi le sens inverse est rejeté
    warnings:    list = field(default_factory=list)

    def is_valid(self) -> bool:
        return (self.direction in ('BUY', 'SELL')
                and self.rr >= MIN_RR
                and self.entry > 0
                and self.sl > 0
                and self.tp > 0)

    def retrospective(self) -> str:
        """Rétrospective des 3 étapes pour affichage/Telegram."""
        lines = [f"📋 RÉTROSPECTIVE 3 ÉTAPES — {self.symbol}",
                 "─" * 45]
        for step in [self.step1, self.step2, self.step3]:
            if step:
                icon = "1️⃣" if step.step == 1 else "2️⃣" if step.step == 2 else "3️⃣"
                lines.append(f"{icon} Étape {step.step} : {step.description}")
        lines += [
            "─" * 45,
            f"{'🟢 ACHAT' if self.direction == 'BUY' else '🔴 VENTE' if self.direction == 'SELL' else '⚪ NEUTRE'}",
        ]
        if self.direction != 'NEUTRAL':
            lines += [
                f"  Entrée : {self.entry:.4f}",
                f"  TP     : {self.tp:.4f}  (HVN cible)",
                f"  SL     : {self.sl:.4f}",
                f"  R/R    : 1:{self.rr:.2f}",
                f"  LVN ↯  : {self.lvn_trigger:.4f}",
                f"  Fisher : {self.fisher:.2f}",
            ]
            if self.invalidation:
                lines.append(f"  ✗ Sens inverse invalide : {self.invalidation}")
        return "\n".join(lines)

    def summary(self) -> str:
        return self.retrospective()


# ══════════════════════════════════════════════════════════════════
#  MOTEUR DE STRATÉGIE 3 ÉTAPES
# ══════════════════════════════════════════════════════════════════
class LVNStrategy:
    """
    Analyse séquentielle en 3 étapes M15 :
      - T-2 = Étape 1 : Identification LVN
      - T-1 = Étape 2 : Test du LVN
      - T   = Étape 3 : Confirmation (entrée)
    (T = dernière bougie FERMÉE)
    """

    LVN_PROXIMITY_PCT = 0.003   # 0.3% = "proche" d'un LVN
    HVN_PROXIMITY_PCT = 0.004   # 0.4% = "proche" d'un HVN

    def __init__(self, symbol: str):
        self.symbol = symbol

    # ──────────────────────────────────────────────────────────────
    def analyze(self, df: pd.DataFrame) -> Signal:
        signal = Signal(symbol=self.symbol, direction='NEUTRAL')

        if df is None or len(df) < 70:
            signal.reason = "Données insuffisantes (min 70 bougies)"
            return signal

        # Calcul des indicateurs sur tout le DataFrame
        df = calc_fisher(df)
        df = calc_vwap(df)
        df = calc_moving_averages(df)

        # Volume Profile sur les 200 dernières bougies
        vp = VolumeProfile(df).compute()
        signal.poc  = vp.poc or 0

        # ── 3 bougies clés (toutes FERMÉES) ──────────────────────
        # df.iloc[-1] = bougie en cours (NON fermée, ignorée)
        # df.iloc[-2] = T = Étape 3 (dernière clôture)
        # df.iloc[-3] = T-1 = Étape 2
        # df.iloc[-4] = T-2 = Étape 1
        if len(df) < 5:
            signal.reason = "Pas assez de bougies"
            return signal

        row_step3 = df.iloc[-2]   # Bougie confirmation (entrée)
        row_step2 = df.iloc[-3]   # Bougie test
        row_step1 = df.iloc[-4]   # Bougie identification

        # Volume moyen sur 20 bougies (pour qualifier un volume élevé)
        avg_vol = float(df['volume'].iloc[-22:-2].mean())

        # ── Analyse de chaque étape ───────────────────────────────
        step1 = self._analyze_step(1, row_step1, vp, avg_vol)
        step2 = self._analyze_step(2, row_step2, vp, avg_vol)
        step3 = self._analyze_step(3, row_step3, vp, avg_vol)

        signal.step1 = step1
        signal.step2 = step2
        signal.step3 = step3

        # Niveaux VP autour du prix de clôture (étape 3)
        cur_price = step3.close
        levels    = vp.nearest(cur_price)
        signal.vwap = step3.vwap

        lvn_above = levels['lvn_above']
        lvn_below = levels['lvn_below']
        hvn_above = levels['hvn_above']
        hvn_below = levels['hvn_below']

        # Fisher sur étape 3
        signal.fisher = step3.fisher
        fisher_cross  = fisher_crossover(df)

        # MA direction
        ma_fast = float(row_step3['ma_fast']) if not pd.isna(row_step3['ma_fast']) else 0
        ma_slow = float(row_step3['ma_slow']) if not pd.isna(row_step3['ma_slow']) else 0

        # ══════════════════════════════════════════════════════════
        #  VALIDATION SÉQUENTIELLE — SELL
        #  Étape 1 : Prix près d'un LVN (ou HVN résistance)
        #  Étape 2 : Prix teste/dépasse le LVN puis hésite ou rejette
        #  Étape 3 : Bougie bearish clôture SOUS le LVN
        # ══════════════════════════════════════════════════════════
        sell_valid, sell_lvn, sell_reason, sell_invalid = self._check_sell(
            step1, step2, step3, lvn_above, lvn_below, hvn_below,
            fisher_cross, ma_fast, ma_slow, avg_vol
        )

        if sell_valid and hvn_below:
            tp_price = hvn_below * 1.001
            sl_price = step3.high + (step3.high - step3.low) * 0.15
            sl_dist  = sl_price - cur_price
            tp_dist  = cur_price - tp_price

            if sl_dist > 0 and tp_dist > 0:
                rr = round(tp_dist / sl_dist, 2)
                if rr >= MIN_RR:
                    signal.direction   = 'SELL'
                    signal.strength    = ('STRONG' if fisher_cross == 'SELL' and step3.is_bearish
                                          else 'NORMAL')
                    signal.entry       = cur_price
                    signal.sl          = round(sl_price, 6)
                    signal.tp          = round(tp_price, 6)
                    signal.rr          = rr
                    signal.lvn_trigger = sell_lvn
                    signal.hvn_target  = hvn_below
                    signal.reason      = sell_reason
                    signal.invalidation= (f"BUY invalidé : prix a clôturé SOUS le LVN "
                                          f"{sell_lvn:.4f} → biais baissier confirmé")
                    return signal

        # ══════════════════════════════════════════════════════════
        #  VALIDATION SÉQUENTIELLE — BUY
        #  Étape 1 : Prix près d'un LVN
        #  Étape 2 : Prix teste le LVN par le bas / rebond
        #  Étape 3 : Bougie bullish clôture AU-DESSUS du LVN
        # ══════════════════════════════════════════════════════════
        buy_valid, buy_lvn, buy_reason, buy_invalid = self._check_buy(
            step1, step2, step3, lvn_above, lvn_below, hvn_above,
            fisher_cross, ma_fast, ma_slow, avg_vol
        )

        if buy_valid and hvn_above:
            tp_price = hvn_above * 0.999
            sl_price = step3.low - (step3.high - step3.low) * 0.15
            sl_dist  = cur_price - sl_price
            tp_dist  = tp_price - cur_price

            if sl_dist > 0 and tp_dist > 0:
                rr = round(tp_dist / sl_dist, 2)
                if rr >= MIN_RR:
                    signal.direction   = 'BUY'
                    signal.strength    = ('STRONG' if fisher_cross == 'BUY' and step3.is_bullish
                                          else 'NORMAL')
                    signal.entry       = cur_price
                    signal.sl          = round(sl_price, 6)
                    signal.tp          = round(tp_price, 6)
                    signal.rr          = rr
                    signal.lvn_trigger = buy_lvn
                    signal.hvn_target  = hvn_above
                    signal.reason      = buy_reason
                    signal.invalidation= (f"SELL invalidé : prix a REBONDI sur le LVN "
                                          f"{buy_lvn:.4f} → biais haussier confirmé")
                    return signal

        # Neutre
        signal.reason = (
            f"⚪ NEUTRE — Séquence 3 étapes non confirmée\n"
            f"  Étape 1 : {step1.description}\n"
            f"  Étape 2 : {step2.description}\n"
            f"  Étape 3 : {step3.description}\n"
            f"  LVN↑: {lvn_above:.4f if lvn_above else 'N/A'} | "
            f"LVN↓: {lvn_below:.4f if lvn_below else 'N/A'}\n"
            f"  Attendre la prochaine clôture M15"
        )
        return signal

    # ──────────────────────────────────────────────────────────────
    def _analyze_step(self, n: int, row: pd.Series,
                      vp: VolumeProfile, avg_vol: float) -> StepAnalysis:
        """Analyse une bougie individuelle."""
        o = float(row['open'])
        h = float(row['high'])
        l = float(row['low'])
        c = float(row['close'])
        v = float(row['volume']) if 'volume' in row else 0
        f = float(row['fisher']) if not pd.isna(row.get('fisher', np.nan)) else 0
        vwap = float(row['vwap']) if not pd.isna(row.get('vwap', np.nan)) else 0

        body      = abs(c - o)
        bar_range = h - l
        wick_top  = h - max(o, c)
        wick_bot  = min(o, c) - l
        density   = vp.density_at(c)

        # Proximité LVN / HVN
        levels    = vp.nearest(c)
        lvn_above = levels['lvn_above']
        lvn_below = levels['lvn_below']
        near_lvn  = False
        lvn_level = 0.0
        if lvn_above and abs(c - lvn_above) / lvn_above < self.LVN_PROXIMITY_PCT:
            near_lvn  = True
            lvn_level = lvn_above
        if lvn_below and abs(c - lvn_below) / lvn_below < self.LVN_PROXIMITY_PCT:
            near_lvn  = True
            lvn_level = lvn_below

        hvn_above = levels['hvn_above']
        hvn_below = levels['hvn_below']
        near_hvn  = False
        if hvn_above and abs(c - hvn_above) / hvn_above < self.HVN_PROXIMITY_PCT:
            near_hvn = True
        if hvn_below and abs(c - hvn_below) / hvn_below < self.HVN_PROXIMITY_PCT:
            near_hvn = True

        # Description de l'étape
        descs = []
        if n == 1:
            if near_lvn:
                descs.append(f"Prix proche LVN {lvn_level:.4f}")
            elif near_hvn:
                descs.append("Prix dans zone HVN (fort volume)")
            else:
                descs.append(f"Prix={c:.4f}, density={density:.0%}")
            descs.append(f"Fisher={f:.2f}")

        elif n == 2:
            if wick_top > body * 0.7:
                descs.append("Mèche haute dominante → test résistance/rejet")
            elif wick_bot > body * 0.7:
                descs.append("Mèche basse dominante → test support/rebond")
            else:
                descs.append("Corps normal" + (" haussier" if c > o else " baissier"))
            if v < avg_vol * 0.7:
                descs.append("Volume faible (hésitation)")
            elif v > avg_vol * 1.3:
                descs.append("Volume élevé (conviction)")

        elif n == 3:
            if c > o:
                descs.append("✅ Bougie HAUSSIÈRE (clôture confirmation)")
            else:
                descs.append("✅ Bougie BAISSIÈRE (clôture confirmation)")
            descs.append(f"Clôture={c:.4f} | VP density={density:.0%}")
            if vwap > 0:
                descs.append(f"{'au-dessus' if c > vwap else 'en-dessous'} VWAP {vwap:.4f}")

        return StepAnalysis(
            step        = n,
            open        = o, high=h, low=l, close=c,
            volume      = v,
            fisher      = f,
            vwap        = vwap,
            vp_density  = density,
            near_lvn    = near_lvn,
            near_hvn    = near_hvn,
            lvn_level   = lvn_level,
            is_bullish  = c > o,
            is_bearish  = c < o,
            description = " | ".join(descs)
        )

    # ──────────────────────────────────────────────────────────────
    def _check_sell(self, s1, s2, s3,
                    lvn_above, lvn_below, hvn_below,
                    fisher_cross, ma_fast, ma_slow, avg_vol):
        """
        Valide le scénario SELL en 3 étapes.
        Retourne (valid, lvn_level, reason, invalidation_of_buy)
        """
        lvn = lvn_above or lvn_below
        if not lvn:
            return False, 0, "", ""

        # Étape 1 : Prix était PRÈS d'un LVN ou d'un HVN résistance
        step1_ok = s1.near_lvn or s1.near_hvn or s1.vp_density >= 0.65

        # Étape 2 : Test — prix a tenté de monter/rester au niveau
        #   → mèche haute (rejet), ou volume faible, ou stagnation
        bar_range2 = s2.high - s2.low
        wick_top2  = s2.high - max(s2.open, s2.close)
        step2_rejection = (wick_top2 > bar_range2 * 0.4)   # Mèche haute ≥ 40%
        step2_weak_vol  = (s2.volume < avg_vol * 0.85)
        step2_bearish   = s2.is_bearish
        step2_ok = step2_rejection or step2_weak_vol or step2_bearish

        # Étape 3 : CONFIRMATION — bougie bearish, clôture SOUS le LVN
        step3_bearish_close = s3.is_bearish and s3.close < lvn
        step3_low_density   = s3.vp_density <= 0.40    # Clôture dans zone LVN/vide
        step3_below_vwap    = s3.vwap > 0 and s3.close < s3.vwap
        step3_ok = step3_bearish_close or (step3_low_density and s3.is_bearish)

        if not (step1_ok and step2_ok and step3_ok):
            return False, 0, "", ""

        # Confirmations supplémentaires
        confirms = []
        if step3_bearish_close:
            confirms.append(f"Clôture bearish sous LVN {lvn:.4f}")
        if step2_rejection:
            confirms.append("Rejet mèche étape 2")
        if step3_below_vwap:
            confirms.append(f"Sous VWAP {s3.vwap:.4f}")
        if fisher_cross in ('SELL', 'SELL_WEAK'):
            confirms.append(f"Fisher ↘ {s3.fisher:.2f}")
        if ma_fast < ma_slow:
            confirms.append(f"MA30<MA60 bearish")

        reason = (
            f"Séquence 3 étapes SELL validée\n"
            f"  Étape 1 : {s1.description}\n"
            f"  Étape 2 : {s2.description}\n"
            f"  Étape 3 : {s3.description}\n"
            f"  Confirmations : {' | '.join(confirms)}"
        )
        invalid = (f"BUY invalidé → clôture E3 ({s3.close:.4f}) "
                   f"sous LVN ({lvn:.4f}) + rejet E2")

        return True, lvn, reason, invalid

    # ──────────────────────────────────────────────────────────────
    def _check_buy(self, s1, s2, s3,
                   lvn_above, lvn_below, hvn_above,
                   fisher_cross, ma_fast, ma_slow, avg_vol):
        """Valide le scénario BUY en 3 étapes."""
        lvn = lvn_below or lvn_above
        if not lvn:
            return False, 0, "", ""

        step1_ok = s1.near_lvn or s1.near_hvn or s1.vp_density >= 0.65

        bar_range2 = s2.high - s2.low
        wick_bot2  = min(s2.open, s2.close) - s2.low
        step2_bounce    = (wick_bot2 > bar_range2 * 0.4)
        step2_weak_vol  = (s2.volume < avg_vol * 0.85)
        step2_bullish   = s2.is_bullish
        step2_ok = step2_bounce or step2_weak_vol or step2_bullish

        step3_bullish_close = s3.is_bullish and s3.close > lvn
        step3_low_density   = s3.vp_density <= 0.40
        step3_above_vwap    = s3.vwap > 0 and s3.close > s3.vwap
        step3_ok = step3_bullish_close or (step3_low_density and s3.is_bullish)

        if not (step1_ok and step2_ok and step3_ok):
            return False, 0, "", ""

        confirms = []
        if step3_bullish_close:
            confirms.append(f"Clôture bullish au-dessus LVN {lvn:.4f}")
        if step2_bounce:
            confirms.append("Rebond mèche étape 2")
        if step3_above_vwap:
            confirms.append(f"Au-dessus VWAP {s3.vwap:.4f}")
        if fisher_cross in ('BUY', 'BUY_WEAK'):
            confirms.append(f"Fisher ↗ {s3.fisher:.2f}")
        if ma_fast > ma_slow:
            confirms.append(f"MA30>MA60 bullish")

        reason = (
            f"Séquence 3 étapes BUY validée\n"
            f"  Étape 1 : {s1.description}\n"
            f"  Étape 2 : {s2.description}\n"
            f"  Étape 3 : {s3.description}\n"
            f"  Confirmations : {' | '.join(confirms)}"
        )
        invalid = (f"SELL invalidé → clôture E3 ({s3.close:.4f}) "
                   f"au-dessus LVN ({lvn:.4f}) + rebond E2")

        return True, lvn, reason, invalid
