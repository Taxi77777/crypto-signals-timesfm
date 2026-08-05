"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.0 — strategy.py                ║
║                                                                  ║
║  PIPELINE COMPLET (dans l'ordre) :                              ║
║                                                                  ║
║  ÉTAPE 1 — CARNET D'ORDRES MULTI-EXCHANGE                       ║
║    6 exchanges × 50 niveaux → qui achète/vend vraiment ?        ║
║    Stacked Imbalances : niveaux consécutifs du même côté        ║
║    CVD : acheteurs ou vendeurs agressifs ?                       ║
║                                                                  ║
║  ÉTAPE 2 — CONSENSUS 80%+ OBLIGATOIRE                           ║
║    Au moins 5/6 exchanges dans la MÊME direction                ║
║    Si 1 exchange fortement opposé → MANIPULATION détectée       ║
║                                                                  ║
║  ÉTAPE 3 — GOOGLE TIMESFM : JUGE FINAL                         ║
║    Reçoit : prix historiques + données carnet d'ordres          ║
║    Prédit les 10 prochaines bougies 4H (~40 heures)             ║
║    → Si TimesFM CONFIRME la direction du consensus : TRADE      ║
║    → Si TimesFM contredit : PAS DE TRADE (trop risqué)         ║
║    → Si TimesFM non disponible : trade si consensus >=90%       ║
║                                                                  ║
║  RÉSULTAT : Seulement les trades où IA + marché = ACCORD        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import pandas as pd
from dataclasses import dataclass, field
from exchanges import get_multi_exchange_orderflow
from indicators import calc_trend_indicators, get_trend_bias
from timesfm_predictor import get_timesfm_verdict
from config import (
    EXCHANGES_TO_CHECK, MIN_CONSENSUS_PCT, ANTI_MANIP_THRESHOLD,
    MIN_RR, ORDERBOOK_DEPTH, ATR_SL_MULT, ATR_TP_MULT,
    USE_TREND_FILTER, MIN_STACKED_LEVELS, USE_SL,
)


@dataclass
class Signal:
    """Signal de trading — validé par consensus exchanges + Google TimesFM."""
    symbol:              str

    direction:           str   = 'NEUTRAL'
    strength:            str   = 'WEAK'
    entry:               float = 0.0
    sl:                  float = 0.0
    tp:                  float = 0.0
    rr:                  float = 0.0

    # Carnet d'ordres
    consensus_pct:       float = 0.0
    avg_direction_score: float = 0.0
    avg_stacked_buy:     float = 0.0
    avg_stacked_sell:    float = 0.0
    exchanges_buy:       int   = 0
    exchanges_sell:      int   = 0
    exchanges_ok:        int   = 0
    manipulation_flag:   bool  = False
    manipulation_detail: str   = ''

    # Google TimesFM
    timesfm_direction:   str   = 'NEUTRAL'
    timesfm_confidence:  float = 0.0
    timesfm_change_pct:  float = 0.0
    timesfm_available:   bool  = False
    timesfm_approved:    bool  = False   # True = TimesFM a donné le feu vert

    # Tendance 4H (informatif)
    trend_bias:          str   = 'NEUTRAL'
    vwap:                float = 0.0
    ema_fast:            float = 0.0
    ema_slow:            float = 0.0
    atr:                 float = 0.0
    rsi:                 float = 50.0

    reason:              str   = ''
    details:             dict  = field(default_factory=dict)
    warnings:            list  = field(default_factory=list)

    def is_valid(self) -> bool:
        """
        Trade valide si :
        - Direction claire (BUY ou SELL)
        - Pas de manipulation détectée
        - Consensus >= seuil (80%)
        - R/R minimum respecté
        - Google TimesFM a donné le feu vert (ou consensus >= 90% si TimesFM indisponible)
        """
        return (
            self.direction in ('BUY', 'SELL')
            and not self.manipulation_flag
            and self.consensus_pct >= MIN_CONSENSUS_PCT
            and self.rr >= MIN_RR
            and self.entry > 0
            and (not USE_SL or self.sl > 0)
            and self.tp > 0
            and self.timesfm_approved      # ← TimesFM doit avoir approuvé
        )

    def summary(self) -> str:
        arrow = "[BUY ✅]" if self.direction == 'BUY' else "[SELL ✅]" if self.direction == 'SELL' else "[NEUTRE]"
        tfm_status = "FEU VERT" if self.timesfm_approved else "EN ATTENTE"
        return (
            f"\n{'='*65}\n"
            f"  {arrow} {self.symbol}\n"
            f"  Consensus : {self.consensus_pct:.0f}% ({self.exchanges_buy}B/{self.exchanges_sell}S/{self.exchanges_ok} exchanges)\n"
            f"  TimesFM   : {self.timesfm_direction} ({self.timesfm_change_pct:+.2f}%) → {tfm_status}\n"
            f"  Tendance  : {self.trend_bias} | RSI:{self.rsi:.0f} | ATR:{self.atr:.4f}\n"
            f"  Entry:{self.entry:.4f}  SL:{self.sl:.4f}  TP:{self.tp:.4f}  R/R:1:{self.rr:.2f}\n"
            f"  Valide: {'OUI - TRADE LANCE' if self.is_valid() else 'NON'}\n"
            f"  {self.reason}\n"
            f"{'='*65}"
        )


class OrderFlowStrategy:
    """
    Stratégie institutionnelle :
    Carnet d'ordres 80%+ consensus → Google TimesFM juge final → TRADE
    """

    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyze(self, df_klines: pd.DataFrame) -> Signal:
        signal = Signal(symbol=self.symbol)

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 1 : CARNET D'ORDRES — 6 EXCHANGES EN PARALLÈLE
        # ══════════════════════════════════════════════════════════
        of = get_multi_exchange_orderflow(
            self.symbol, EXCHANGES_TO_CHECK, ORDERBOOK_DEPTH
        )

        signal.exchanges_ok        = of['exchanges_ok']
        signal.consensus_pct       = of['consensus_pct']
        signal.avg_direction_score = of['avg_direction_score']
        signal.avg_stacked_buy     = of['avg_stacked_buy']
        signal.avg_stacked_sell    = of['avg_stacked_sell']
        signal.exchanges_buy       = of['book_buy']
        signal.exchanges_sell      = of['book_sell']
        signal.details             = of['details']

        # Minimum 3 exchanges connectés pour analyser
        if of['exchanges_ok'] < 3:
            signal.reason = f"Pas assez d'exchanges ({of['exchanges_ok']}/6)"
            return signal

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 2 : CONSENSUS 80%+ SUR TOUTES LES PLATEFORMES
        # ══════════════════════════════════════════════════════════
        consensus_dir = of['consensus_direction']
        consensus_pct = of['consensus_pct']

        if consensus_pct < MIN_CONSENSUS_PCT:
            signal.reason = (
                f"[NEUTRE] Consensus insuffisant : {consensus_pct:.0f}% "
                f"(requis {MIN_CONSENSUS_PCT:.0f}%) | "
                f"Carnet: {of['book_buy']}B/{of['book_sell']}S | "
                f"CVD: {of['cvd_buy']}B/{of['cvd_sell']}S"
            )
            return signal

        # ══════════════════════════════════════════════════════════
        # DÉTECTION MANIPULATION : 1 exchange fortement opposé ?
        # ══════════════════════════════════════════════════════════
        for ex, d in of['details'].items():
            if not d.get('ok'):
                continue
            ex_side  = d.get('dominant_side', 'NEUTRAL')
            ex_score = d.get('direction_score', 0)

            if consensus_dir == 'BUY' and ex_side == 'SELL' and ex_score < -ANTI_MANIP_THRESHOLD:
                signal.manipulation_flag   = True
                signal.manipulation_detail = f"{ex} dit SELL fort ({ex_score:.0f}) contre consensus BUY"
                signal.warnings.append(f"MANIPULATION? {ex}")
                d['manipulation_suspect']  = True

            elif consensus_dir == 'SELL' and ex_side == 'BUY' and ex_score > ANTI_MANIP_THRESHOLD:
                signal.manipulation_flag   = True
                signal.manipulation_detail = f"{ex} dit BUY fort ({ex_score:.0f}) contre consensus SELL"
                signal.warnings.append(f"MANIPULATION? {ex}")
                d['manipulation_suspect']  = True

        if signal.manipulation_flag:
            signal.reason = f"[STOP] {signal.manipulation_detail}"
            return signal

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 3 : TENDANCE 4H (informatif pour les logs)
        # ══════════════════════════════════════════════════════════
        df      = calc_trend_indicators(df_klines)
        ti      = get_trend_bias(df)
        price   = ti['price']
        atr     = ti['atr'] if ti['atr'] > 0 else price * 0.015

        signal.trend_bias = ti['bias']
        signal.entry      = price
        signal.vwap       = ti['vwap']
        signal.ema_fast   = ti['ema_fast']
        signal.ema_slow   = ti['ema_slow']
        signal.atr        = atr
        signal.rsi        = ti['rsi']

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 4 : CALCUL SL / TP / R/R
        # ══════════════════════════════════════════════════════════
        if consensus_dir == 'BUY':
            sl_price = price - (atr * ATR_SL_MULT)
            tp_price = price + (atr * ATR_TP_MULT)
        else:  # SELL
            sl_price = price + (atr * ATR_SL_MULT)
            tp_price = price - (atr * ATR_TP_MULT)

        sl_dist = abs(price - sl_price)
        tp_dist = abs(tp_price - price)
        rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        if rr < MIN_RR:
            signal.reason = (
                f"[STOP] R/R insuffisant : 1:{rr:.2f} (requis 1:{MIN_RR}) | "
                f"ATR:{atr:.4f} | Consensus:{consensus_pct:.0f}%"
            )
            return signal

        signal.direction = consensus_dir
        signal.sl        = round(sl_price, 6) if USE_SL else 0.0
        signal.tp        = round(tp_price, 6)
        signal.rr        = rr

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 5 : GOOGLE TIMESFM — JUGE FINAL
        # ══════════════════════════════════════════════════════════
        #
        # TimesFM reçoit les données de prix historiques et prédit
        # les 10 prochaines bougies. Si sa prédiction CONFIRME
        # la direction du consensus → FEU VERT → TRADE LANCÉ
        #
        # Règles :
        #  a) TimesFM disponible + confirme direction → APPROUVÉ ✅
        #  b) TimesFM disponible + neutre             → APPROUVÉ ✅ (pas d'opposition)
        #  c) TimesFM disponible + contredit          → REFUSÉ ❌
        #  d) TimesFM non disponible + consensus ≥90% → APPROUVÉ ✅ (signal très fort)
        #  e) TimesFM non disponible + consensus <90% → REFUSÉ ❌ (trop incertain)
        # ══════════════════════════════════════════════════════════
        # ── COMMUNICATION EXCHANGES → TIMESFM ──────────────────────
        # On transmet TOUTES les données des 6 plateformes à TimesFM
        # pour qu'il les combine avec sa prédiction de prix
        timesfm = get_timesfm_verdict(df_klines, self.symbol, exchange_data=of)

        signal.timesfm_direction  = timesfm['direction']
        signal.timesfm_confidence = timesfm['confidence']
        signal.timesfm_change_pct = timesfm.get('predicted_change_pct', 0.0)
        signal.timesfm_available  = timesfm['available']

        tfm_dir  = timesfm['direction']
        tfm_conf = timesfm['confidence']
        tfm_ok   = timesfm['available']

        if tfm_ok:
            # TimesFM est disponible
            if tfm_dir == consensus_dir:
                # ✅ CAS A : TimesFM CONFIRME — feu vert
                signal.timesfm_approved = True
                signal.strength = 'STRONG'
                signal.reason = (
                    f"[APPROUVE] Consensus {consensus_dir} {consensus_pct:.0f}% "
                    f"({of['book_buy' if consensus_dir == 'BUY' else 'book_sell']}/{of['exchanges_ok']} exchanges) | "
                    f"TimesFM CONFIRME {tfm_dir} (Δ{signal.timesfm_change_pct:+.2f}%, conf={tfm_conf:.0%}) | "
                    f"Tendance 4H: {ti['bias']} | RSI:{ti['rsi']:.0f} | "
                    f"Stacked: {of['avg_stacked_buy' if consensus_dir=='BUY' else 'avg_stacked_sell']:.1f} niveaux"
                )

            elif tfm_dir == 'NEUTRAL':
                # ✅ CAS B : TimesFM neutre — pas d'opposition, on trade
                signal.timesfm_approved = True
                signal.strength = 'NORMAL'
                signal.reason = (
                    f"[APPROUVE] Consensus {consensus_dir} {consensus_pct:.0f}% "
                    f"({of['book_buy' if consensus_dir=='BUY' else 'book_sell']}/{of['exchanges_ok']} exchanges) | "
                    f"TimesFM: NEUTRE (pas d'opposition) | "
                    f"Tendance 4H: {ti['bias']} | ATR:{atr:.4f}"
                )

            else:
                # ❌ CAS C : TimesFM CONTREDIT — on ne trade pas
                signal.timesfm_approved = False
                signal.direction = 'NEUTRAL'   # annule le signal
                signal.reason = (
                    f"[REFUSE PAR TIMESFM] Carnet: {consensus_dir} {consensus_pct:.0f}% MAIS "
                    f"TimesFM predit {tfm_dir} (Δ{signal.timesfm_change_pct:+.2f}%, conf={tfm_conf:.0%}) — "
                    f"Contradiction IA vs Carnet — PAS DE TRADE"
                )
        else:
            # TimesFM non disponible (en cours de telechargement)
            if consensus_pct >= 90.0:
                # ✅ CAS D : Signal très fort (90%+) → on trade même sans TimesFM
                signal.timesfm_approved = True
                signal.strength = 'STRONG'
                signal.reason = (
                    f"[APPROUVE] Consensus TRES FORT {consensus_dir} {consensus_pct:.0f}% "
                    f"({of['book_buy' if consensus_dir=='BUY' else 'book_sell']}/{of['exchanges_ok']} exchanges) | "
                    f"TimesFM: en cours de chargement | "
                    f"Tendance 4H: {ti['bias']}"
                )
            else:
                # ❌ CAS E : Consensus pas assez fort sans TimesFM → on attend
                signal.timesfm_approved = False
                signal.reason = (
                    f"[EN ATTENTE TIMESFM] Consensus {consensus_dir} {consensus_pct:.0f}% ok MAIS "
                    f"TimesFM pas encore disponible — besoin de {90.0:.0f}%+ pour trader sans IA "
                    f"(actuellement {consensus_pct:.0f}%)"
                )

        return signal
