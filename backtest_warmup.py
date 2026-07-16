"""
backtest_warmup.py — TIME MACHINE : entrainement accelere sur le passe
Rejoue jusqu'a 2 ans d'historique BITCOIN (bougies 1h) : a chaque fenetre, les 5 IA
predisent l'avenir SANS le voir, puis on verifie qui avait raison.
Resultat : track_record.json (carnet de notes) utilise par la ponderation dynamique.
"""

import os
import sys
import gc
import logging
import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("warmup")

import config
from src.track_record import load_track, save_track, record_result, accuracy_summary

# ── Parametres (surchargables par variables d'environnement) ──────────────────
PAIRS = os.getenv("WARMUP_PAIRS", "BTC-USD").split(",")   # SPECIALISTE BITCOIN
PERIOD   = os.getenv("WARMUP_PERIOD", "730d")   # 2 ans
INTERVAL = os.getenv("WARMUP_INTERVAL", "1h")
STEP     = int(os.getenv("WARMUP_STEP", "6"))   # 1 fenetre toutes les 6h (BTC seul -> on peut etre dense)
LLA_MULT = int(os.getenv("WARMUP_LLA_MULT", "3"))  # Lag-Llama plus lent -> 1 fenetre sur 3
CONTEXT  = 512
HORIZON  = config.FORECAST_HORIZON              # 4 bougies
THRESHOLD = 0.05                                 # seuil directionnel crypto (%)
BATCH    = 32


def _direction(var_pct: float) -> str:
    if var_pct > THRESHOLD:
        return "BUY"
    if var_pct < -THRESHOLD:
        return "SELL"
    return "HOLD"


def build_windows():
    """Telecharge l'historique et construit les fenetres (contexte, prix actuel, prix futur)."""
    import yfinance as yf
    windows = []   # (symbol, np.array contexte float32, prix_actuel, prix_futur)
    for symbol in PAIRS:
        symbol = symbol.strip()
        try:
            df = yf.Ticker(symbol).history(period=PERIOD, interval=INTERVAL)
            closes = df["Close"].dropna().values.astype(np.float32)
            n = len(closes)
            if n < CONTEXT + HORIZON + 1:
                logger.warning(f"{symbol}: historique insuffisant ({n})")
                continue
            count = 0
            for i in range(CONTEXT, n - HORIZON, STEP):
                ctx = closes[i - CONTEXT:i]
                windows.append((symbol, ctx, float(closes[i - 1]), float(closes[i - 1 + HORIZON])))
                count += 1
            logger.info(f"{symbol}: {n} bougies -> {count} fenetres")
        except Exception as e:
            logger.error(f"{symbol}: {e}")
    return windows


def score(track, model_key, wins_tests):
    for symbol, pred_dir, actual_dir in wins_tests:
        if pred_dir in ("BUY", "SELL"):
            record_result(track, model_key, symbol, pred_dir == actual_dir)


def run_timesfm(track, windows):
    from src.timesfm_predictor import _load_model, unload_timesfm
    model = _load_model()
    if model is None:
        logger.error("TimesFM indisponible"); return
    results = []
    for b in range(0, len(windows), BATCH):
        batch = windows[b:b + BATCH]
        try:
            point, _ = model.forecast(horizon=HORIZON, inputs=[w[1] for w in batch])
            for (symbol, _, cur, fut), preds in zip(batch, point):
                target = float(preds[min(HORIZON - 1, len(preds) - 1)])
                results.append((symbol, _direction((target - cur) / cur * 100),
                                _direction((fut - cur) / cur * 100)))
        except Exception as e:
            logger.error(f"TimesFM batch {b}: {e}")
        if b % (BATCH * 20) == 0:
            logger.info(f"TimesFM: {b}/{len(windows)}")
    score(track, "TFM", results)
    unload_timesfm(); gc.collect()


def run_chronos(track, windows):
    import torch
    from src.chronos_predictor import _load_pipeline, unload_chronos
    pipe = _load_pipeline()
    if pipe is None:
        logger.error("Chronos indisponible"); return
    results = []
    for b in range(0, len(windows), BATCH):
        batch = windows[b:b + BATCH]
        try:
            ctx = torch.tensor(np.stack([w[1] for w in batch]), dtype=torch.float32)
            fc = pipe.predict(ctx, HORIZON, num_samples=10)
            med = fc.median(dim=1).values.numpy()   # (batch, horizon)
            for (symbol, _, cur, fut), preds in zip(batch, med):
                target = float(preds[min(HORIZON - 1, len(preds) - 1)])
                results.append((symbol, _direction((target - cur) / cur * 100),
                                _direction((fut - cur) / cur * 100)))
        except Exception as e:
            logger.error(f"Chronos batch {b}: {e}")
        if b % (BATCH * 20) == 0:
            logger.info(f"Chronos: {b}/{len(windows)}")
    score(track, "CHO", results)
    unload_chronos(); gc.collect()


def _run_simple(track, windows, key, predict_fn, unload_fn, step_mult=1):
    results = []
    sub = windows[::step_mult]
    for i, (symbol, ctx, cur, fut) in enumerate(sub):
        try:
            preds = predict_fn(ctx)
            if preds is None or len(preds) == 0:
                continue
            target = float(preds[min(HORIZON - 1, len(preds) - 1)])
            results.append((symbol, _direction((target - cur) / cur * 100),
                            _direction((fut - cur) / cur * 100)))
        except Exception as e:
            logger.error(f"{key} fenetre {i}: {e}")
        if i % 200 == 0:
            logger.info(f"{key}: {i}/{len(sub)}")
    score(track, key, results)
    unload_fn(); gc.collect()


def main():
    logger.info("=== TIME MACHINE — entrainement accelere sur le passe ===")
    logger.info(f"Paires: {PAIRS} | periode: {PERIOD} | intervalle: {INTERVAL} | pas: {STEP}")
    windows = build_windows()
    logger.info(f"TOTAL: {len(windows)} fenetres a rejouer x 5 IA")
    if not windows:
        sys.exit(1)

    track = load_track(force=True)

    logger.info("── Passe 1/5 : Google TimesFM ──")
    run_timesfm(track, windows); save_track(track)

    logger.info("── Passe 2/5 : Amazon Chronos ──")
    run_chronos(track, windows); save_track(track)

    logger.info("── Passe 3/5 : Salesforce Moirai ──")
    from src.moirai_predictor import predict_moirai, unload_moirai
    _run_simple(track, windows, "MOI", predict_moirai, unload_moirai); save_track(track)

    logger.info("── Passe 4/5 : Lag-Llama ──")
    from src.lagllama_predictor import predict_lagllama, unload_lagllama
    _run_simple(track, windows, "LLA", predict_lagllama, unload_lagllama, step_mult=LLA_MULT); save_track(track)

    logger.info("── Passe 5/5 : IBM Granite TTM ──")
    from src.granite_predictor import predict_granite, unload_granite
    _run_simple(track, windows, "GRA", predict_granite, unload_granite); save_track(track)

    logger.info(f"=== TERMINE — Carnet de notes : {accuracy_summary(track)} ===")


if __name__ == "__main__":
    main()
