"""
╔══════════════════════════════════════════════════════════════════╗
║     INSTITUTIONAL HUNTER PRO v5.1 — strategy.py                 ║
║                                                                  ║
║  PIPELINE — L'ORDRE EST STRICT :                                ║
║                                                                  ║
║  ETAPE 1 — DESEQUILIBRE PRIX + VOLUME SUR LES 6 EXCHANGES       ║
║    6 exchanges x 50 niveaux de carnet.                          ║
║    Desequilibre de VOLUME niveau par niveau (ratio bid/ask).    ║
║    Stacked Imbalances : blocs consecutifs du meme cote.         ║
║    CVD : volume des acheteurs vs vendeurs agressifs.            ║
║                                                                  ║
║  ETAPE 2 — CONSENSUS OBLIGATOIRE                                ║
║    Si le consensus est insuffisant : ARRET IMMEDIAT.            ║
║    TimesFM n'est meme pas appele. On ne derange pas l'IA         ║
║    tant que le marche n'a pas parle.                            ║
║                                                                  ║
║  ETAPE 3 — DETECTION DE MANIPULATION                            ║
║    Un exchange fortement oppose au consensus = piege.           ║
║                                                                  ║
║  ETAPE 4 — SL / TP / RATIO R/R                                  ║
║                                                                  ║
║  ETAPE 5 — GOOGLE TIMESFM, JUGE FINAL                           ║
║    Recoit les prix 4H ET toutes les metriques des 6 exchanges.  ║
║    Doit CONFIRMER la direction ET atteindre une confiance       ║
║    minimale, sinon le trade est refuse.                         ║
║                                                                  ║
║  CORRECTIFS v5.1 :                                              ║
║  • TIMESFM_STRICT : NEUTRAL ne vaut plus approbation tacite.    ║
║  • La confiance de TimesFM est enfin VERIFIEE. Le bonus issu    ║
║    du consensus / Stacked / CVD etait calcule puis ignore :     ║
║    les donnees des 6 exchanges n'avaient aucun effet reel.      ║
║  • Refus si les metriques exchanges n'ont pas ete transmises.   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import logging
import pandas as pd
from dataclasses import dataclass, field
from exchanges import get_multi_exchange_orderflow
from indicators import calc_trend_indicators, get_trend_bias
from timesfm_predictor import get_timesfm_verdict
from config import (
    EXCHANGES_TO_CHECK, MIN_CONSENSUS_PCT, ANTI_MANIP_THRESHOLD,
    MIN_RR, ORDERBOOK_DEPTH, ATR_SL_MULT, ATR_TP_MULT,
    USE_TREND_FILTER, MIN_STACKED_LEVELS, USE_SL,
    MIN_EXCHANGES_OK, TIMESFM_STRICT, TIMESFM_MIN_CONFIDENCE,
    TIMESFM_REQUIRE_EXCHANGE_DATA,
)

log = logging.getLogger("IHP-STRATEGY")


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
    timesfm_approved:    bool  = False
    timesfm_used_book:   bool  = False   # métriques exchanges bien transmises ?

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
        Trade valide si TOUTES ces conditions sont réunies :
          - direction claire (BUY ou SELL)
          - aucune manipulation détectée
          - consensus >= seuil
          - R/R minimum respecté
          - SL présent si USE_SL
          - TP présent
          - TimesFM a donné son feu vert
          - les métriques des 6 exchanges ont bien été transmises à TimesFM
        """
        return (
            self.direction in ('BUY', 'SELL')
            and not self.manipulation_flag
            and self.consensus_pct >= MIN_CONSENSUS_PCT
            and self.rr >= MIN_RR
            and self.entry > 0
            and (not USE_SL or self.sl > 0)
            and self.tp > 0
            and self.timesfm_approved
            and (not TIMESFM_REQUIRE_EXCHANGE_DATA or self.timesfm_used_book)
        )

    def summary(self) -> str:
        arrow = "[BUY]" if self.direction == 'BUY' else "[SELL]" if self.direction == 'SELL' else "[NEUTRE]"
        tfm_status = "FEU VERT" if self.timesfm_approved else "REFUS"
        sl_str = f"{self.sl:.6f}" if self.sl > 0 else "aucun"
        return (
            f"\n{'='*65}\n"
            f"  {arrow} {self.symbol}\n"
            f"  Consensus : {self.consensus_pct:.0f}% ({self.exchanges_buy}B/{self.exchanges_sell}S sur {self.exchanges_ok} exchanges)\n"
            f"  Stacked   : BUY {self.avg_stacked_buy:.1f} / SELL {self.avg_stacked_sell:.1f} niveaux\n"
            f"  TimesFM   : {self.timesfm_direction} ({self.timesfm_change_pct:+.2f}%) "
            f"conf={self.timesfm_confidence:.0%} -> {tfm_status}\n"
            f"  Donnees exchanges transmises a l'IA : {'OUI' if self.timesfm_used_book else 'NON'}\n"
            f"  Tendance  : {self.trend_bias} | RSI:{self.rsi:.0f} | ATR:{self.atr:.6f}\n"
            f"  Entry:{self.entry:.6f}  SL:{sl_str}  TP:{self.tp:.6f}  R/R:1:{self.rr:.2f}\n"
            f"  Valide: {'OUI' if self.is_valid() else 'NON'}\n"
            f"  {self.reason}\n"
            f"{'='*65}"
        )


