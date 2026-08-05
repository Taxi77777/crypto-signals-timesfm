"""
╔══════════════════════════════════════════════════════════════════╗
║     BACKTEST PRIX + VOLUME — Institutional Hunter Pro           ║
║     backtest_signals.py                                          ║
║                                                                  ║
║  CE QUI EST BACKTESTABLE :                                       ║
║    • Google TimesFM 2.5 sur les cloture 4H (donnees historiques) ║
║    • Le filtre de volume des bougies                             ║
║    • L'ATR, le TP, la liquidation, l'expiration                  ║
║    • Les frais taker                                             ║
║                                                                  ║
║  CE QUI NE L'EST PAS :                                           ║
║    • Le desequilibre du carnet d'ordres des 6 exchanges          ║
║    • Le CVD                                                      ║
║  Aucun exchange ne publie gratuitement les snapshots L2 passes.  ║
║  Ces deux filtres ne peuvent donc que REDUIRE le nombre de       ║
║  signaux mesure ici. Les chiffres produits sont un PLAFOND.      ║
║                                                                  ║
║  AUCUN LOOKAHEAD :                                               ║
║    Decision prise a la cloture de la bougie i, en n'utilisant    ║
║    que les cloture <= i. L'issue est evaluee sur i+1 et apres.   ║
╚══════════════════════════════════════════════════════════════════╝
"""
import sys
import logging
import numpy as np
import pandas as pd

