"""
╔══════════════════════════════════════════════════════════════════╗
║     BALAYAGE DU LEVIER — Institutional Hunter Pro                ║
║     backtest_leverage_sweep.py                                   ║
║                                                                  ║
║  POURQUOI LE LEVIER CHANGE TOUT SANS STOP-LOSS :                 ║
║                                                                  ║
║  Sans SL, la seule sortie perdante est la LIQUIDATION, et sa     ║
║  distance depend directement du levier :                         ║
║                                                                  ║
║      distance = (100 / levier) x marge_de_maintenance            ║
║                                                                  ║
║      x40 -> 2.25 %      x20 -> 4.50 %                            ║
║      x10 -> 9.00 %      x5  -> 18.00 %                           ║
║                                                                  ║
║  Plus le levier est bas, plus le prix a de place pour respirer   ║
║  avant de tuer la position. Le TP, lui, ne bouge pas : il reste  ║
║  a N x ATR. Le rapport TP / distance-de-liquidation s'inverse.   ║
║                                                                  ║
║  COMPARAISON HONNETE :                                           ║
║    Le profit factor est independant de la taille de position     ║
║    (gains et pertes sont multiplies par le meme notionnel), il   ║
║    isole donc l'effet du levier sur la GEOMETRIE des sorties.    ║
║    Le rendement sur marge est reporte a part : c'est lui qui     ║
║    montre ce que le levier rapporte ou coute reellement.         ║
║                                                                  ║
║  Trailing stop TOUJOURS actif : il ameliore 9 variantes sur 9    ║
║  dans le balayage precedent, la question est tranchee.           ║
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
    LIQUIDATION_BUFFER, TAKER_FEE_PCT, MIN_VOLUME_RATIO,
    TIMESFM_MIN_CONFIDENCE, POSITION_MARGIN_PCT, MAX_MARGIN_USDT,
    TRAILING_TRIGGER, TRAILING_STEP,
)
from backtest_signals import (
    timesfm_verdict_from_forecast, atr_at, volume_ratio_at,
    N_PAIRS, KLINES, STEP, WARMUP, BATCH, CAPITAL, MAX_HOLD_BARS,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('LEV-SWEEP')

LEVERAGES = [5, 10, 15, 20, 25, 30, 40]
TP_MULTS = [1.0, 1.5, 2.0, 3.0]


def simulate(df, i, direction, entry, tp, liq):
    """Trailing toujours actif. Stop teste avant TP (hypothese defavorable)."""
    end = min(i + MAX_HOLD_BARS, len(df) - 1)
    trail = None
    for j in range(i + 1, end + 1):
        high = float(df['high'].iloc[j])
        low = float(df['low'].iloc[j])
        if direction == 'BUY':
            stop = max(liq, trail) if trail is not None else liq
            if low <= stop:
                return stop, ('TRAILING' if (trail is not None and stop == trail) else 'LIQUIDATION')
            if high >= tp:
                return tp, 'TAKE_PROFIT'
            if (high - entry) / entry * 100 >= TRAILING_TRIGGER:
                nt = high * (1 - TRAILING_STEP / 100.0)
                if trail is None or nt > trail:
                    trail = nt
        else:
            stop = min(liq, trail) if trail is not None else liq
            if high >= stop:
                return stop, ('TRAILING' if (trail is not None and stop == trail) else 'LIQUIDATION')
            if low <= tp:
                return tp, 'TAKE_PROFIT'
            if (entry - low) / entry * 100 >= TRAILING_TRIGGER:
                nt = low * (1 + TRAILING_STEP / 100.0)
                if trail is None or nt < trail:
                    trail = nt
    if end > i:
        return float(df['close'].iloc[end]), 'EXPIRATION'
    return None, None


def run():
    model = _load_model()
    if model is None:
        log.error("TimesFM indisponible.")
        return 1

    pairs = get_active_pairs(True)[:N_PAIRS]
    signals, frames = [], {}

    for pair in pairs:
        try:
            df = api.get_klines(pair, '4h', KLINES)
        except Exception as e:
            log.warning(f"{pair} : {e}")
            continue
        if df is None or len(df) < WARMUP + 50:
            continue
        frames[pair] = df
        closes = df['close'].values.astype(float)
        idxs = list(range(WARMUP, len(df) - MAX_HOLD_BARS - 1, STEP))

        for b0 in range(0, len(idxs), BATCH):
            chunk = idxs[b0:b0 + BATCH]
            inputs = [closes[max(0, i - CONTEXT):i + 1] for i in chunk]
            try:
                pf_, _ = model.forecast(HORIZON, inputs)
            except Exception as e:
                log.warning(f"{pair} : forecast {e}")
                continue
            for k, i in enumerate(chunk):
                cur = float(closes[i])
                d, conf, _ = timesfm_verdict_from_forecast(list(pf_[k]), cur)
                if d == 'NEUTRAL' or conf < TIMESFM_MIN_CONFIDENCE:
                    continue
                vr = volume_ratio_at(df, i)
                if vr > 0 and vr < MIN_VOLUME_RATIO:
                    continue
                atr = atr_at(df, i)
                if atr <= 0:
                    continue
                signals.append({'pair': pair, 'i': i, 'dir': d, 'entry': cur, 'atr': atr})
        log.info(f"{pair} : {len(signals)} signaux cumules")

    if not signals:
        print("Aucun signal.")
        return 0

    atr_pct = np.mean([s['atr'] / s['entry'] * 100 for s in signals])
    margin = min(CAPITAL * (POSITION_MARGIN_PCT / 100.0), MAX_MARGIN_USDT)

    print("\n" + "=" * 104)
    print(f"  BALAYAGE DU LEVIER — {len(signals)} signaux identiques | trailing actif")
    print(f"  ATR moyen {atr_pct:.2f} % du prix | marge engagee {margin:.2f} USDT par trade")
    print("=" * 104)
    print(f"{'Levier':>7} {'LIQ a':>7} {'TP':>5} {'TP en %':>8} {'TP/LIQ':>7} "
          f"{'reussite':>9} {'PF':>6} {'rend/marge':>11} {'DD marge':>9} "
          f"{'TP':>4} {'TRAIL':>6} {'LIQ':>5} {'EXP':>4}")
    print("-" * 104)

    rows = []
    for lev in LEVERAGES:
        adverse = (100.0 / lev) * LIQUIDATION_BUFFER / 100.0
        notional = margin * lev

        for mult in TP_MULTS:
            pnls = []
            reasons = {'TAKE_PROFIT': 0, 'TRAILING': 0, 'LIQUIDATION': 0, 'EXPIRATION': 0}

            for s in signals:
                df = frames[s['pair']]
                entry, atr, d = s['entry'], s['atr'], s['dir']
                tp = entry + atr * mult if d == 'BUY' else entry - atr * mult
                liq = entry * (1 - adverse) if d == 'BUY' else entry * (1 + adverse)
                exit_px, reason = simulate(df, s['i'], d, entry, tp, liq)
                if exit_px is None:
                    continue
                move = ((exit_px - entry) / entry * 100) if d == 'BUY' \
                    else ((entry - exit_px) / entry * 100)
                pnl = notional * (move / 100.0) - notional * (TAKER_FEE_PCT / 100.0) * 2
                pnls.append(pnl)
                reasons[reason] += 1

            if not pnls:
                continue
            arr = np.array(pnls)
            wins, losses = arr[arr > 0], arr[arr <= 0]
            gw, gl = wins.sum(), abs(losses.sum())
            pf = (gw / gl) if gl > 0 else float('inf')

            eq = peak = dd = 0.0
            for p in arr:
                eq += p
                peak = max(peak, eq)
                dd = max(dd, peak - eq)

            n = len(arr)
            tp_pct = mult * atr_pct
            rend = arr.sum() / margin * 100 / n      # rendement moyen par trade, en % de la marge
            dd_marge = dd / margin * 100

            print(f"{'x'+str(lev):>7} {adverse*100:>6.2f}% {mult:>5.2f} {tp_pct:>7.2f}% "
                  f"{tp_pct/(adverse*100):>7.2f} {len(wins)/n*100:>8.1f}% {pf:>6.2f} "
                  f"{rend:>+10.3f}% {-dd_marge:>8.1f}% "
                  f"{reasons['TAKE_PROFIT']:>4d} {reasons['TRAILING']:>6d} "
                  f"{reasons['LIQUIDATION']:>5d} {reasons['EXPIRATION']:>4d}")

            rows.append({'leverage': lev, 'liq_pct': round(adverse*100, 3),
                         'tp_mult': mult, 'tp_pct': round(tp_pct, 3),
                         'tp_over_liq': round(tp_pct/(adverse*100), 3),
                         'win_rate': round(len(wins)/n*100, 2), 'profit_factor': round(pf, 3),
                         'return_on_margin_pct': round(rend, 4),
                         'dd_on_margin_pct': round(-dd_marge, 2),
                         'pnl_total': round(arr.sum(), 2),
                         'n_tp': reasons['TAKE_PROFIT'], 'n_trail': reasons['TRAILING'],
                         'n_liq': reasons['LIQUIDATION'], 'n_exp': reasons['EXPIRATION'],
                         'n_trades': n})
        print("-" * 104)

    res = pd.DataFrame(rows)
    res.to_csv('leverage_sweep_results.csv', index=False)

    print("\n  TAUX DE LIQUIDATION PAR LEVIER (moyenne sur les TP testes)")
    for lev in LEVERAGES:
        sub = res[res['leverage'] == lev]
        liq_rate = sub['n_liq'].sum() / sub['n_trades'].sum() * 100
        print(f"    x{lev:<3} : {liq_rate:5.1f} % des trades liquides | "
              f"PF moyen {sub['profit_factor'].mean():.2f}")

    best_pf = res.loc[res['profit_factor'].idxmax()]
    best_rend = res.loc[res['return_on_margin_pct'].idxmax()]
    print(f"\n  Meilleur profit factor  : x{best_pf['leverage']} TP {best_pf['tp_mult']}x "
          f"-> PF {best_pf['profit_factor']:.2f}, liquidations {best_pf['n_liq']}, "
          f"DD {best_pf['dd_on_margin_pct']:.1f} % de la marge")
    print(f"  Meilleur rendement/marge: x{best_rend['leverage']} TP {best_rend['tp_mult']}x "
          f"-> {best_rend['return_on_margin_pct']:+.3f} %/trade, "
          f"DD {best_rend['dd_on_margin_pct']:.1f} %")

    print("\n  LECTURE : le profit factor mesure la QUALITE des sorties, il ne")
    print("  depend pas de la taille de position. Le rendement sur marge mesure")
    print("  ce que le levier RAPPORTE — il monte avec le levier tant que la")
    print("  liquidation ne coupe pas trop de trades gagnants.")
    print("  Le carnet des 6 exchanges n'est PAS inclus : plafond, pas prevision.")
    print("=" * 104)
    return 0


if __name__ == '__main__':
    sys.exit(run())
