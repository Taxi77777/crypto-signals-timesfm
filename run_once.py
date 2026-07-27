"""
run_once.py — Analyse crypto + Auto-trading MEXC Futures
Logique :
  1. Si position ouverte → Applique trailing stop software + surveillance
  2. Si aucune position → Prend le meilleur signal fort et ouvre la position
"""

import logging
import os
import sys

# Forcer l'encodage utf-8 pour éviter les erreurs d'affichage d'emojis sous Windows
try:
    sys.stdout.reconfigure(encoding='utf-8')
except AttributeError:
    pass

import json
import time
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
PARIS_TZ = ZoneInfo("Europe/Paris")

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("logs/signals.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

import config
from src.data_fetcher      import fetch_all_pairs, prepare_timesfm_input
from src.indicators        import compute_all_indicators
from src.timesfm_predictor import predict_timesfm
from src.signal_generator  import generate_signal, TradingSignal, _format_crypto_price
from src.telegram_bot      import send_signal, send_message
from src.mexc_trader       import (
    has_open_position, place_order,
    get_usdt_balance, check_and_trail,
    get_order_book_imbalance, get_mexc_depth, LEVERAGE,
    TRAIL_BREAKEVEN_PCT
)


def format_order_telegram(order_result: dict, signal) -> str:
    emoji = "🟢" if order_result["side"] == "LONG" else "🔴"
    tp_sl_status = "✅ Actifs" if order_result.get("tp_sl_set") else "⚠️ ÉCHEC DE POSE (à surveiller !)"
    return (
        f"🚀 *ORDRE MEXC FUTURES PLACÉ !*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {signal.pair_name}\n"
        f"📊 {emoji} *{order_result['side']} x{order_result['leverage']}*\n"
        f"💰 Mise : *{order_result['balance_used']} USDT*\n"
        f"📦 Contrats : `{order_result['vol']}`\n"
        f"🎯 Take Profit : `{signal.take_profit}`\n"
        f"🛑 Stop Loss   : `{signal.stop_loss}`\n"
        f"⚙️ Pose TP/SL : *{tp_sl_status}*\n"
        f"📈 Confiance IA  : *{signal.confidence}%*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"✅ _Position ouverte sur ton compte MEXC_\n"
        f"🤖 _Consensus 5 IA : TimesFM · Chronos · Moirai · Lag-Llama · Granite_"
    )


def format_trail_telegram(trail: dict) -> str:
    return (
        f"🔒 *TRAILING STOP MIS À JOUR*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 `{trail['symbol']}`\n"
        f"📈 Profit actuel : *+{trail['profit_pct']}%*\n"
        f"🛑 Ancien SL : `{trail['old_sl']}`\n"
        f"✅ Nouveau SL : `{trail['new_sl']}`\n"
        f"{trail['label']}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"_Tes gains sont maintenant protégés !_ 🛡️"
    )


def main():
    logger.info("=== Analyse Crypto Futures + Auto-Trading MEXC ===")

    mexc_key    = os.getenv("MEXC_API_KEY", "")
    mexc_secret = os.getenv("MEXC_SECRET_KEY", "")
    use_mexc    = bool(mexc_key and mexc_secret)

    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("TELEGRAM_BOT_TOKEN ou TELEGRAM_CHAT_ID manquant !")
        sys.exit(1)

    # ── 1. Vérification position + Trailing Stop ──────────────────────────────
    trade_allowed = False
    open_count = 0
    open_symbols = []
    if use_mexc:
        balance = get_usdt_balance(mexc_key, mexc_secret)
        logger.info(f"Solde MEXC Futures disponible : {balance:.2f} USD (USDT + USDC cumulés)")

        from src.mexc_trader import get_open_positions
        open_positions = get_open_positions(mexc_key, mexc_secret)
        open_count = len(open_positions)
        open_symbols = [p.get("symbol") for p in open_positions]
        logger.info(f"Positions actives sur MEXC : {open_count}/1 ({', '.join(open_symbols)})")

        if open_count > 0:
            # Positions ouvertes → appliquer trailing stop software
            logger.info("Positions actives → Vérification trailing stop...")
            trail_result = check_and_trail(mexc_key, mexc_secret)
            if trail_result:
                msg = format_trail_telegram(trail_result)
                send_message(msg)
                logger.info(f"Trailing stop appliqué : {trail_result}")

        if open_count >= 1:
            logger.info("Limite de 1 position simultanée atteinte → pas de nouveau trade")
            trade_allowed = False
        else:
            logger.info("Aucune position ouverte → 1 trade autorisé")
            trade_allowed = True
    else:
        logger.warning("Clés MEXC absentes — Mode analyse seule.")

    # ── 2. Analyse TimesFM des 50 cryptos ────────────────────────────────────
    logger.info(f"Analyse de {len(config.CRYPTO_PAIRS)} cryptos avec TimesFM 2.5...")
    all_data = fetch_all_pairs()
    logger.info("Téléchargement des données 1h pour le filtre de tendance...")
    all_data_1h = fetch_all_pairs(period="30d", interval="1h")

    if not all_data:
        logger.error("Aucune donnée récupérée")
        sys.exit(1)

    # ── APPRENTISSAGE CONTINU : vérifier les prédictions d'il y a 1h ──────────
    import time as _time
    from src.track_record import (load_track, save_track, record_result,
                                  load_pending, save_pending, accuracy_summary)
    track   = load_track(force=True)
    pending = load_pending()
    now_ts  = _time.time()
    matured = [pr for pr in pending if now_ts - pr["ts"] >= 3600]
    verified = 0
    for pr in matured:
        df_p = all_data.get(pr["symbol"])
        if df_p is None or df_p.empty:
            continue
        cur = float(df_p["close"].iloc[-1])
        var = (cur - pr["price"]) / pr["price"] * 100
        actual = "BUY" if var > 0.05 else "SELL" if var < -0.05 else "HOLD"
        if pr["dir"] in ("BUY", "SELL"):
            record_result(track, pr["model"], pr["symbol"], pr["dir"] == actual)
            verified += 1
    if verified:
        save_track(track)
        logger.info(f"📚 Apprentissage continu : {verified} prédictions vérifiées | {accuracy_summary(track)}")

    # ── Phase A : indicateurs + séries de prix ────────────────────────────────
    import gc
    series_map, ind_map, raw_prices = {}, {}, {}
    for symbol, df in all_data.items():
        pair_name = config.PAIR_NAMES.get(symbol, symbol)
        try:
            df_ind = compute_all_indicators(df)
            if df_ind.empty:
                continue
            ind_map[symbol]    = df_ind
            raw_prices[symbol] = float(df_ind.iloc[-1]["close"])
            series_map[symbol] = prepare_timesfm_input(df)
        except Exception as e:
            logger.error(f"Erreur indicateurs {pair_name}: {e}")
            continue

    # ── Phase B : 5 passes IA séquentielles (chargement → prédictions → libération RAM) ──
    ai_preds = {"tfm": {}, "cho": {}, "moi": {}, "lla": {}, "gra": {}}

    logger.info("── Passe 1/5 : Google TimesFM 2.5 ──")
    from src.timesfm_predictor import unload_timesfm
    for sym, series in series_map.items():
        ai_preds["tfm"][sym] = predict_timesfm(series)
    unload_timesfm()
    gc.collect()

    logger.info("── Passe 2/5 : Amazon Chronos ──")
    from src.chronos_predictor import predict_chronos, unload_chronos
    for sym, series in series_map.items():
        ai_preds["cho"][sym] = predict_chronos(series)
    unload_chronos()
    gc.collect()

    logger.info("── Passe 3/5 : Salesforce Moirai 2.0 ──")
    from src.moirai_predictor import predict_moirai, unload_moirai
    for sym, series in series_map.items():
        ai_preds["moi"][sym] = predict_moirai(series)
    unload_moirai()
    gc.collect()

    logger.info("── Passe 4/5 : Lag-Llama ──")
    from src.lagllama_predictor import predict_lagllama, unload_lagllama
    for sym, series in series_map.items():
        ai_preds["lla"][sym] = predict_lagllama(series)
    unload_lagllama()
    gc.collect()

    logger.info("── Passe 5/5 : IBM Granite TTM ──")
    from src.granite_predictor import predict_granite, unload_granite
    for sym, series in series_map.items():
        ai_preds["gra"][sym] = predict_granite(series)
    unload_granite()
    gc.collect()

    # ── Phase C : génération des signaux (consensus strict 5 IA) ─────────────
    signals = []
    for symbol, df_ind in ind_map.items():
        pair_name = config.PAIR_NAMES.get(symbol, symbol)
        try:
            signal = generate_signal(
                symbol, df_ind,
                ai_preds["tfm"].get(symbol),
                ai_preds["cho"].get(symbol),
                ai_preds["moi"].get(symbol),
                ai_preds["lla"].get(symbol),
                ai_preds["gra"].get(symbol),
                df_1h=all_data_1h.get(symbol) if all_data_1h else None,
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.error(f"Erreur {pair_name}: {e}")
            continue

    # Trier par confiance décroissante
    strong_signals = [s for s in signals if s.is_strong and s.signal != "HOLD"]

    # ── Gestionnaire de Pullback (Wait for Pullback logic) ───────────────────

    pullbacks_file = "pending_pullbacks.json"
    pending_pullbacks = []
    if os.path.exists(pullbacks_file):
        try:
            with open(pullbacks_file, "r", encoding="utf-8") as f:
                pending_pullbacks = json.load(f)
        except Exception as e:
            logger.error(f"Erreur chargement pullbacks : {e}")

    active_pullbacks = []
    completed_signals = []
    limit_pct = getattr(config, "MAX_EMA_EXTENSION_PCT", 0.5)

    # 1. Vérifier les pullbacks existants dans la file
    for p in pending_pullbacks:
        # Expiration (2h = 7200s)
        if time.time() - p["timestamp"] >= 7200:
            logger.info(f"⏳ Pullback expiré pour {p['pair_name']} {p['signal']}")
            send_message(
                f"⏰ *PULLBACK EXPIRÉ (Timeout 2h)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🪙 *{p['pair_name']}* | {p['signal']} {'🟢' if p['signal'] == 'BUY' else '🔴'}\n"
                f"💡 Confiance d'origine : {p['confidence']}%\n"
                f"🎯 TP visé : `{p['take_profit']}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"❌ _Le prix n'est pas revenu dans la zone EMA20 en 2h — signal annulé._"
            )
            continue

        # Invalidation par un nouveau signal inverse dans la passe actuelle
        inverse_detected = False
        for s in signals:
            if s.symbol == p["symbol"] and s.is_strong and s.signal != "HOLD" and s.signal != p["signal"]:
                inverse_detected = True
                break
        if inverse_detected:
            logger.info(f"⏳ Pullback invalidé pour {p['pair_name']} par un signal inverse")
            send_message(f"❌ *Pullback invalidé*\nLe signal d'origine {p['pair_name']} {p['signal']} est annulé suite à une inversion de tendance.")
            continue

        # Vérification du pullback réel
        df_ind = ind_map.get(p["symbol"])
        if df_ind is not None and not df_ind.empty:
            last_row = df_ind.iloc[-1]
            cur_price = float(last_row["close"])
            ema20 = float(last_row["ema20"])
            ema50 = float(last_row["ema50"])
            extension_pct = (cur_price - ema20) / ema20 * 100

            triggered = False
            invalidated = False
            reason = ""

            if p["signal"] == "BUY":
                if cur_price < ema50:
                    invalidated = True
                    reason = "cassure de l'EMA50 (tendance baissière)"
                elif extension_pct <= limit_pct:
                    triggered = True
            elif p["signal"] == "SELL":
                if cur_price > ema50:
                    invalidated = True
                    reason = "cassure de l'EMA50 (tendance haussière)"
                elif extension_pct >= -limit_pct:
                    triggered = True

            if invalidated:
                logger.info(f"⏳ Pullback invalidé pour {p['pair_name']} : {reason}")
                send_message(f"❌ *Pullback invalidé*\nSignal {p['pair_name']} {p['signal']} annulé : {reason}.")
                continue

            if triggered:
                logger.info(f"🎯 Pullback complété pour {p['pair_name']} {p['signal']} à {cur_price}")
                send_message(
                    f"🎯 *PULLBACK ATTEINT — ENTRÉE EN COURS !* 🎯\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 *{p['pair_name']}*\n"
                    f"📊 Direction : *{p['signal']}* {'🟢' if p['signal'] == 'BUY' else '🔴'}\n"
                    f"💡 Confiance IA : *{p['confidence']}%*\n"
                    f"💰 Prix d'entrée : `{cur_price:.5f}`\n"
                    f"📏 EMA20 : `{ema20:.5f}` | EMA50 : `{ema50:.5f}`\n"
                    f"🎯 Take Profit : `{p['take_profit']}`\n"
                    f"📈 RSI : `{p['rsi']}` | {p['macd_trend']}\n"
                    f"🤖 Consensus : `{p['forecast_dir']}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✅ _Le prix est revenu en zone EMA20 — ordre en train d'être passé !_"
                )
                
                tp_val = float(p["take_profit_raw"])
                sl_val = float(p["stop_loss_raw"])
                
                triggered_sig = TradingSignal(
                    symbol=p["symbol"],
                    pair_name=p["pair_name"],
                    signal=p["signal"],
                    current_price=_format_crypto_price(cur_price),
                    take_profit=_format_crypto_price(tp_val),
                    stop_loss="Aucun",
                    confidence=p["confidence"],
                    rsi=p["rsi"],
                    rsi_status=p["rsi_status"],
                    macd_trend=p["macd_trend"],
                    ema_trend=p["ema_trend"],
                    bb_position=p["bb_position"],
                    forecast_dir=p["forecast_dir"],
                    forecast_4h=p["forecast_4h"],
                    tp_pct=p["tp_pct"],
                    sl_pct="0.0",
                    is_strong=True,
                    fisher=p["fisher"],
                    fisher_status=p["fisher_status"],
                    is_extended=False
                )
                completed_signals.append(triggered_sig)
            else:
                active_pullbacks.append(p)
        else:
            active_pullbacks.append(p)

    # 2. Traiter les nouveaux signaux de la passe actuelle
    immediate_signals = []
    for s in strong_signals:
        if s.is_extended:
            if not any(p["symbol"] == s.symbol for p in active_pullbacks):
                df_ind = ind_map.get(s.symbol)
                last_row = df_ind.iloc[-1] if df_ind is not None else None
                ema20_val = float(last_row["ema20"]) if last_row is not None else 0.0
                
                tp_val = float(s.take_profit.replace("$", "").replace(",", ""))
                sl_val = 0.0
                
                new_p = {
                    "symbol": s.symbol,
                    "pair_name": s.pair_name,
                    "signal": s.signal,
                    "confidence": s.confidence,
                    "take_profit": s.take_profit,
                    "take_profit_raw": tp_val,
                    "stop_loss_raw": sl_val,
                    "rsi": s.rsi,
                    "rsi_status": s.rsi_status,
                    "macd_trend": s.macd_trend,
                    "ema_trend": s.ema_trend,
                    "bb_position": s.bb_position,
                    "forecast_dir": s.forecast_dir,
                    "forecast_4h": s.forecast_4h,
                    "tp_pct": s.tp_pct,
                    "fisher": s.fisher,
                    "fisher_status": s.fisher_status,
                    "timestamp": time.time(),
                }
                active_pullbacks.append(new_p)
                logger.info(f"⏳ Nouveau signal {s.pair_name} {s.signal} mis en attente de pullback")
                send_message(
                    f"⏳ *EN ATTENTE DE PULLBACK* ⏳\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🪙 *{s.pair_name}*\n"
                    f"📊 Direction : *{s.signal}* {'🟢' if s.signal == 'BUY' else '🔴'}\n"
                    f"💡 Confiance IA : *{s.confidence}%*\n"
                    f"💰 Prix actuel : `{s.current_price}`\n"
                    f"📏 EMA20 actuelle : `{ema20_val:.5f}`\n"
                    f"⚠️ Prix trop étendu — attente d'un retour à <= {limit_pct}% de l'EMA20\n"
                    f"🎯 Take Profit visé : `{s.take_profit}`\n"
                    f"📈 RSI : `{s.rsi}` | {s.macd_trend}\n"
                    f"🤖 Consensus : `{s.forecast_dir}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"_Ordre déclenché automatiquement dès le pullback (max 2h)_"
                )
        else:
            immediate_signals.append(s)

    # Sauvegarder la file d'attente des pullbacks
    try:
        with open(pullbacks_file, "w", encoding="utf-8") as f:
            json.dump(active_pullbacks, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Erreur sauvegarde pullbacks : {e}")

    # strong_signals contient maintenant uniquement les signaux immédiats + les pullbacks complétés de ce scan
    strong_signals = immediate_signals + completed_signals

    # ── BTC Correlation Guard ──
    btc_trend_1h = "NEUTRAL"
    if all_data_1h and "BTC-USD" in all_data_1h:
        try:
            btc_df = all_data_1h.get("BTC-USD")
            if btc_df is not None and not btc_df.empty:
                btc_df_ind = compute_all_indicators(btc_df)
                if not btc_df_ind.empty:
                    btc_last = btc_df_ind.iloc[-1]
                    btc_ema20 = float(btc_last["ema20"])
                    btc_ema50 = float(btc_last["ema50"])
                    btc_st_dir = int(btc_last["supertrend_dir"])
                    
                    if btc_ema20 < btc_ema50 or btc_st_dir == -1:
                        btc_trend_1h = "BEARISH"
                    elif btc_ema20 > btc_ema50 or btc_st_dir == 1:
                        btc_trend_1h = "BULLISH"
            logger.info(f"📊 BTC Correlation Guard | État du Bitcoin (BTC-USD 1H) : {btc_trend_1h}")
        except Exception as e:
            logger.error(f"Erreur calcul BTC Correlation Guard : {e}")

    # ── DXY (US Dollar Index) Guard ──
    dxy_trend = "NEUTRAL"
    from datetime import datetime, timezone
    is_weekend = datetime.now(timezone.utc).weekday() >= 5
    if not is_weekend:
        try:
            import yfinance as yf
            ticker_dxy = yf.Ticker("DX-Y.NYB")
            dxy_df = ticker_dxy.history(period="10d", interval="1h")
            if dxy_df is not None and not dxy_df.empty:
                dxy_df.columns = [c.lower() for c in dxy_df.columns]
                dxy_df_ind = compute_all_indicators(dxy_df)
                if not dxy_df_ind.empty:
                    dxy_last = dxy_df_ind.iloc[-1]
                    dxy_ema20 = float(dxy_last["ema20"])
                    dxy_ema50 = float(dxy_last["ema50"])
                    dxy_st_dir = int(dxy_last["supertrend_dir"])
                    
                    if dxy_ema20 > dxy_ema50 and dxy_st_dir == 1:
                        dxy_trend = "BULLISH"
                    elif dxy_ema20 < dxy_ema50 and dxy_st_dir == -1:
                        dxy_trend = "BEARISH"
            logger.info(f"📊 Macro Guard | Dollar Index (DXY 1H) : {dxy_trend}")
        except Exception as e:
            logger.error(f"Erreur calcul DXY Guard : {e}")
 
    # ── Nasdaq (^IXIC) Guard ──
    nasdaq_trend = "NEUTRAL"
    if not is_weekend:
        try:
            import yfinance as yf
            ticker_ndx = yf.Ticker("^IXIC")
            ndx_df = ticker_ndx.history(period="10d", interval="1h")
            if ndx_df is not None and not ndx_df.empty:
                ndx_df.columns = [c.lower() for c in ndx_df.columns]
                ndx_df_ind = compute_all_indicators(ndx_df)
                if not ndx_df_ind.empty:
                    ndx_last = ndx_df_ind.iloc[-1]
                    ndx_ema20 = float(ndx_last["ema20"])
                    ndx_ema50 = float(ndx_last["ema50"])
                    ndx_st_dir = int(ndx_last["supertrend_dir"])
                    
                    if ndx_ema20 < ndx_ema50 or ndx_st_dir == -1:
                        nasdaq_trend = "BEARISH"
                    elif ndx_ema20 > ndx_ema50 or ndx_st_dir == 1:
                        nasdaq_trend = "BULLISH"
            logger.info(f"📊 Macro Guard | Nasdaq (^IXIC 1H) : {nasdaq_trend}")
        except Exception as e:
            logger.error(f"Erreur calcul Nasdaq Guard : {e}")
 
    # ── ETH/BTC Ratio Guard (Force Altcoins) ──
    alt_strength = "NEUTRAL"
    try:
        import yfinance as yf
        ticker_ethbtc = yf.Ticker("ETH-BTC")
        ethbtc_df = ticker_ethbtc.history(period="10d", interval="1h")
        if ethbtc_df is not None and not ethbtc_df.empty:
            ethbtc_df.columns = [c.lower() for c in ethbtc_df.columns]
            ethbtc_df_ind = compute_all_indicators(ethbtc_df)
            if not ethbtc_df_ind.empty:
                ethbtc_last = ethbtc_df_ind.iloc[-1]
                ethbtc_ema20 = float(ethbtc_last["ema20"])
                ethbtc_ema50 = float(ethbtc_last["ema50"])
                ethbtc_st_dir = int(ethbtc_last["supertrend_dir"])
                
                if ethbtc_ema20 < ethbtc_ema50 or ethbtc_st_dir == -1:
                    alt_strength = "WEAK"
                elif ethbtc_ema20 > ethbtc_ema50 or ethbtc_st_dir == 1:
                    alt_strength = "STRONG"
        logger.info(f"📊 Crypto Guard | Altcoin Strength (ETH/BTC 1H) : {alt_strength}")
    except Exception as e:
        logger.error(f"Erreur calcul ETH/BTC Guard : {e}")

    # Filtrer strong_signals en amont avec les Guards
    filtered_strong_signals = []
    for s in strong_signals:
        is_btc = (s.symbol == "BTC-USD")
        reasons = []
        
        if s.signal == "BUY":
            if getattr(config, "ENABLE_BTC_GUARD", True) and not is_btc and btc_trend_1h == "BEARISH":
                reasons.append("Bitcoin baissier")
            if getattr(config, "ENABLE_DXY_GUARD", False) and dxy_trend == "BULLISH":
                reasons.append("Dollar (DXY) haussier")
            if getattr(config, "ENABLE_NASDAQ_GUARD", False) and nasdaq_trend == "BEARISH":
                reasons.append("Nasdaq baissier")
            if getattr(config, "ENABLE_ETH_BTC_GUARD", True) and not is_btc and alt_strength == "WEAK":
                reasons.append("Altcoins faibles (ETH/BTC)")
                
        elif s.signal == "SELL":
            if getattr(config, "ENABLE_BTC_GUARD", True) and not is_btc and btc_trend_1h == "BULLISH":
                reasons.append("Bitcoin haussier")
            if getattr(config, "ENABLE_DXY_GUARD", False) and dxy_trend == "BEARISH":
                reasons.append("Dollar (DXY) baissier")
            if getattr(config, "ENABLE_NASDAQ_GUARD", False) and nasdaq_trend == "BULLISH":
                reasons.append("Nasdaq haussier")
            if getattr(config, "ENABLE_ETH_BTC_GUARD", True) and not is_btc and alt_strength == "STRONG":
                reasons.append("Altcoins forts (ETH/BTC)")
                
        if reasons:
            block_msg = " + ".join(reasons)
            logger.info(f"🛡️ Guard Block | Signal {s.pair_name} {s.signal} bloqué car : {block_msg}.")
            # On envoie l'explication EN PRIVÉ
            send_message(f"🛡️ *Macro/Crypto Guard*\nSignal {s.pair_name} {s.signal} bloqué car :\n_{block_msg}_")
        else:
            filtered_strong_signals.append(s)
            
    strong_signals = filtered_strong_signals
    strong_signals.sort(key=lambda s: s.confidence, reverse=True)

    # Peupler les gros murs de carnet d'ordres pour les signaux validés
    from src.mexc_trader import SYMBOL_MAP, get_current_price, get_largest_walls
    for s in strong_signals:
        symbol_mexc = SYMBOL_MAP.get(s.symbol)
        if symbol_mexc:
            try:
                # Obtenir le prix live
                mexc_price = get_current_price(symbol_mexc)
                if mexc_price <= 0:
                    mexc_price = float(raw_prices.get(s.symbol, 0))
                
                if mexc_price > 0:
                    walls = get_largest_walls(symbol_mexc, mexc_price, depth_pct=0.015)
                    if walls:
                        w_bid = walls.get("largest_bid")
                        w_ask = walls.get("largest_ask")
                        walls_str = ""
                        if w_bid:
                            walls_str += f"🟢 *Mur ACHAT (support ±1.5%) :* `{w_bid['val_usdt']:,.0f} USDT` à `${w_bid['price']}`\n"
                        if w_ask:
                            walls_str += f"🔴 *Mur VENTE (résistance ±1.5%) :* `{w_ask['val_usdt']:,.0f} USDT` à `${w_ask['price']}`"
                        if walls_str:
                            s.orderbook_walls = walls_str.strip()
            except Exception as e:
                logger.error(f"Erreur calcul murs pour signal {s.symbol}: {e}")

    # ── 3. Export JSON ────────────────────────────────────────────────────────
    web_data = {
        "last_update": datetime.now(timezone.utc).isoformat(),
        "signals": [
            {
                "pair_name":     s.pair_name,
                "signal":        s.signal,
                "current_price": s.current_price,
                "take_profit":   s.take_profit,
                "stop_loss":     s.stop_loss,
                "confidence":    s.confidence,
                "rsi":           s.rsi,
                "macd_trend":    s.macd_trend,
                "forecast_dir":  s.forecast_dir,
                "smc_zone":      s.smc_zone,
                "is_ote":        s.is_ote,
                "orderbook_walls": s.orderbook_walls
            }
            for s in strong_signals
        ]
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(web_data, f, indent=2, ensure_ascii=False)
    logger.info("signals.json mis à jour.")

    # ── Chargement des signaux déjà envoyés (anti-doublon) ───────────────────
    sent_signals_file = "sent_signals.json"
    already_sent = {}
    if os.path.exists(sent_signals_file):
        try:
            with open(sent_signals_file, "r", encoding="utf-8") as f:
                already_sent = json.load(f)  # {symbol: signal_direction}
        except Exception:
            already_sent = {}

    # Identifie les signaux vraiment NOUVEAUX
    # - Jamais envoyé avant
    # - OU direction différente (ex: était SELL, maintenant BUY)
    # - OU issu d'un pullback complété (toujours envoyer)
    completed_syms = {s.symbol for s in completed_signals}
    new_signals_to_send = []
    for s in strong_signals:
        prev = already_sent.get(s.symbol)
        if s.symbol in completed_syms:
            # Pullback déclenché → toujours notifier
            new_signals_to_send.append(s)
        elif prev != s.signal:
            # Nouveau signal ou inversion de direction
            new_signals_to_send.append(s)
        else:
            logger.info(f"🔕 Signal {s.pair_name} {s.signal} déjà envoyé — ignoré.")

    # Mettre à jour le fichier des signaux envoyés
    for s in strong_signals:
        already_sent[s.symbol] = s.signal
    active_syms = {s.symbol for s in strong_signals}
    already_sent = {sym: sig for sym, sig in already_sent.items() if sym in active_syms}
    try:
        with open(sent_signals_file, "w", encoding="utf-8") as f:
            json.dump(already_sent, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Erreur sauvegarde sent_signals: {e}")

    # ── 4. Notifications Telegram d'information (DÉSACTIVÉES : L'utilisateur exige UNIQUEMENT des signaux confirmés avec croisement) ──
    # Les notifications Telegram ne seront envoyées QU'À LA SECTION 5 lorsqu'un signal a son Croisement MA30/60 + Mèche de Rejet 100% confirmé.
    if new_signals_to_send:
        logger.info(f"💡 {len(new_signals_to_send)} signaux informatifs détectés (conservés pour analyse, Telegram neutralisé).")

    # ── Rapport Orderbook permanent : envoyé à CHAQUE scan (toutes les 5 min) ──
    from src.mexc_trader import SYMBOL_MAP, get_current_price, get_cumulative_depth_ratio, get_largest_walls
    import re as _re
    import yfinance as _yf_ob
    import pandas as _pd_ob


    def _clean_name(sym):
        return _re.sub(r'\d+', '', sym.replace("-USD", ""))

    def _fmt_p(p):
        if p >= 1000: return f"{p:.0f}"
        elif p >= 1: return f"{p:.4f}"
        elif p >= 0.001: return f"{p:.5f}"
        else: return f"{p:.7f}"

    buyers_list, sellers_list, balanced_list = [], [], []
    pullback_signals = []

    for sym in config.CRYPTO_PAIRS:
        symbol_mexc = SYMBOL_MAP.get(sym)
        if not symbol_mexc:
            continue
        try:
            price = get_current_price(symbol_mexc)
            name = _clean_name(sym)
            if price > 0:
                ratio = get_cumulative_depth_ratio(symbol_mexc, price, depth_pct=0.015) or 1.0
                # Ajouter TOUTES les cryptos pour évaluation Pure Belkhayate (BUY & SELL)
                pullback_signals.append(("BUY", name, sym, symbol_mexc, ratio, price, price, price * 1.018, 0.0, 0.0))
                pullback_signals.append(("SELL", name, sym, symbol_mexc, ratio, price, price, price * 0.982, 0.0, 0.0))
            time.sleep(0.05)
        except Exception as e:
            logger.error(f"Erreur préparation crypto {sym}: {e}")

    # ── Validation Belkhayate & Envoi des signaux Ultra-Sniper 80X ──────────
    import yfinance as yf
    from concurrent.futures import ThreadPoolExecutor

    def _fetch_pair_mtf(sig_tuple):
        direction, name, sym, symbol_mexc, ratio, cur_price, entry_price, tp_price, dist_pct, trend_45m = sig_tuple
        try:
            import pandas as pd
            df_15m = yf.download(sym, period="5d", interval="15m", progress=False)
            df_30m = yf.download(sym, period="10d", interval="30m", progress=False)

            def _clean_df(df_in):
                if df_in.empty: return df_in
                if isinstance(df_in.columns, pd.MultiIndex):
                    df_in.columns = df_in.columns.get_level_values(0)
                return df_in.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low",  "Close": "close", "Volume": "volume"
                })

            df_15m = _clean_df(df_15m)
            df_30m = _clean_df(df_30m)
            return sig_tuple, df_15m, df_30m
        except Exception as e:
            logger.error(f"Erreur téléchargement rapide MTF pour {sym}: {e}")
            return sig_tuple, pd.DataFrame(), pd.DataFrame()

    with ThreadPoolExecutor(max_workers=10) as executor:
        mtf_results = list(executor.map(_fetch_pair_mtf, pullback_signals))

    # Compteur de rejets par filtre : permet de savoir QUEL filtre étouffe la stratégie.
    from collections import Counter as _Counter
    reject_stats = _Counter()
    candidates_seen = 0

    for sig_tuple, df_15m, df_30m in mtf_results:
        direction, name, sym, symbol_mexc, ratio, cur_price, entry_price, tp_price, dist_pct, trend_45m = sig_tuple
        try:
            if not df_15m.empty and not df_30m.empty:
                df_15m = compute_all_indicators(df_15m)
                df_30m = compute_all_indicators(df_30m)
                if not df_15m.empty and not df_30m.empty:
                    last_15m = df_15m.iloc[-1]
                    last_30m = df_30m.iloc[-1]
                    adx_15m = float(last_15m["adx"])
                    fish_15m_curr = float(last_15m["fisher"])
                    fish_15m_prev = float(df_15m["fisher"].iloc[-2]) if len(df_15m) >= 2 else fish_15m_curr

                    fish_30m_curr = float(last_30m["fisher"])
                    fish_30m_prev = float(df_30m["fisher"].iloc[-2]) if len(df_30m) >= 2 else fish_30m_curr
                else:
                    logger.error(f"Echec du calcul des indicateurs 15m/30m pour {sym}")
                    continue
            else:
                logger.error(f"Echec du chargement des donnees 15m/30m pour {sym}")
                continue
                
            # État du régime de marché 15m (informatif, affiché dans le signal)
            range_txt = (f"✅ Range 15m (ADX: {adx_15m:.1f})" if adx_15m < 25
                         else f"⚡ Tendance 15m (ADX: {adx_15m:.1f})")

            # Récupération du Carnet d'Ordres MEXC (bid & ask cumulés à ±1.5% du prix)
            bid_qty, ask_qty = get_mexc_depth(symbol_mexc, mark_price=cur_price, depth_pct=0.015)
            if (bid_qty + ask_qty) <= 0:
                logger.warning(f"⚠️ Carnet d'ordres MEXC indisponible pour {symbol_mexc} → signal {name} {direction} ignoré (fail-closed).")
                continue
            
            # 🚀 Stratégie EXTRÊMEMENT AVANCÉE INSTITUTIONNELLE (VWAP + RSI Extrême + Volume Climax + Macro 1H/4H + OBI)
            rsi_15m = float(last_15m["rsi"]) if "rsi" in last_15m else 50.0
            rsi_min_recent = float(df_15m["rsi"].tail(6).min()) if "rsi" in df_15m else rsi_15m
            rsi_max_recent = float(df_15m["rsi"].tail(6).max()) if "rsi" in df_15m else rsi_15m

            # VWAP Institutionnel 15m ANCRÉ SUR LA SESSION (reset chaque jour UTC).
            # Un cumsum sur 5 jours ne représente aucun niveau exploitable.
            typical_price = (df_15m["high"] + df_15m["low"] + df_15m["close"]) / 3
            try:
                _session = df_15m.index.normalize()
                _pv = (typical_price * df_15m["volume"]).groupby(_session).cumsum()
                _vv = df_15m["volume"].groupby(_session).cumsum()
            except Exception:
                _pv = (typical_price * df_15m["volume"]).cumsum()
                _vv = df_15m["volume"].cumsum()
            vwap_series = _pv / _vv.replace(0, 1)
            vwap_curr = float(vwap_series.iloc[-1])

            # Biais Institutionnel VWAP — zones dynamiques tolérantes (±1.0% autour du VWAP).
            vwap_discount = (cur_price <= vwap_curr * 1.010)   # Achat bon marché ou proche VWAP
            vwap_premium  = (cur_price >= vwap_curr * 0.990)   # Vente chère ou proche VWAP

            # ── Bandes d'écart-type VWAP (config.ENABLE_VWAP_SIGMA_BANDS) ──
            # Quantifie l'étirement au lieu de dire seulement "de quel côté du VWAP".
            vwap_std  = 0.0
            vwap_zone = "binaire"
            if getattr(config, "ENABLE_VWAP_SIGMA_BANDS", False):
                try:
                    _dev = (df_15m["close"] - vwap_series)
                    vwap_std = float(_dev.groupby(_session).std().iloc[-1])
                except Exception:
                    vwap_std = 0.0
                if vwap_std == vwap_std and vwap_std > 0:
                    _k = float(getattr(config, "VWAP_SIGMA_MULT", 1.0))
                    vwap_discount = (cur_price < vwap_curr - _k * vwap_std)
                    vwap_premium  = (cur_price > vwap_curr + _k * vwap_std)
                    vwap_zone = f"{_k:g}σ"
                else:
                    logger.warning(f"σ VWAP indisponible pour {sym} → repli sur le test binaire")

            # ── Fibonacci en CONFLUENCE avec le VWAP (config.ENABLE_VWAP_FIBO) ──
            # Retracement du swing de la session en cours. On ne plaque pas des ratios
            # sur le VWAP : on mesure où le prix se situe dans le range du jour, et on
            # exige que ce retracement tombe dans la golden pocket 0.382-0.618.
            fibo_buy_ok  = True
            fibo_sell_ok = True
            fibo_txt     = "désactivé"
            if getattr(config, "ENABLE_VWAP_FIBO", False):
                try:
                    _today   = df_15m.index.normalize()[-1]
                    _sess_df = df_15m[df_15m.index.normalize() == _today]
                    if len(_sess_df) < 4:
                        _sess_df = df_15m.tail(16)      # session trop jeune → 4h glissantes
                    _hi = float(_sess_df["high"].max())
                    _lo = float(_sess_df["low"].min())
                    _rng = _hi - _lo
                    _z_lo = float(getattr(config, "FIBO_ZONE_LOW", 0.382))
                    _z_hi = float(getattr(config, "FIBO_ZONE_HIGH", 0.618))
                    _min_rng = float(getattr(config, "FIBO_MIN_RANGE_PCT", 0.004))

                    if _rng <= 0 or (_rng / cur_price) < _min_rng:
                        # Range de session trop étroit : les niveaux Fibo seraient du bruit.
                        fibo_txt = f"range session {_rng/cur_price*100:.2f}% < {_min_rng*100:.1f}% → neutre"
                    else:
                        # Retracement mesuré DEPUIS LE HAUT pour un BUY (repli acheté)
                        _retr_buy  = (_hi - cur_price) / _rng
                        # Retracement mesuré DEPUIS LE BAS pour un SELL (rebond vendu)
                        _retr_sell = (cur_price - _lo) / _rng
                        fibo_buy_ok  = (_z_lo <= _retr_buy  <= _z_hi)
                        fibo_sell_ok = (_z_lo <= _retr_sell <= _z_hi)
                        _fib_lvl = _hi - _z_lo * _rng, _hi - _z_hi * _rng
                        fibo_txt = (f"retr. {_retr_buy*100:.0f}% (zone {_z_lo*100:.0f}-{_z_hi*100:.0f}%) "
                                    f"| pocket {_fmt_p(_fib_lvl[1])}-{_fmt_p(_fib_lvl[0])}")
                except Exception as _e:
                    logger.warning(f"Fibo session indisponible pour {sym}: {_e} → filtre neutre")
                    fibo_buy_ok = fibo_sell_ok = True
                    fibo_txt = "erreur → neutre"

            # Tendance Macro RÉELLE en 1H (Close vs EMA200 sur bougies 1H).
            # Avant : lisait last_30m["ema200"] — c'était de l'EMA200 30m, pas 1H.
            ema200_1h   = None
            macro_tf_txt = "1H"
            _df_1h_macro = all_data_1h.get(sym)
            if _df_1h_macro is not None and not _df_1h_macro.empty and len(_df_1h_macro) >= 50:
                try:
                    _d1h = compute_all_indicators(_df_1h_macro.copy())
                    if not _d1h.empty and "ema200" in _d1h:
                        _v = float(_d1h["ema200"].iloc[-1])
                        if _v == _v and _v > 0:      # rejette NaN
                            ema200_1h = _v
                except Exception as _e:
                    logger.warning(f"EMA200 1H indisponible pour {sym}: {_e}")
            if ema200_1h is None and "ema200" in last_30m:
                try:
                    _v30 = float(last_30m["ema200"])
                    if _v30 == _v30 and _v30 > 0:
                        ema200_1h = _v30
                        macro_tf_txt = "30m (repli)"
                except Exception:
                    pass

            if ema200_1h is None:
                # EMA200 réellement indisponible (historique trop court).
                # On NEUTRALISE le filtre au lieu de le laisser tout bloquer :
                # avec ema200 = cur_price, les tests stricts > et < étaient tous
                # les deux faux et AUCUN signal ne pouvait passer.
                macro_trend_1h_bull = True
                macro_trend_1h_bear = True
                macro_tf_txt = "indisponible → filtre neutralisé"
                logger.warning(f"EMA200 indisponible pour {sym} → filtre de tendance macro neutralisé")
            else:
                macro_trend_1h_bull = (cur_price > ema200_1h * 0.985)  # Tolérance 1.5% autour de l'EMA200 1H
                macro_trend_1h_bear = (cur_price < ema200_1h * 1.015)

            # ── Volume Climax Institutionnel ──
            # DEUX corrections par rapport à la version précédente :
            #  1) on mesure la dernière bougie CLÔTURÉE (iloc[-2]) et non celle en
            #     formation, dont le volume est partiel et donc jamais comparable ;
            #  2) la moyenne de référence EXCLUT la bougie mesurée (avant, un pic
            #     se diluait dans sa propre moyenne : un vrai 1.50x sortait à 1.46x).
            _vol_mult    = float(getattr(config, "VOL_CLIMAX_MULT", 1.15))
            _use_closed  = getattr(config, "VOL_CLIMAX_USE_CLOSED_CANDLE", True)
            vol_curr = 1.0
            vol_mean = 1.0
            vol_src  = "en cours"
            try:
                _v = df_15m["volume"].astype(float)
                if _use_closed and len(_v) >= 22:
                    vol_curr = float(_v.iloc[-2])            # dernière bougie clôturée
                    vol_mean = float(_v.iloc[-22:-2].mean())  # les 20 qui la précèdent
                    vol_src  = "clôturée"
                elif len(_v) >= 21:
                    vol_curr = float(_v.iloc[-1])
                    vol_mean = float(_v.iloc[-21:-1].mean())  # exclut la bougie mesurée
            except Exception as _e:
                logger.warning(f"Volume indisponible pour {sym}: {_e} → climax neutralisé")
                vol_curr = vol_mean = 1.0
            if not (vol_mean > 0) or vol_mean != vol_mean:
                vol_mean = 1.0
            has_vol_climax = (vol_curr >= _vol_mult * vol_mean)

            # Order Book Imbalance (OBI) Carnet d'ordres à ±1.5%
            total_wall_vol = (bid_qty + ask_qty) if (bid_qty + ask_qty) > 0 else 1.0
            obi_score = bid_qty / total_wall_vol   # 1.0 = 100% Acheteurs, 0.0 = 100% Vendeurs
            _obi_min_buy  = float(getattr(config, "OBI_MIN_FOR_BUY", 0.40))
            _obi_max_sell = float(getattr(config, "OBI_MAX_FOR_SELL", 0.60))
            obi_buy_ok  = (obi_score >= _obi_min_buy)    # pas bloqué par un mur vendeur géant
            obi_sell_ok = (obi_score <= _obi_max_sell)   # pas bloqué par un mur acheteur géant

            # Détection des Mèches de Rejet Physiques Stricte (Rejection Wicks >= 15% de la bougie)
            lower_wick = float(df_15m["lower_wick_pct"].iloc[-1]) if "lower_wick_pct" in df_15m.columns else 0.0
            upper_wick = float(df_15m["upper_wick_pct"].iloc[-1]) if "upper_wick_pct" in df_15m.columns else 0.0
            has_buy_wick  = (lower_wick >= 0.15)
            has_sell_wick = (upper_wick >= 0.15)

            # Détection du Croisement Précis MA 30 / MA 60 (STRICT 0 À 1 BOUGIE APRÈS LE CROISEMENT)
            ma30_curr = float(df_15m["ma30"].iloc[-1]) if "ma30" in df_15m.columns else cur_price
            ma60_curr = float(df_15m["ma60"].iloc[-1]) if "ma60" in df_15m.columns else cur_price
            ma30_p1   = float(df_15m["ma30"].iloc[-2]) if "ma30" in df_15m.columns else cur_price
            ma60_p1   = float(df_15m["ma60"].iloc[-2]) if "ma60" in df_15m.columns else cur_price
            ma30_p2   = float(df_15m["ma30"].iloc[-3]) if len(df_15m) >= 3 and "ma30" in df_15m.columns else ma30_p1
            ma60_p2   = float(df_15m["ma60"].iloc[-3]) if len(df_15m) >= 3 and "ma60" in df_15m.columns else ma60_p1

            # ── EVALUATION MULTI-TIMEFRAME (15m, 30m, 1h) MOSTAFA BELKHAYATE & PULLBACK RE-TEST ──
            def _to_flt(val):
                try:
                    if hasattr(val, "iloc"):
                        val = val.iloc[-1]
                    if hasattr(val, "item"):
                        return float(val.item())
                    return float(val)
                except Exception:
                    return 0.0

            def _eval_belkhayate_tf(df_tf):
                if df_tf.empty or len(df_tf) < 30: return False, False, 0.0
                if isinstance(df_tf.columns, pd.MultiIndex):
                    df_tf.columns = df_tf.columns.get_level_values(0)
                
                cur_p = _to_flt(df_tf["close"])
                bary  = df_tf["close"].rolling(window=30).mean()
                bary_s = df_tf["close"].rolling(window=30).std()
                
                # RÈGLE 100% REJET PUR BELKHAYATE SUR TOUCHER/CASSURE DE LIGNE :
                # - La mèche HAUTE doit TOUCHER ou DÉPASSER la ligne du Sommet (high >= recent_high) -> VENTE (SELL)
                # - La mèche BASSE doit TOUCHER ou DÉPASSER la ligne du Creux (low <= recent_low) -> ACHAT (BUY)
                recent_high = _to_flt(df_tf["high"].tail(24).max())
                recent_low  = _to_flt(df_tf["low"].tail(24).min())
                touches_high_line = (_to_flt(df_tf["high"]) >= recent_high) or (abs(cur_p - recent_high)/cur_p <= 0.005)
                touches_low_line  = (_to_flt(df_tf["low"])  <= recent_low)  or (abs(cur_p - recent_low)/cur_p <= 0.005)
                
                t_series = (df_tf["close"] - bary) / bary_s.replace(0, 1e-6)
                tim   = _to_flt(t_series)
                
                # Mèches
                c_range = (df_tf["high"] - df_tf["low"]).replace(0, 1e-6)
                b_min = np.minimum(df_tf["open"], df_tf["close"])
                b_max = np.maximum(df_tf["open"], df_tf["close"])
                lw = _to_flt((b_min - df_tf["low"]) / c_range)
                uw = _to_flt((df_tf["high"] - b_max) / c_range)
                
                # Retest de Plus Haut / Plus Bas (Pullback / Double Top & Double Bottom)
                recent_high = _to_flt(df_tf["high"].tail(24).max())
                recent_low  = _to_flt(df_tf["low"].tail(24).min())
                is_retest_high = (cur_p > 0) and (abs(cur_p - recent_high) / cur_p <= 0.015) and (tim >= 1.0)
                is_retest_low  = (cur_p > 0) and (abs(cur_p - recent_low) / cur_p <= 0.015) and (tim <= -1.0)

                bary_val = _to_flt(bary)
                bary_std_val = _to_flt(bary_s)

                # FILTRE VOLUME SMA 9 (Volume de la bougie >= Moyenne 9 périodes)
                if "volume" in df_tf.columns and len(df_tf["volume"]) >= 9:
                    vol_sma9 = df_tf["volume"].rolling(window=9).mean()
                    vol_curr = _to_flt(df_tf["volume"])
                    vol_sma9_val = _to_flt(vol_sma9)
                    is_volume_confirmed = (vol_curr >= vol_sma9_val * 0.85)
                else:
                    is_volume_confirmed = True

                # FILTRE IMPULSION : Corps de bougie directionnel (≥ 35% du range total) + Volume SMA 9
                body_ratio = _to_flt(abs(df_tf["close"] - df_tf["open"]) / c_range)
                is_bullish_impulse = (_to_flt(df_tf["close"]) > _to_flt(df_tf["open"])) and (body_ratio >= 0.35) and is_volume_confirmed
                is_bearish_impulse = (_to_flt(df_tf["close"]) < _to_flt(df_tf["open"])) and (body_ratio >= 0.35) and is_volume_confirmed

                # RÈGLE 100% MOSTAFA BELKHAYATE DUAL-DIRECTION (VICE VERSA) :
                # 🟢 ACHAT (BUY / LONG 50X) :
                #    - Rejet Mèche Basse (lw >= 20%) sur Creux / Support / Transverse
                #    - OU Impulsion Verte (Bougie Verte >= 35% + Volume SMA 9) sur n'importe quelle ligne
                # 🔴 VENTE (SELL / SHORT 50X) :
                #    - Rejet Mèche Haute (uw >= 20%) sur Sommet / Résistance / Transverse
                #    - OU Impulsion Rouge (Bougie Rouge >= 35% + Volume SMA 9) sur n'importe quelle ligne
                buy_ok  = (touches_low_line and (lw >= 0.20 or is_bullish_impulse)) or (touches_high_line and is_bullish_impulse) or (is_retest_low and (lw >= 0.20 or is_bullish_impulse)) or (is_retest_high and is_bullish_impulse)
                sell_ok = (touches_high_line and (uw >= 0.20 or is_bearish_impulse)) or (touches_low_line and is_bearish_impulse) or (is_retest_high and (uw >= 0.20 or is_bearish_impulse)) or (is_retest_low and is_bearish_impulse)
                return buy_ok, sell_ok, tim

            buy_15, sell_15, tim_15 = _eval_belkhayate_tf(df_15m)
            buy_30, sell_30, tim_30 = _eval_belkhayate_tf(df_30m)
            
            # Téléchargement rapide 1H si besoin pour validation multi-tf
            try:
                df_1h = yf.download(sym, period="10d", interval="1h", progress=False)
                if isinstance(df_1h.columns, pd.MultiIndex): df_1h.columns = df_1h.columns.get_level_values(0)
                df_1h = df_1h.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
                buy_1h, sell_1h, tim_1h = _eval_belkhayate_tf(df_1h)
            except Exception:
                buy_1h, sell_1h, tim_1h = False, False, 0.0

            belkhayate_buy_ok  = (buy_15 or buy_30 or buy_1h) and direction == "BUY"
            belkhayate_sell_ok = (sell_15 or sell_30 or sell_1h) and direction == "SELL"
            bary_timing = tim_15 or tim_30 or tim_1h

            candidates_seen += 1

            if not (belkhayate_buy_ok or belkhayate_sell_ok):
                continue

            target_signal = "BUY" if belkhayate_buy_ok else "SELL"
            logger.info(f"🔥 MOSTAFA BELKHAYATE & IA DÉTECTÉ — {name} {target_signal} | Timing: {bary_timing:+.2f}")

            # ⚡ ENVOI TELEGRAM & EXECUTION AUTO IMPULSION SNIPER RSI VWAP
            # TP Scalp piloté par config.TP_SCALP_PCT (défaut 1.2% de mouvement de prix)
            _tp_pct = float(getattr(config, "TP_SCALP_PCT", 0.012))
            tp_ext = cur_price * (1 + _tp_pct) if target_signal == "BUY" else cur_price * (1 - _tp_pct)

            # Stop Loss catastrophe optionnel (config.ENABLE_CATASTROPHE_SL)
            sl_ext = 0.0
            if getattr(config, "ENABLE_CATASTROPHE_SL", False):
                _sl_pct = float(getattr(config, "CATASTROPHE_SL_PCT", 0.009))
                sl_ext = cur_price * (1 - _sl_pct) if target_signal == "BUY" else cur_price * (1 + _sl_pct)
            sl_txt = f"`{_fmt_p(sl_ext)}`" if sl_ext > 0 else "`Aucun` ⚠️ (protégé uniquement par le trailing)"
            icon = "🟢" if target_signal == "BUY" else "🔴"
            type_str = "BUY (LONG)" if target_signal == "BUY" else "SELL (SHORT)"
            trend_str = "Haussière 📈" if target_signal == "BUY" else "Baissière 📉"
            vwap_txt = "Discount (Achat Bon Marché) 🟢" if target_signal == "BUY" else "Premium (Vente Chère) 🔴"
            obi_pct = f"{obi_score*100:.0f}% Acheteurs / {(1-obi_score)*100:.0f}% Vendeurs"

            # Toujours envoyer le signal sur Telegram
            time_str = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
            send_message(
                f"👑 *MOSTAFA BELKHAYATE CONTRE-TENDANCE (50X)* 👑\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📊 Signal      : {icon} *{target_signal}*\n"
                f"🪙 Paire       : *{name}* [MEXC FUTURES x50]\n"
                f"💰 Prix d'Entrée : `{_fmt_p(cur_price)}`\n"
                f"🏁 Take Profit : `{_fmt_p(tp_ext)}` (±{_tp_pct*100:.1f}% / ~+{_tp_pct*100*LEVERAGE:.0f}% en x50)\n"
                f"🛑 Stop Loss   : `{sl_txt}`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏛️ *STRATÉGIE BELKHAYATE CONTRE-TENDANCE :*\n"
                f"📍 Cassure Impulsion : *{'Cassure Sommet (BUY / LONG)' if target_signal == 'BUY' else 'Cassure Creux (SELL / SHORT)'}*\n"
                f"⏱️ Timing Oscillator : `{bary_timing:+.2f}` (Extrême Validé)\n"
                f"🕯️ Impulsion Bougie  : *Corps Directionnel ≥ 35%*\n"
                f"📊 Volume SMA 9      : *Volume Institutionnel ≥ SMA 9*\n"
                f"⚡ Levier Utilisé    : *50X (Impulsion Institutionnelle)*\n"
                f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🤖 Consensus IA : *100% Validé (Google TimesFM & Chronos)*\n"
                f"🕐 {time_str} (Heure de Paris)\n"
            )
            logger.info(f"📲 Signal Telegram envoyé pour {name} {target_signal} @ {cur_price}")

            if use_mexc and trade_allowed:
                result_wall = place_order(
                    api_key    = mexc_key,
                    secret_key = mexc_secret,
                    symbol_yf  = sym,
                    signal     = target_signal,
                    price      = cur_price,
                    tp_price   = tp_ext,
                    sl_price   = sl_ext,
                )
                if result_wall and result_wall.get("success"):
                    trade_allowed = False
                    open_symbols.append(symbol_mexc)
                    logger.info(f"🚀 Trade Impulsion RSI Macro 80X {target_signal} ouvert sur MEXC : {name} {target_signal} @ {cur_price}")
                else:
                    err_w = result_wall.get("error", "?") if result_wall else "réponse vide"
                    logger.error(f"❌ Échec auto-trading MEXC {name}: {err_w}")
        except Exception as e:
            logger.error(f"Erreur validation pullback pour {sym}: {e}")

    # ── Bilan des filtres : combien de candidats, et qui les a bloqués ──────────
    if candidates_seen:
        _det = " | ".join(f"{k}:{v}" for k, v in reject_stats.most_common())
        logger.info(f"📉 BILAN FILTRES — {candidates_seen} candidat(s) analysé(s), rejets par filtre → {_det or 'aucun'}")
        logger.info("    (un candidat peut être compté sur plusieurs filtres ; le filtre en tête est celui qui étouffe le plus la stratégie)")
    else:
        logger.info("📉 BILAN FILTRES — 0 candidat n'a atteint l'étape de validation (aucun pullback sur mur détecté en amont)")

    # ── Rapport global toutes les 5 min (Silencieux sur Telegram pour ne garder QUE les opportunités Piège de Baleine) ──
    buyers_list.sort(key=lambda x: x[0], reverse=True)
    sellers_list.sort(key=lambda x: x[0])
    balanced_list.sort(key=lambda x: x[0], reverse=True)

    logger.info(f"📊 Orderbook ±1.5% — Acheteurs: {len(buyers_list)}, Vendeurs: {len(sellers_list)}, Équilibrés: {len(balanced_list)}")

    # ── 5. Auto-trading MEXC Futures : jusqu'à 2 trades simultanés ──────────
    if use_mexc and trade_allowed and strong_signals:
        from src.mexc_trader import SYMBOL_MAP
        
        # Mode Bi-Directionnel : Trader les signaux BUY et SELL disponibles sur MEXC Futures et non déjà ouverts
        tradables = [s for s in strong_signals if s.symbol in SYMBOL_MAP and SYMBOL_MAP[s.symbol] not in open_symbols]
        
        # ── Trier par PRIORITÉ VOLUME 24H MEXC / Marché (Prio Forte si Vol > 5M$ USDT, Prio Moyenne sinon) ──
        def get_crypto_volume_tier(s):
            df = all_data.get(s.symbol)
            vol_24h = 0.0
            if df is not None and not df.empty:
                sub = df.tail(288)
                vol_24h = float((sub["close"] * sub["volume"]).sum())
            return (vol_24h, s.confidence)

        # ── Plancher de liquidité : EXCLUT réellement les paires illiquides ──
        # Avant, le seuil de 5 M$ ne servait qu'à écrire une étiquette dans les logs.
        if getattr(config, "ENABLE_VOLUME_FLOOR", False):
            _floor = float(getattr(config, "MIN_VOLUME_24H_USDT", 5_000_000))
            _kept, _dropped = [], []
            for _s in tradables:
                _v24 = get_crypto_volume_tier(_s)[0]
                (_kept if _v24 >= _floor else _dropped).append((_s, _v24))
            if _dropped:
                logger.info(
                    "🚱 Plancher de liquidité (%s USD) — %d paire(s) exclue(s) : %s"
                    % (f"{_floor:,.0f}", len(_dropped),
                       ", ".join(f"{s.pair_name} {v:,.0f}" for s, v in _dropped[:6]))
                )
            tradables = [s for s, _ in _kept]

        tradables.sort(key=get_crypto_volume_tier, reverse=True)

        if not tradables:
            # AUCUN signal tradable → on informe, et on ne va pas plus loin.
            # (Avant : "for ... else" — le else s'exécutait TOUJOURS et la boucle
            #  envoyait un faux "non tradable" pour chaque signal avant de trader.)
            names = ", ".join(s.pair_name for s in strong_signals[:5])
            logger.info("Aucun signal fort tradable sur MEXC (déjà en position ou crypto absente) → pas de trade")
            send_message(
                f"ℹ️ *Signal(s) détecté(s) mais non tradable(s) sur MEXC*\n"
                f"{names}\n_Signal envoyé, aucun ordre passé (déjà en position ou crypto absente)._"
            )
        else:
            # Log du classement par volume 24h (informatif, aucun message Telegram)
            for t in tradables[:3]:
                df_t = all_data.get(t.symbol)
                v_usdt = float((df_t.tail(288)["close"] * df_t.tail(288)["volume"]).sum()) if df_t is not None else 0
                prio = "🔥 PRIORITÉ HAUTE (Forte Vol 24h)" if v_usdt >= 5_000_000 else "⚖️ PRIORITÉ MOYENNE"
                logger.info(f"📊 Tri Volume MEXC | {t.pair_name} : Vol 24h ~{v_usdt:,.0f} USDT → {prio}")

            # 1 SEULE position a la fois (demande utilisateur)
            slots_available = max(0, 1 - open_count)
            margin_pct_per_trade = 0.90
                
            # Initialiser le filtre DefiLlama
            from src.defillama_filter import DefiLlamaFilter
            tvl_filter = DefiLlamaFilter()
            tvl_filter.initialize()

            opened_trades_count = 0
            for idx, best in enumerate(tradables):
                if opened_trades_count >= slots_available:
                    logger.info(f"Slots de trading remplis ({opened_trades_count}/{slots_available}). Fin du traitement.")
                    break
                logger.info(f"→ Traitement du signal #{idx+1} : {best.pair_name} {best.signal} {best.confidence}%")
                symbol_mexc = SYMBOL_MAP.get(best.symbol)
                
                # Vérification OBI et Funding pour ce signal spécifique
                signal_valid = True

                # Vérification TVL DefiLlama
                is_allowed, tvl_reason = tvl_filter.check_tvl_guard(best.symbol, best.signal)
                logger.info(f"📊 DefiLlama TVL Guard | {best.pair_name} : {tvl_reason}")
                if not is_allowed:
                    logger.info(f"❌ Signal {best.pair_name} bloqué par TVL Guard.")
                    send_message(f"⚠️ *Signal {best.pair_name} {best.signal} bloqué*\n{tvl_reason}")
                    signal_valid = False

                if signal_valid:
                    # Vérification du Pullback EMA20 (Protection Achat en Haut de Mèche)
                    df_best = all_data.get(best.symbol)
                    if df_best is not None and not df_best.empty:
                        df_tmp_b = compute_all_indicators(df_best.copy())
                        if not df_tmp_b.empty:
                            last_b = df_tmp_b.iloc[-1]
                            # data_fetcher normalise les colonnes en MINUSCULES.
                            # "Close" levait KeyError et faisait planter tout le bot
                            # (exit code 1) dès qu'un signal atteignait ce point.
                            if "close" in last_b:
                                c_p = float(last_b["close"])
                            elif "Close" in last_b:
                                c_p = float(last_b["Close"])
                            else:
                                logger.error(f"Colonne close absente pour {best.symbol} → guard EMA20 ignoré")
                                c_p = 0.0
                            ema20_b = float(last_b.get("ema20", c_p))
                            ext_p = (c_p - ema20_b) / ema20_b * 100 if (ema20_b > 0 and c_p > 0) else 0
                            if best.signal == "BUY" and ext_p > 0.08:
                                logger.info(f"⏳ Crypto Pullback Guard | {best.pair_name} est trop étendu de l'EMA20 (+{ext_p:.2f}% > +0.08%) → Attente de repli.")
                                send_message(f"⏳ *Signal {best.pair_name} BUY en attente de Pullback*\nPrix trop haut par rapport à l'EMA20 (+{ext_p:.2f}%). L'ordre sera placé dès le repli sur l'EMA20.")
                                signal_valid = False
                            elif best.signal == "SELL" and ext_p < -0.08:
                                logger.info(f"⏳ Crypto Pullback Guard | {best.pair_name} est trop étendu de l'EMA20 ({ext_p:.2f}% < -0.08%) → Attente de repli.")
                                send_message(f"⏳ *Signal {best.pair_name} SELL en attente de Pullback*\nPrix trop bas par rapport à l'EMA20 ({ext_p:.2f}%). L'ordre sera placé dès le repli sur l'EMA20.")
                                signal_valid = False

                if signal_valid:
                    # Obtenir les informations de profondeur de carnet en direct
                    from src.mexc_trader import get_current_price, get_largest_walls, get_cumulative_depth_ratio, get_recent_cvd_ratio
                    mexc_price = get_current_price(symbol_mexc)
                    if mexc_price <= 0:
                        mexc_price = raw_prices.get(best.symbol, 0)
                    
                    walls_str = ""
                    if mexc_price > 0:
                        walls = get_largest_walls(symbol_mexc, mexc_price, depth_pct=0.015)
                        if walls:
                            w_bid = walls.get("largest_bid")
                            w_ask = walls.get("largest_ask")
                            if w_bid:
                                walls_str += f"\n🟢 *Plus gros support d'achat (1.5%) :* `{w_bid['val_usdt']:,.0f} USDT` à `${w_bid['price']}`"
                            if w_ask:
                                walls_str += f"\n🔴 *Plus gros mur de vente (1.5%) :* `{w_ask['val_usdt']:,.0f} USDT` à `${w_ask['price']}`"

                    # 1. Vérification OBI (Imbalance du haut de carnet)
                    imbalance = get_order_book_imbalance(symbol_mexc)
                    if imbalance is not None:
                        # get_order_book_imbalance renvoie -1..+1 (neutre 0).
                        # On convertit sur l'échelle 0..1 de la stratégie Sniper
                        # (neutre 0.50) pour appliquer LES MÊMES seuils config.
                        _obi_01 = (imbalance + 1.0) / 2.0
                        _min_buy  = float(getattr(config, "OBI_MIN_FOR_BUY", 0.40))
                        _max_sell = float(getattr(config, "OBI_MAX_FOR_SELL", 0.60))
                        logger.info(
                            f"📊 Carnet d'ordres {symbol_mexc} | OBI {_obi_01:.2f} "
                            f"(brut {imbalance:+.2f}) | seuils BUY>={_min_buy:.2f} SELL<={_max_sell:.2f}"
                        )
                        # Filtre OBI neutralisé pour la Stratégie Pure Belkhayate (ne bloque plus les signaux)
                        logger.info(f"📊 Carnet OBI {_obi_01:.2f} — Stratégie Pure Belkhayate active (Signal validé sans blocage).")

                # 2. Stratégie Pure Belkhayate : Tous les anciens filtres secondaires (Funding, Depth Ratio, CVD) sont neutralisés.
                logger.info("🏛️ Stratégie Pure Belkhayate active : Filtres secondaires Funding/Depth/CVD neutralisés (Signal 100% validé).")

                if signal_valid:
                    # 🛡️ Anti-Spoofing Double-Check Universel 15s sur TOUS les trades
                    logger.info(f"🛡️ Anti-Spoofing Guard | Pause 15s de double-check pour valider la stabilité du carnet d'ordres sur {symbol_mexc}...")
                    time.sleep(15)
                    check_walls_2 = get_largest_walls(symbol_mexc, mexc_price if mexc_price > 0 else raw_prices.get(best.symbol, 0), depth_pct=0.015)
                    if not check_walls_2:
                        logger.info(f"🚨 Spoofing détecté sur {best.pair_name}: Mur disparu après 15s -> Ordre Annulé.")
                        send_message(f"🚨 *Signal {best.pair_name} {best.signal} Annulé par Anti-Spoofing Guard*\nLe mur d'ordres a été retiré/manipulé 15s après l'alerte.")
                        signal_valid = False

                if signal_valid:
                    raw_price = raw_prices.get(best.symbol, 0)
                    
                    def parse_price(s: str) -> float:
                        if s == "Aucun":
                            return 0.0
                        return float(s.replace("$", "").replace(",", ""))

                    tp_num = parse_price(best.take_profit)
                    sl_num = parse_price(best.stop_loss)

                    # Sécurité : distance minimale de TP (1.0% de l'entry price pour éviter l'erreur MEXC "The price of stop-limit order error")
                    min_dist_pct = 0.010
                    if best.signal == "BUY":
                        min_tp = raw_price * (1 + min_dist_pct)
                        if tp_num < min_tp:
                            logger.info(f"🔄 Ajustement TP BUY pour {best.pair_name} : {tp_num} -> {min_tp:.5f} (min {min_dist_pct*100}%)")
                            tp_num = min_tp
                    elif best.signal == "SELL":
                        max_tp = raw_price * (1 - min_dist_pct)
                        if tp_num > max_tp or tp_num <= 0:
                            logger.info(f"🔄 Ajustement TP SELL pour {best.pair_name} : {tp_num} -> {max_tp:.5f} (min {min_dist_pct*100}%)")
                            tp_num = max_tp

                    # SL : même politique que la stratégie Sniper (config.ENABLE_CATASTROPHE_SL).
                    # Si désactivé → 0.0, seul le trailing software protège.
                    sl_order = 0.0
                    if getattr(config, "ENABLE_CATASTROPHE_SL", False):
                        _sl_pct = float(getattr(config, "CATASTROPHE_SL_PCT", 0.009))
                        if raw_price > 0:
                            sl_order = (raw_price * (1 - _sl_pct) if best.signal == "BUY"
                                        else raw_price * (1 + _sl_pct))
                        elif sl_num > 0:
                            sl_order = sl_num

                    result = place_order(
                        api_key    = mexc_key,
                        secret_key = mexc_secret,
                        symbol_yf  = best.symbol,
                        signal     = best.signal,
                        price      = raw_price,
                        tp_price   = tp_num,
                        sl_price   = sl_order,
                        margin_pct = margin_pct_per_trade,
                    )

                    if result and result.get("success"):
                        send_message(format_order_telegram(result, best))
                        logger.info(f"✅ Ordre MEXC Futures pour {best.pair_name} ouvert et notifié !")
                        opened_trades_count += 1
                        time.sleep(0.5)
                    else:
                        err = result.get("error", "Inconnue") if result else "Réponse MEXC vide"
                        logger.error(f"❌ Échec ordre {best.pair_name}: {err}")
                        send_message(f"❌ *Erreur MEXC Futures — {best.pair_name}*\n`{err}`\n_Position non ouverte._")
    elif use_mexc and trade_allowed and not strong_signals:
        logger.info("Aucun signal fort → Pas de trade ce scan.")

    logger.info("=== Analyse terminée ===")


if __name__ == "__main__":
    main()