import mexc_api as api
from timesfm_predictor import _load_model, HORIZON, CONTEXT
from scanner import get_active_pairs
from config import (
    ATR_TP_MULT, LEVERAGE, LIQUIDATION_BUFFER, TAKER_FEE_PCT,
    MAX_HOLD_HOURS, VOLUME_MA_PERIOD, MIN_VOLUME_RATIO,
    TIMESFM_MIN_CONFIDENCE, POSITION_MARGIN_PCT, MAX_MARGIN_USDT,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('BACKTEST')

# ── Paramètres du backtest ────────────────────────────────────────
N_PAIRS      = 12      # paires testées (les plus gros volumes)
KLINES       = 1000    # bougies 4H récupérées (~166 jours)
STEP         = 2       # on teste une bougie sur STEP (vitesse)
WARMUP       = 260     # bougies minimales avant la première décision
BATCH        = 64      # forecasts groupés envoyés à TimesFM
CAPITAL      = 100.0   # capital de référence pour le PnL

BARS_PER_HOUR = 0.25                       # 1 bougie = 4 h
MAX_HOLD_BARS = int(MAX_HOLD_HOURS / 4)    # 24 h -> 6 bougies


def timesfm_verdict_from_forecast(predicted, current_price):
    """
    Reproduit EXACTEMENT la logique de timesfm_predictor.predict_price_direction,
    partie prix uniquement (sans le bonus issu des exchanges).
    """
    predicted_final = float(predicted[-1])
    change_pct = (predicted_final - current_price) / current_price * 100
    x = np.arange(len(predicted))
    slope = np.polyfit(x, predicted, 1)[0]
    slope_pct = (slope / current_price) * 100
    bullish = sum(1 for p in predicted if p > current_price)
    bearish = sum(1 for p in predicted if p < current_price)

    direction, conf = 'NEUTRAL', 0.0
    if change_pct >= 0.5 and slope_pct > 0 and bullish >= 5:
        direction, conf = 'BUY', min(0.95, 0.5 + abs(change_pct) / 8.0)
    elif change_pct <= -0.5 and slope_pct < 0 and bearish >= 5:
        direction, conf = 'SELL', min(0.95, 0.5 + abs(change_pct) / 8.0)
    elif abs(change_pct) >= 0.3:
        direction = 'BUY' if change_pct > 0 else 'SELL'
        conf = 0.35 + abs(change_pct) / 15.0
    return direction, round(conf, 4), round(change_pct, 4)


def atr_at(df, i, period=14):
    """ATR calculé uniquement sur les bougies <= i (aucun lookahead)."""
    sl = df.iloc[max(0, i - period - 1):i + 1]
    if len(sl) < 3:
        return 0.0
    h, l, c = sl['high'].values, sl['low'].values, sl['close'].values
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    return float(np.mean(tr)) if len(tr) else 0.0


def volume_ratio_at(df, i):
    """Volume de la bougie i rapporté aux VOLUME_MA_PERIOD précédentes."""
    if i < VOLUME_MA_PERIOD + 1:
        return 0.0
    window = df['volume'].iloc[i - VOLUME_MA_PERIOD:i]
    avg = float(window.mean())
    if avg <= 0:
        return 0.0
    return float(df['volume'].iloc[i]) / avg


def simulate_outcome(df, i, direction, entry, tp, liq):
    """
    Rejoue les bougies i+1 .. i+MAX_HOLD_BARS.
    Hypothèse défavorable : on teste la LIQUIDATION avant le TAKE PROFIT
    à l'intérieur d'une même bougie (l'ordre réel des ticks est inconnu).
    """
    end = min(i + MAX_HOLD_BARS, len(df) - 1)
    for j in range(i + 1, end + 1):
        high = float(df['high'].iloc[j])
        low = float(df['low'].iloc[j])
        if direction == 'BUY':
            if low <= liq:
                return liq, 'LIQUIDATION', j - i
            if high >= tp:
                return tp, 'TAKE_PROFIT', j - i
        else:
            if high >= liq:
                return liq, 'LIQUIDATION', j - i
            if low <= tp:
                return tp, 'TAKE_PROFIT', j - i
    if end > i:
        return float(df['close'].iloc[end]), 'EXPIRATION', end - i
    return None, None, 0


def run():
    model = _load_model()
    if model is None:
        log.error("TimesFM indisponible — backtest impossible.")
        return 1

    pairs = get_active_pairs(True)[:N_PAIRS]
    log.info(f"Paires testees : {len(pairs)} -> {', '.join(pairs)}")

    adverse = (100.0 / LEVERAGE) * LIQUIDATION_BUFFER / 100.0
    margin = min(CAPITAL * (POSITION_MARGIN_PCT / 100.0), MAX_MARGIN_USDT)
    notional = margin * LEVERAGE
    log.info(f"Liquidation simulee a {adverse*100:.2f}% | notionnel {notional:.2f} USDT")

    verdicts = {'BUY': 0, 'SELL': 0, 'NEUTRAL': 0}
    n_points = 0
    n_conf_ok = 0
    n_vol_ok = 0
    n_signals = 0
    trades = []

    for pair in pairs:
        try:
            df = api.get_klines(pair, '4h', KLINES)
        except Exception as e:
            log.warning(f"{pair} : klines indisponibles ({e})")
            continue
        if df is None or len(df) < WARMUP + 50:
            log.warning(f"{pair} : historique insuffisant ({0 if df is None else len(df)})")
            continue

        closes = df['close'].values.astype(float)
        idxs = list(range(WARMUP, len(df) - MAX_HOLD_BARS - 1, STEP))

        for b0 in range(0, len(idxs), BATCH):
            chunk = idxs[b0:b0 + BATCH]
            inputs = [closes[max(0, i - CONTEXT):i + 1] for i in chunk]
            try:
                point_forecast, _ = model.forecast(HORIZON, inputs)
            except Exception as e:
                log.warning(f"{pair} : forecast en echec ({e})")
                continue

            for k, i in enumerate(chunk):
                n_points += 1
                current = float(closes[i])
                direction, conf, change = timesfm_verdict_from_forecast(
                    list(point_forecast[k]), current
                )
                verdicts[direction] += 1
                if direction == 'NEUTRAL':
                    continue
                if conf < TIMESFM_MIN_CONFIDENCE:
                    continue
                n_conf_ok += 1

                vr = volume_ratio_at(df, i)
                if vr > 0 and vr < MIN_VOLUME_RATIO:
                    continue
                n_vol_ok += 1

                atr = atr_at(df, i)
                if atr <= 0:
                    continue

                entry = current
                tp = entry + atr * ATR_TP_MULT if direction == 'BUY' else entry - atr * ATR_TP_MULT
                liq = entry * (1 - adverse) if direction == 'BUY' else entry * (1 + adverse)

                exit_px, reason, bars = simulate_outcome(df, i, direction, entry, tp, liq)
                if exit_px is None:
                    continue

                move = ((exit_px - entry) / entry * 100) if direction == 'BUY' \
                    else ((entry - exit_px) / entry * 100)
                pnl = notional * (move / 100.0) - notional * (TAKER_FEE_PCT / 100.0) * 2

                n_signals += 1
                trades.append({
                    'pair': pair, 'direction': direction, 'conf': conf,
                    'change_pct': change, 'vol_ratio': round(vr, 2),
                    'entry': entry, 'exit': exit_px, 'reason': reason,
                    'bars': bars, 'move_pct': round(move, 3), 'pnl': round(pnl, 4),
                })

        log.info(f"{pair} : termine ({n_signals} signaux cumules)")

    # ── Résultats ────────────────────────────────────────────────
    print("\n" + "=" * 70)
    print("  BACKTEST PRIX + VOLUME — RESULTATS")
    print("=" * 70)
    print(f"  Paires              : {len(pairs)}")
    print(f"  Points evalues      : {n_points}")
    print(f"  Periode             : ~{KLINES*4/24:.0f} jours de bougies 4H\n")

    print("  VERDICTS DE GOOGLE TIMESFM (prix seul, sans bonus carnet)")
    for k in ('BUY', 'SELL', 'NEUTRAL'):
        pct = verdicts[k] / n_points * 100 if n_points else 0
        print(f"    {k:<8} : {verdicts[k]:6d}  ({pct:5.1f} %)")

    print(f"\n  ENTONNOIR")
    print(f"    Direction non neutre           : {verdicts['BUY']+verdicts['SELL']:6d}")
    print(f"    + confiance >= {TIMESFM_MIN_CONFIDENCE:.0%}            : {n_conf_ok:6d}")
    print(f"    + volume >= {MIN_VOLUME_RATIO:.1f}x moyenne      : {n_vol_ok:6d}")
    print(f"    = signaux simules              : {n_signals:6d}")
    if n_points:
        print(f"    soit {n_signals/n_points*100:.2f} % des points evalues")

    if not trades:
        print("\n  Aucun signal — rien a mesurer.")
        print("=" * 70)
        return 0

    t = pd.DataFrame(trades)
    wins = t[t['pnl'] > 0]
    losses = t[t['pnl'] <= 0]
    gross_win = wins['pnl'].sum()
    gross_loss = abs(losses['pnl'].sum())

    equity, peak, max_dd = 0.0, 0.0, 0.0
    for p in t['pnl']:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    print(f"\n  RESULTAT DES {len(t)} SIGNAUX (notionnel {notional:.0f} USDT, frais inclus)")
    print(f"    Taux de reussite  : {len(wins)/len(t)*100:.1f} %  ({len(wins)}G / {len(losses)}P)")
    print(f"    PnL total         : {t['pnl'].sum():+.2f} USDT")
    print(f"    Esperance / trade : {t['pnl'].mean():+.4f} USDT")
    print(f"    Gain moyen        : {wins['pnl'].mean() if len(wins) else 0:+.4f}")
    print(f"    Perte moyenne     : {losses['pnl'].mean() if len(losses) else 0:+.4f}")
    pf = (gross_win / gross_loss) if gross_loss > 0 else float('inf')
    print(f"    Profit factor     : {pf:.2f}")
    print(f"    Drawdown max      : -{max_dd:.2f} USDT")

    print(f"\n  REPARTITION DES SORTIES")
    for reason, grp in t.groupby('reason'):
        print(f"    {reason:<12} : {len(grp):4d}  ({len(grp)/len(t)*100:5.1f} %)  "
              f"PnL {grp['pnl'].sum():+8.2f}")

    print(f"\n  PAR DIRECTION")
    for d, grp in t.groupby('direction'):
        w = (grp['pnl'] > 0).sum()
        print(f"    {d:<5} : {len(grp):4d} trades | reussite {w/len(grp)*100:5.1f} % | "
              f"PnL {grp['pnl'].sum():+8.2f}")

    t.to_csv('backtest_results.csv', index=False)
    print(f"\n  Detail complet ecrit dans backtest_results.csv")
    print("=" * 70)
    print("\n  RAPPEL : le desequilibre du carnet des 6 exchanges et le CVD")
    print("  ne sont PAS inclus (donnees historiques inexistantes).")
    print("  Ils ne peuvent que reduire ce nombre de signaux.")
    print("  Ces chiffres sont donc un PLAFOND, pas une prevision.")
    print("=" * 70)
    return 0


if __name__ == '__main__':
    sys.exit(run())
