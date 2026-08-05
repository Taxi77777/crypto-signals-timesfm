"""
╔══════════════════════════════════════════════════════════════════╗
║     GOOGLE TIMESFM 2.5 — JUGE FINAL                             ║
║     timesfm_predictor.py                                        ║
║                                                                  ║
║  RÔLE : Recevoir TOUTES les données et donner le feu vert       ║
║  INPUT : prix historiques 4H + données carnet d'ordres          ║
║  OUTPUT: BUY / SELL / NEUTRAL → SEUL l'accord TimesFM trade    ║
║                                                                  ║
║  → Modèle : google/timesfm-2.5-200m-pytorch (local, gratuit)   ║
║  → Prédit 10 bougies 4H + analyse le flux des exchanges         ║
║  → TimesFM BLOQUE ou APPROUVE — c'est lui le juge final        ║
╚══════════════════════════════════════════════════════════════════╝
"""
import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass, field

log = logging.getLogger("IHP-TIMESFM")

# Modèle singleton — chargé une seule fois en mémoire
_model = None
HORIZON = 10    # 10 bougies 4H (~40 heures)
CONTEXT = 512   # Historique envoyé au modèle


@dataclass
class TimesFMPrediction:
    direction:            str
    confidence:           float
    current_price:        float
    predicted_prices:     list
    predicted_change_pct: float
    reasoning:            str
    available:            bool = True
    exchange_data_used:   bool = False   # True si données exchanges transmises


def _load_model():
    """Charge TimesFM 2.5 via from_pretrained (repo pytorch correct v2.0.2)."""
    global _model
    if _model is not None:
        return _model

    log.info("[TimesFM] Chargement du modele Google TimesFM 2.5...")
    log.info("[TimesFM] Repo : google/timesfm-2.5-200m-pytorch")
    try:
        from timesfm import timesfm_2p5_torch as tf25
        from timesfm import ForecastConfig
        tfm = tf25.TimesFM_2p5_200M_torch.from_pretrained(
            "google/timesfm-2.5-200m-pytorch"
        )
        log.info("[TimesFM] Compilation du modele (ForecastConfig par defaut)...")
        fc = ForecastConfig(
            max_context=CONTEXT,
            max_horizon=HORIZON,
            normalize_inputs=True,
        )
        tfm.compile(fc)
        _model = tfm
        log.info("[TimesFM] MODELE COMPILE ET PRET — JUGE FINAL ACTIF")
        return tfm
    except Exception as e:
        log.error(f"[TimesFM] Erreur chargement : {e}")
        return None


def preload_model():
    """A appeler au demarrage du bot — charge TimesFM en avance."""
    return _load_model()


