"""
╔══════════════════════════════════════════════════════════════════╗
║     BALAYAGE DU TAKE PROFIT — Institutional Hunter Pro          ║
║     backtest_tp_sweep.py                                         ║
║                                                                  ║
║  CONSTAT DU BACKTEST PRECEDENT :                                 ║
║    TP a 5 x ATR -> atteint 3 fois sur 287 (1.0 %)               ║
║    Liquidation a 2.25 % -> declenchee 121 fois (42.2 %)         ║
║    Le TP est structurellement HORS D'ATTEINTE avant la           ║
║    liquidation. Ce n'est pas la detection qui echoue, c'est la   ║
║    geometrie de la sortie.                                       ║
║                                                                  ║
║  METHODE :                                                       ║
║    1. On calcule les signaux UNE SEULE FOIS (TimesFM + volume). ║
║    2. On rejoue le MEME jeu de signaux pour chaque valeur de TP. ║
║    Seul le TP change : la comparaison est donc rigoureuse,       ║
║    aucun biais de selection entre les variantes.                 ║
║                                                                  ║
║  Le TP est aussi exprime en % du prix, pour le comparer          ║
║  directement a la distance de liquidation.                       ║
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
    LEVERAGE, LIQUIDATION_BUFFER, TAKER_FEE_PCT, MAX_HOLD_HOURS,
    VOLUME_MA_PERIOD, MIN_VOLUME_RATIO, TIMESFM_MIN_CONFIDENCE,
    POSITION_MARGIN_PCT, MAX_MARGIN_USDT,
    TRAILING_TRIGGER, TRAILING_STEP,
)
from backtest_signals import (
    timesfm_verdict_from_forecast, atr_at, volume_ratio_at,
    N_PAIRS, KLINES, STEP, WARMUP, BATCH, CAPITAL, MAX_HOLD_BARS,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger('TP-SWEEP')

# Multiplicateurs d'ATR testés pour le Take Profit
TP_MULTS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 5.0]


def simulate(df, i, direction, entry, tp, liq, trailing=False):
    """
    Rejoue les bougies suivant l'entrée.

    Hypothèse défavorable systématique :
      • le stop (liquidation ou trailing) est testé AVANT le take profit
        dans une même bougie — l'ordre réel des ticks est inconnu ;
      • le trailing n'est mis à jour qu'APRÈS avoir testé le stop de la
        bougie courante, sinon on utiliserait le plus haut de la bougie
        pour se protéger à l'intérieur de cette même bougie : ce serait
        du lookahead intra-bougie.

    Le trailing ne s'arme qu'après TRAILING_TRIGGER % de profit, il ne
    peut donc jamais transformer un gain en perte sèche.
    """
    end = min(i + MAX_HOLD_BARS, len(df) - 1)
    trail = None      # niveau de stop suiveur, None tant que non armé

    for j in range(i + 1, end + 1):
        high = float(df['high'].iloc[j])
        low = float(df['low'].iloc[j])

        if direction == 'BUY':
            stop = max(liq, trail) if trail is not None else liq
            if low <= stop:
                return stop, ('TRAILING_STOP' if (trail is not None and stop == trail)
                              else 'LIQUIDATION')
            if high >= tp:
                return tp, 'TAKE_PROFIT'
            if trailing and (high - entry) / entry * 100 >= TRAILING_TRIGGER:
                new_trail = high * (1 - TRAILING_STEP / 100.0)
                if trail is None or new_trail > trail:
                    trail = new_trail
        else:
            stop = min(liq, trail) if trail is not None else liq
            if high >= stop:
                return stop, ('TRAILING_STOP' if (trail is not None and stop == trail)
                              else 'LIQUIDATION')
            if low <= tp:
                return tp, 'TAKE_PROFIT'
            if trailing and (entry - low) / entry * 100 >= TRAILING_TRIGGER:
                new_trail = low * (1 + TRAILING_STEP / 100.0)
                if trail is None or new_trail < trail:
                    trail = new_trail

    if end > i:
        return float(df['close'].iloc[end]), 'EXPIRATION'
    return None, None


def run():
    model = _load_model()
    if model is None:
        log.error("TimesFM indisponible.")
        return 1

    pairs = get_active_pairs(True)[:N_PAIRS]
    adverse = (100.0 / LEVERAGE) * LIQUIDATION_BUFFER / 100.0
    margin = min(CAPITAL * (POSITION_MARGIN_PCT / 100.0), MAX_MARGIN_USDT)
    notional = margin * LEVERAGE

    log.info(f"Paires : {len(pairs)} | liquidation a {adverse*100:.2f} % | notionnel {notional:.0f} USDT")

    # ── PASSE 1 : collecte des signaux (une seule fois) ──────────
    signals = []
    frames = {}

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
                pf, _ = model.forecast(HORIZON, inputs)
            except Exception as e:
                log.warning(f"{pair} : forecast {e}")
                continue

            for k, i in enumerate(chunk):
                current = float(closes[i])
                direction, conf, _ = timesfm_verdict_from_forecast(list(pf[k]), current)
                if direction == 'NEUTRAL' or conf < TIMESFM_MIN_CONFIDENCE:
                    continue
                vr = volume_ratio_at(df, i)
                if vr > 0 and vr < MIN_VOLUME_RATIO:
                    continue
                atr = atr_at(df, i)
                if atr <= 0:
                    continue
                signals.append({'pair': pair, 'i': i, 'dir': direction,
                                'entry': current, 'atr': atr})

        log.info(f"{pair} : {len(signals)} signaux cumules")

    if not signals:
        print("Aucun signal — rien a balayer.")
        return 0

    # ATR moyen en % du prix : explique pourquoi le TP a 5x est inatteignable
    atr_pct = np.mean([s['atr'] / s['entry'] * 100 for s in signals])
    log.info(f"ATR moyen = {atr_pct:.2f} % du prix")

    # ── PASSE 2 : rejeu du MEME jeu de signaux pour chaque TP ────
    rows = []

    for trailing in (False, True):
        titre = ("AVEC TRAILING STOP "
                 f"(armement +{TRAILING_TRIGGER} %, suivi {TRAILING_STEP} %)"
                 if trailing else "SANS TRAILING STOP")
        print("\n" + "=" * 100)
        print(f"  {titre} — {len(signals)} signaux identiques rejoues")
        print(f"  Liquidation fixe a {adverse*100:.2f} % | ATR moyen {atr_pct:.2f} % du prix")
        print("=" * 100)
        print(f"{'TP':>6} {'TP en %':>9} {'reussite':>9} {'PF':>7} {'esperance':>11} "
              f"{'PnL tot':>10} {'DD max':>9} {'TP':>5} {'TRAIL':>6} {'LIQ':>6} {'EXPIR':>6}")
        print("-" * 100)

        for mult in TP_MULTS:
            pnls = []
            reasons = {'TAKE_PROFIT': 0, 'TRAILING_STOP': 0,
                       'LIQUIDATION': 0, 'EXPIRATION': 0}

            for s in signals:
                df = frames[s['pair']]
                entry, atr, d = s['entry'], s['atr'], s['dir']
                tp = entry + atr * mult if d == 'BUY' else entry - atr * mult
                liq = entry * (1 - adverse) if d == 'BUY' else entry * (1 + adverse)

                exit_px, reason = simulate(df, s['i'], d, entry, tp, liq, trailing)
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
            print(f"{mult:>6.2f} {tp_pct:>8.2f}% {len(wins)/n*100:>8.1f}% {pf:>7.2f} "
                  f"{arr.mean():>+11.4f} {arr.sum():>+10.2f} {-dd:>9.2f} "
                  f"{reasons['TAKE_PROFIT']:>5d} {reasons['TRAILING_STOP']:>6d} "
                  f"{reasons['LIQUIDATION']:>6d} {reasons['EXPIRATION']:>6d}")

            rows.append({'trailing': trailing, 'tp_mult': mult, 'tp_pct': round(tp_pct, 3),
                         'win_rate': round(len(wins)/n*100, 2), 'profit_factor': round(pf, 3),
                         'expectancy': round(arr.mean(), 4), 'pnl_total': round(arr.sum(), 2),
                         'max_dd': round(-dd, 2), 'n_tp': reasons['TAKE_PROFIT'],
                         'n_trail': reasons['TRAILING_STOP'],
                         'n_liq': reasons['LIQUIDATION'], 'n_exp': reasons['EXPIRATION'],
                         'n_trades': n})

        print("=" * 100)

    res = pd.DataFrame(rows)
    res.to_csv('tp_sweep_results.csv', index=False)

    def label(r):
        return (f"TP {r['tp_mult']}x ATR ({r['tp_pct']:.2f} %) "
                f"{'AVEC' if r['trailing'] else 'SANS'} trailing")

    best_pf = res.loc[res['profit_factor'].idxmax()]
    best_exp = res.loc[res['expectancy'].idxmax()]
    best_dd = res.loc[res['max_dd'].idxmax()]     # drawdown le moins profond

    print("\n  MEILLEURES VARIANTES")
    print(f"    Profit factor  : {label(best_pf)} -> PF {best_pf['profit_factor']:.2f}, "
          f"reussite {best_pf['win_rate']:.1f} %, DD {best_pf['max_dd']:.2f}")
    print(f"    Esperance      : {label(best_exp)} -> {best_exp['expectancy']:+.4f} USDT/trade, "
          f"PnL {best_exp['pnl_total']:+.2f}")
    print(f"    Drawdown       : {label(best_dd)} -> DD {best_dd['max_dd']:.2f}, "
          f"PF {best_dd['profit_factor']:.2f}")

    # Effet isolé du trailing, à TP identique
    print("\n  EFFET DU TRAILING STOP, A TP IDENTIQUE")
    sans = res[~res['trailing']].set_index('tp_mult')
    avec = res[res['trailing']].set_index('tp_mult')
    for m in TP_MULTS:
        if m in sans.index and m in avec.index:
            d_pf = avec.loc[m, 'profit_factor'] - sans.loc[m, 'profit_factor']
            d_dd = avec.loc[m, 'max_dd'] - sans.loc[m, 'max_dd']
            signe = "ameliore" if d_pf > 0 else ("degrade" if d_pf < 0 else "neutre")
            print(f"    TP {m:>5.2f}x : PF {sans.loc[m,'profit_factor']:.2f} -> "
                  f"{avec.loc[m,'profit_factor']:.2f} ({d_pf:+.2f}, {signe}) | "
                  f"DD {sans.loc[m,'max_dd']:.2f} -> {avec.loc[m,'max_dd']:.2f} ({d_dd:+.2f})")

    print("\n  LECTURE : un profit factor <= 1.0 signifie que la variante perd de")
    print("  l'argent. Entre 1.0 et 1.2, l'avantage est trop mince pour survivre")
    print("  au slippage et a la variation des frais. Le carnet des 6 exchanges")
    print("  n'est PAS inclus ici : ces chiffres restent un plafond.")
    print("=" * 92)
    return 0


if __name__ == '__main__':
    sys.exit(run())