class OrderFlowStrategy:
    """
    Déséquilibre prix + volume sur 6 exchanges -> consensus -> Google TimesFM -> trade.
    """

    def __init__(self, symbol: str):
        self.symbol = symbol

    def analyze(self, df_klines: pd.DataFrame) -> Signal:
        signal = Signal(symbol=self.symbol)

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 1 : DÉSÉQUILIBRE PRIX + VOLUME — 6 EXCHANGES
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

        if of['exchanges_ok'] < MIN_EXCHANGES_OK:
            signal.reason = (
                f"[STOP] Pas assez d'exchanges joignables : "
                f"{of['exchanges_ok']}/{len(EXCHANGES_TO_CHECK)} (minimum {MIN_EXCHANGES_OK})"
            )
            return signal

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 2 : CONSENSUS — TimesFM N'EST PAS APPELÉ AVANT
        # ══════════════════════════════════════════════════════════
        consensus_dir = of['consensus_direction']
        consensus_pct = of['consensus_pct']

        if consensus_dir == 'NEUTRAL' or consensus_pct < MIN_CONSENSUS_PCT:
            signal.reason = (
                f"[NEUTRE] Desequilibre insuffisant : {consensus_pct:.0f}% "
                f"(requis {MIN_CONSENSUS_PCT:.0f}%) | "
                f"Carnet: {of['book_buy']}B/{of['book_sell']}S sur {of['exchanges_ok']} | "
                f"CVD: {of['cvd_buy']}B/{of['cvd_sell']}S | "
                f"score={of['avg_direction_score']:+.0f}"
            )
            return signal

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 3 : DÉTECTION DE MANIPULATION
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
        # ÉTAPE 4 : TENDANCE 4H + SL / TP / R/R
        # ══════════════════════════════════════════════════════════
        df    = calc_trend_indicators(df_klines)
        ti    = get_trend_bias(df)
        price = ti['price']
        atr   = ti['atr'] if ti['atr'] > 0 else price * 0.015

        signal.trend_bias = ti['bias']
        signal.entry      = price
        signal.vwap       = ti['vwap']
        signal.ema_fast   = ti['ema_fast']
        signal.ema_slow   = ti['ema_slow']
        signal.atr        = atr
        signal.rsi        = ti['rsi']

        if consensus_dir == 'BUY':
            sl_price = price - (atr * ATR_SL_MULT)
            tp_price = price + (atr * ATR_TP_MULT)
        else:
            sl_price = price + (atr * ATR_SL_MULT)
            tp_price = price - (atr * ATR_TP_MULT)

        sl_dist = abs(price - sl_price)
        tp_dist = abs(tp_price - price)
        rr      = round(tp_dist / sl_dist, 2) if sl_dist > 0 else 0

        if rr < MIN_RR:
            signal.reason = (
                f"[STOP] R/R insuffisant : 1:{rr:.2f} (requis 1:{MIN_RR}) | ATR:{atr:.6f}"
            )
            return signal

        signal.direction = consensus_dir
        signal.sl        = round(sl_price, 6) if USE_SL else 0.0
        signal.tp        = round(tp_price, 6)
        signal.rr        = rr

        # ══════════════════════════════════════════════════════════
        # ÉTAPE 5 : GOOGLE TIMESFM — JUGE FINAL
        #
        # Le dict `of` complet est transmis : consensus, direction,
        # Stacked Imbalances moyens, CVD, score directionnel, nombre
        # d'exchanges valides. TimesFM combine ces métriques avec sa
        # prédiction de prix pour produire sa confiance finale.
        # ══════════════════════════════════════════════════════════
        log.info(
            f"[{self.symbol}] Desequilibre confirme ({consensus_dir} {consensus_pct:.0f}%) "
            f"-> transmission des metriques des {of['exchanges_ok']} exchanges a Google TimesFM"
        )

        timesfm = get_timesfm_verdict(df_klines, self.symbol, exchange_data=of)

        signal.timesfm_direction  = timesfm['direction']
        signal.timesfm_confidence = timesfm['confidence']
        signal.timesfm_change_pct = timesfm.get('predicted_change_pct', 0.0)
        signal.timesfm_available  = timesfm['available']
        signal.timesfm_used_book  = timesfm.get('exchange_data_used', False)

        tfm_dir  = timesfm['direction']
        tfm_conf = timesfm['confidence']
        tfm_ok   = timesfm['available']

        # Les métriques exchanges doivent avoir atteint le modèle
        if TIMESFM_REQUIRE_EXCHANGE_DATA and not signal.timesfm_used_book:
            signal.timesfm_approved = False
            signal.direction = 'NEUTRAL'
            signal.reason = (
                "[REFUSE] Les metriques des 6 exchanges n'ont pas ete transmises a TimesFM — "
                "decision a l'aveugle refusee"
            )
            return signal

        if not tfm_ok:
            # ❌ TimesFM indisponible. En mode strict, aucun trade sans IA.
            signal.timesfm_approved = False
            signal.direction = 'NEUTRAL'
            signal.reason = (
                f"[REFUSE] Google TimesFM indisponible — accord de l'IA obligatoire "
                f"(consensus {consensus_dir} {consensus_pct:.0f}% ignore)"
            )
            return signal

        if tfm_dir == consensus_dir:
            # ✅ L'IA confirme la direction du desequilibre
            if tfm_conf < TIMESFM_MIN_CONFIDENCE:
                signal.timesfm_approved = False
                signal.direction = 'NEUTRAL'
                signal.reason = (
                    f"[REFUSE] TimesFM confirme {tfm_dir} mais confiance trop faible : "
                    f"{tfm_conf:.0%} < {TIMESFM_MIN_CONFIDENCE:.0%} "
                    f"(Δ{signal.timesfm_change_pct:+.2f}%)"
                )
                return signal

            signal.timesfm_approved = True
            signal.strength = 'STRONG'
            signal.reason = (
                f"[APPROUVE] Desequilibre {consensus_dir} {consensus_pct:.0f}% sur "
                f"{of['exchanges_ok']} exchanges | "
                f"Stacked {of['avg_stacked_buy' if consensus_dir=='BUY' else 'avg_stacked_sell']:.1f} niveaux | "
                f"CVD {of['cvd_buy']}B/{of['cvd_sell']}S | "
                f"TimesFM CONFIRME {tfm_dir} (Δ{signal.timesfm_change_pct:+.2f}%, conf={tfm_conf:.0%}) | "
                f"Tendance 4H: {ti['bias']} | RSI:{ti['rsi']:.0f}"
            )
            return signal

        if tfm_dir == 'NEUTRAL':
            if TIMESFM_STRICT:
                # ❌ Mode strict : pas d'accord explicite = pas de trade
                signal.timesfm_approved = False
                signal.direction = 'NEUTRAL'
                signal.reason = (
                    f"[REFUSE PAR TIMESFM] Desequilibre {consensus_dir} {consensus_pct:.0f}% "
                    f"mais l'IA reste NEUTRE (Δ{signal.timesfm_change_pct:+.2f}%) — "
                    f"accord explicite obligatoire"
                )
                return signal

            # Mode permissif (historique) : neutre = pas d'opposition
            signal.timesfm_approved = True
            signal.strength = 'NORMAL'
            signal.reason = (
                f"[APPROUVE] Desequilibre {consensus_dir} {consensus_pct:.0f}% | "
                f"TimesFM NEUTRE (pas d'opposition) | Tendance 4H: {ti['bias']}"
            )
            return signal

        # ❌ Contradiction franche entre l'IA et le carnet
        signal.timesfm_approved = False
        signal.direction = 'NEUTRAL'
        signal.reason = (
            f"[REFUSE PAR TIMESFM] Carnet: {consensus_dir} {consensus_pct:.0f}% MAIS "
            f"TimesFM predit {tfm_dir} (Δ{signal.timesfm_change_pct:+.2f}%, conf={tfm_conf:.0%}) — "
            f"contradiction IA vs marche"
        )
        return signal