def predict_price_direction(
    df: pd.DataFrame,
    symbol: str = "?",
    exchange_data: dict = None,   # ← Données exchanges transmises par strategy.py
) -> TimesFMPrediction:
    """
    JUGE FINAL — analyse prix + données de TOUTES les plateformes.

    Paramètres :
      df            : DataFrame bougies 4H (prix historiques)
      symbol        : symbole ex. BTC_USDT
      exchange_data : dict complet des carnets d'ordres 6 exchanges
                      {consensus_pct, consensus_direction, avg_stacked_buy,
                       avg_stacked_sell, cvd_buy, cvd_sell, avg_direction_score...}

    Retourne TimesFMPrediction avec direction BUY/SELL/NEUTRAL
    """
    neutral = TimesFMPrediction(
        direction='NEUTRAL', confidence=0.0, current_price=0.0,
        predicted_prices=[], predicted_change_pct=0.0,
        reasoning="TimesFM non disponible", available=False
    )

    if df is None or len(df) < 32:
        return neutral

    model = _load_model()
    if model is None:
        return neutral

    try:
        closes        = df['close'].values.astype(float)
        ctx_len       = min(CONTEXT, len(closes))
        input_seq     = closes[-ctx_len:]
        current_price = float(closes[-1])

        log.info(f"[TimesFM] {symbol} : analyse {ctx_len} bougies 4H -> horizon {HORIZON} bougies")

        # ── Prédiction via TimesFM ─────────────────────────────────
        # Signature : forecast(horizon: int, inputs: list[np.ndarray])
        # ATTENTION : horizon est POSITIONNEL (pas keyword)
        point_forecast, _ = model.forecast(HORIZON, [input_seq])
        predicted = point_forecast[0].tolist()

        if not predicted:
            return neutral

        predicted_final = float(predicted[-1])
        change_pct      = (predicted_final - current_price) / current_price * 100
        x               = np.arange(len(predicted))
        slope           = np.polyfit(x, predicted, 1)[0]
        slope_pct       = (slope / current_price) * 100
        bullish_pts     = sum(1 for p in predicted if p > current_price)
        bearish_pts     = sum(1 for p in predicted if p < current_price)

        # ── Décision initiale sur le prix seul ────────────────────────
        price_direction = 'NEUTRAL'
        price_confidence = 0.0

        if change_pct >= 0.5 and slope_pct > 0 and bullish_pts >= 5:
            price_direction  = 'BUY'
            price_confidence = min(0.95, 0.5 + abs(change_pct) / 8.0)
        elif change_pct <= -0.5 and slope_pct < 0 and bearish_pts >= 5:
            price_direction  = 'SELL'
            price_confidence = min(0.95, 0.5 + abs(change_pct) / 8.0)
        elif abs(change_pct) >= 0.3:
            price_direction  = 'BUY' if change_pct > 0 else 'SELL'
            price_confidence = 0.35 + abs(change_pct) / 15.0

        # ── Enrichissement par les données exchanges ───────────────────
        # TimesFM combine sa prédiction de prix avec :
        #  - le consensus multi-exchange (qui achète/vend vraiment ?)
        #  - les Stacked Imbalances (institutionnels)
        #  - le CVD (qui frappe le marché en agressif ?)
        exchange_used = False
        exchange_bonus = 0.0
        exchange_notes = []

        if exchange_data and isinstance(exchange_data, dict):
            exchange_used = True
            cons_dir  = exchange_data.get('consensus_direction', 'NEUTRAL')
            cons_pct  = exchange_data.get('consensus_pct', 0)
            stack_b   = exchange_data.get('avg_stacked_buy', 0)
            stack_s   = exchange_data.get('avg_stacked_sell', 0)
            cvd_b     = exchange_data.get('cvd_buy', 0)
            cvd_s     = exchange_data.get('cvd_sell', 0)
            score     = exchange_data.get('avg_direction_score', 0)
            ex_ok     = exchange_data.get('exchanges_ok', 0)

            log.info(
                f"[TimesFM] Donnees exchanges recues : consensus={cons_dir} {cons_pct:.0f}% | "
                f"stacked B:{stack_b:.1f} S:{stack_s:.1f} | CVD {cvd_b}B/{cvd_s}S | "
                f"score={score:+.0f} | {ex_ok}/6 exchanges"
            )

            # Bonus si exchanges et TimesFM sont ALIGNES
            if cons_dir == price_direction and cons_pct >= 80:
                exchange_bonus += 0.12
                exchange_notes.append(f"Carnet ALIGNE {cons_dir} {cons_pct:.0f}%")

            # Bonus Stacked Imbalances institutionnels
            if price_direction == 'BUY' and stack_b >= 4:
                exchange_bonus += 0.06
                exchange_notes.append(f"Stacked BUY fort ({stack_b:.1f} niveaux)")
            elif price_direction == 'SELL' and stack_s >= 4:
                exchange_bonus += 0.06
                exchange_notes.append(f"Stacked SELL fort ({stack_s:.1f} niveaux)")

            # Bonus CVD confirmatoire
            if price_direction == 'BUY' and cvd_b > cvd_s:
                exchange_bonus += 0.04
                exchange_notes.append(f"CVD haussier ({cvd_b}B/{cvd_s}S)")
            elif price_direction == 'SELL' and cvd_s > cvd_b:
                exchange_bonus += 0.04
                exchange_notes.append(f"CVD baissier ({cvd_s}S/{cvd_b}B)")

            # Bonus score directionnel global
            if price_direction == 'BUY' and score > 15:
                exchange_bonus += 0.04
                exchange_notes.append(f"Score directionnel fort +{score:.0f}")
            elif price_direction == 'SELL' and score < -15:
                exchange_bonus += 0.04
                exchange_notes.append(f"Score directionnel fort {score:.0f}")

        # Confiance finale = prix seul + bonus exchanges
        final_confidence = min(0.97, price_confidence + exchange_bonus)
        final_direction  = price_direction

        if exchange_notes:
            ex_note_str = " | ".join(exchange_notes)
        elif exchange_used:
            ex_note_str = f"donnees recues ({ex_ok}/6 exchanges) — consensus {cons_dir} {cons_pct:.0f}% (pas de bonus applique)"
        else:
            ex_note_str = "pas de donnees exchanges transmises"


        reasoning = (
            f"TimesFM {ctx_len}x4H -> actuel:{current_price:.2f} "
            f"predit:{predicted_final:.2f} delta:{change_pct:+.2f}% "
            f"haussier:{bullish_pts}/{len(predicted)} pente:{slope_pct:+.4f}%/4H | "
            f"Exchanges: {ex_note_str}"
        )

        log.info(
            f"[TimesFM] {symbol} -> VERDICT: {final_direction} | "
            f"conf={final_confidence:.0%} | delta={change_pct:+.2f}% | "
            f"bonus_exchanges={exchange_bonus:+.2f}"
        )

        return TimesFMPrediction(
            direction=final_direction,
            confidence=round(final_confidence, 3),
            current_price=current_price,
            predicted_prices=[round(p, 4) for p in predicted],
            predicted_change_pct=round(change_pct, 4),
            reasoning=reasoning,
            available=True,
            exchange_data_used=exchange_used,
        )

    except Exception as e:
        log.error(f"[TimesFM] Erreur prediction {symbol}: {e}")
        return neutral


def get_timesfm_verdict(
    df: pd.DataFrame,
    symbol: str = "?",
    exchange_data: dict = None,   # ← Reçoit les données de TOUTES les plateformes
) -> dict:
    """
    Interface principale appelée par strategy.py.
    Reçoit les données de prix ET les données carnet d'ordres de tous les exchanges.
    Retourne toujours un dict valide.
    """
    try:
        pred = predict_price_direction(df, symbol, exchange_data=exchange_data)
    except Exception as e:
        log.error(f"[TimesFM] Exception globale {symbol}: {e}")
        pred = TimesFMPrediction(
            direction='NEUTRAL', confidence=0.0, current_price=0.0,
            predicted_prices=[], predicted_change_pct=0.0,
            reasoning=f"Erreur: {str(e)[:80]}", available=False
        )
    return {
        'direction':            pred.direction,
        'confidence':           pred.confidence,
        'reasoning':            pred.reasoning,
        'available':            pred.available,
        'predicted_prices':     pred.predicted_prices,
        'predicted_change_pct': pred.predicted_change_pct,
        'current_price':        pred.current_price,
        'exchange_data_used':   pred.exchange_data_used,
    }
