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
from datetime import datetime, timezone

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
    get_order_book_imbalance
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
        logger.info(f"Solde MEXC Futures : {balance:.2f} USDT")

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
    waiting = [pr for pr in pending if now_ts - pr["ts"] < 3600]
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
        block = False
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

    # ── 4. Envoi Telegram des signaux forts (uniquement les NOUVEAUX) ──────────
    if new_signals_to_send:
        logger.info(f"Envoi de {len(new_signals_to_send)} nouveaux signaux sur Telegram...")
        for s in new_signals_to_send:
            send_signal(s)
            time.sleep(0.5)
    elif strong_signals:
        logger.info(f"Signaux actifs ({len(strong_signals)}) déjà envoyés — pas de notification.")
    else:
        logger.info("Aucun signal fort ce scan.")

    # ── Rapport Orderbook permanent : envoyé à CHAQUE scan (toutes les 5 min) ──
    from src.mexc_trader import SYMBOL_MAP, get_current_price, get_cumulative_depth_ratio, get_largest_walls
    import re as _re
    import yfinance as _yf_ob
    import pandas as _pd_ob

    APPROACH_THRESHOLD = 0.02   # 2% → prix à 2% du mur = potentiellement en approche
    PULLBACK_THRESHOLD = 0.005  # 0.5% → prix à 0.5% du mur = en contact direct

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
            if price > 0:
                ratio = get_cumulative_depth_ratio(symbol_mexc, price, depth_pct=1.5)
                walls = get_largest_walls(symbol_mexc, price, depth_pct=0.015)
                if ratio is not None:
                    name = _clean_name(sym)
                    MIN_DIST = 0.001   # distance mini au mur (0.1%) — au contact du mur
                    if ratio >= 1.2:
                        buyers_list.append((ratio, name))
                        # ⚡ PULLBACK REBOND SUR MUR DES BALEINES (ACHAT AU MUR DE SUPPORT 0.3268)
                        if walls and walls.get("largest_bid"):
                            wall_price = float(walls["largest_bid"]["price"])
                            dist = (price - wall_price) / price  # positif = mur de support en dessous
                            if 0.0005 <= dist <= 0.004:  # Le prix est en plein pullback au contact du mur des baleines (<= 0.4%)
                                try:
                                    tp_est = price * 1.015   # TP étendu +1.5% après le rebond sur le mur des baleines
                                    pullback_signals.append(("BUY", name, sym, symbol_mexc, ratio, price, wall_price, tp_est, dist * 100, 0.0))
                                except Exception:
                                    pass
                        elif walls and walls.get("largest_ask"):
                            wall_price = float(walls["largest_ask"]["price"])
                            dist = (wall_price - price) / price
                            if 0.0005 <= dist <= 0.008:
                                tp_est = wall_price * 0.999
                                pullback_signals.append(("BUY", name, sym, symbol_mexc, ratio, price, wall_price, tp_est, dist * 100, 0.0))
                    elif ratio <= 0.8:
                        sellers_list.append((ratio, name))
                        # ⚡ PULLBACK REBOND SUR MUR VENTE DES BALEINES (VENTE AU MUR DE RESISTANCE)
                        if walls and walls.get("largest_ask"):
                            wall_price = float(walls["largest_ask"]["price"])
                            dist = (wall_price - price) / price  # positif = mur de résistance au-dessus
                            if 0.0005 <= dist <= 0.004:  # Le prix est en plein pullback au contact du mur des baleines (<= 0.4%)
                                try:
                                    tp_est = price * 0.985   # TP étendu -1.5% après le rejet du mur des baleines
                                    pullback_signals.append(("SELL", name, sym, symbol_mexc, ratio, price, wall_price, tp_est, dist * 100, 0.0))
                                except Exception:
                                    pass
                    else:
                        balanced_list.append((ratio, name))
            time.sleep(0.15)
        except Exception as e:
            logger.error(f"Erreur orderbook ratio {sym}: {e}")

    # ── Validation Fisher & Envoi des signaux pullback (direction confirmée) ──────────
    import yfinance as yf

    for direction, name, sym, symbol_mexc, ratio, cur_price, entry_price, tp_price, dist_pct, trend_45m in pullback_signals:
        try:
            # 🛡️ 1. Vérification Anti-Spoofing & Piège des Baleines (Trade Contre-Tendance)
            new_walls = get_largest_walls(symbol_mexc, cur_price, depth_pct=0.015)
            spoofing_detected = False
            trap_direction = None

            if not new_walls or not new_walls.get("largest_bid") or not new_walls.get("largest_ask"):
                spoofing_detected = True
                trap_direction = "SELL" if direction == "BUY" else "BUY"
            elif direction == "BUY":
                current_ask_price = float(new_walls["largest_ask"]["price"])
                if abs(current_ask_price - entry_price) / entry_price > 0.001:
                    spoofing_detected = True
                    trap_direction = "BUY"  # Le faux mur de vente a disparu -> Explosion haussière (PUMP)!
            else:
                current_bid_price = float(new_walls["largest_bid"]["price"])
                if abs(current_bid_price - entry_price) / entry_price > 0.001:
                    spoofing_detected = True
                    trap_direction = "SELL" # Le faux mur d'achat a disparu -> Cassure baissière (DUMP)!

            if spoofing_detected:
                logger.info(f"🚨 PIÈGE DE BALEINE DÉTECTÉ sur {name} ! Activation du Trade Contre-Tendance {trap_direction}...")
                if use_mexc and trade_allowed:
                    tp_trap = cur_price * (1.02 if trap_direction == "BUY" else 0.98)
                    result_trap = place_order(
                        api_key    = mexc_key,
                        secret_key = mexc_secret,
                        symbol_yf  = sym,
                        signal     = trap_direction,
                        price      = cur_price,
                        tp_price   = tp_trap,
                        sl_price   = 0.0,
                    )
                    if result_trap and result_trap.get("success"):
                        trade_allowed = False
                        open_symbols.append(symbol_mexc)
                        emoji_trap = "🟢" if trap_direction == "BUY" else "🔴"
                        send_message(
                            f"{emoji_trap} *PIÈGE DE BALEINE EXPLOITÉ — {name}* {emoji_trap}\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"🚨 *Faux mur retiré par les baleines !*\n"
                            f"📌 *ENTRÉE EN CONTRE-TENDANCE : {trap_direction} x{LEVERAGE}*\n"
                            f"💰 Prix Entrée : `{_fmt_p(cur_price)}`\n"
                            f"🏁 TP Cible : `{_fmt_p(tp_trap)}` (+2.0% de capture)\n"
                            f"🔒 Trailing Stop Actif (+1.5% Breakeven)\n"
                        )
                continue

            anti_scam_txt = "🛡️ Validé Anti-Spoofing (Double check OK)"

            # 📊 2. Vérification Graphique (Range / Fisher en 15 minutes)
            df = yf.download(sym, period="5d", interval="15m", progress=False)
            if not df.empty:
                import pandas as pd
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                df = df.rename(columns={
                    "Open": "open", "High": "high",
                    "Low": "low",  "Close": "close", "Volume": "volume"
                })
                df = compute_all_indicators(df)
                if not df.empty:
                    last = df.iloc[-1]
                    adx = float(last["adx"])
                    fisher = float(last["fisher"])
                else:
                    logger.error(f"Echec du calcul des indicateurs pour {sym} (df vide)")
                    continue
                
                # Check Range
                if adx < 25:
                    range_txt = f"✅ Range confirmé (ADX: {adx:.1f})"
                else:
                    range_txt = f"⚠️ Hors Range (ADX: {adx:.1f})"
                    
                # Check Fisher Pullback Réel (Obligatoire)
                if (direction == "BUY" and fisher <= -0.5):
                    fisher_txt = f"✅ Fisher(9) en creux (Pullback validé): {fisher:.2f}"
                elif (direction == "SELL" and fisher >= 0.5):
                    fisher_txt = f"✅ Fisher(9) en sommet (Pullback validé): {fisher:.2f}"
                else:
                    logger.info(f"⏳ Pullback Guard | {name} {direction} Fisher non optimal ({fisher:.2f}) → Achat en haut de mèche bloqué. Attente vrai mouvement de pullback.")
                    continue
                    
                wall_type = "🟢 *REBOND SUR SUPPORT BALEINE (BUY)*" if direction == "BUY" else "🔴 *REJET SUR RÉSISTANCE BALEINE (SELL)*"
                emoji = "🟢" if direction == "BUY" else "🔴"
                send_message(
                    f"{emoji} {wall_type} {emoji}\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"📌 Pair : *{name}* | Ordre : *{direction}*\n"
                    f"💰 Prix actuel : `{_fmt_p(cur_price)}`\n"
                    f"🧱 Mur Baleine : `{_fmt_p(entry_price)}` — Distance : `{dist_pct:.2f}%`\n"
                    f"🏁 Take Profit : `{_fmt_p(tp_price)}`\n"
                    f"📊 Ratio Orderbook : `{ratio:.2f}`\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"{anti_scam_txt}\n"
                    f"{range_txt}\n"
                    f"{fisher_txt}"
                )
                logger.info(f"⚡ Signal Approche Mur envoyé : {name} {direction} @ {cur_price}")

                # ⚡ EXECUTION AUTO : 1 seul trade, TP étendu + TRAILING
                if use_mexc and trade_allowed:
                    if direction == "SELL":
                        tp_ext = entry_price - (cur_price - entry_price)
                    else:
                        tp_ext = entry_price + (entry_price - cur_price)
                    sl_wall = 0.0
                    result_wall = place_order(
                        api_key    = mexc_key,
                        secret_key = mexc_secret,
                        symbol_yf  = sym,
                        signal     = direction,
                        price      = cur_price,
                        tp_price   = tp_ext,
                        sl_price   = sl_wall,
                    )
                    if result_wall and result_wall.get("success"):
                        trade_allowed = False
                        open_symbols.append(symbol_mexc)
                        send_message(
                            f"🚀 *TRADE BALEINE OUVERT — {name}*\n"
                            f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                            f"📌 *{direction} ({wall_type})* x{result_wall.get('leverage')} — Mise {result_wall.get('balance_used')} USDT\n"
                            f"💰 Prix Entrée : `{_fmt_p(cur_price)}`\n"
                            f"🧱 Mur Baleine : `{_fmt_p(entry_price)}` (Distance: {dist_pct:.2f}%)\n"
                            f"🏁 TP Cible : `{_fmt_p(tp_ext)}`\n"
                            f"🔒 + Trailing Stop Actif (+1.5% Breakeven)\n"
                        )
                        logger.info(f"🚀 Trade Aspiration/Baleine ouvert : {name} {direction} TP {tp_price}")
                    else:
                        err_w = result_wall.get("error", "?") if result_wall else "réponse vide"
                        logger.error(f"❌ Échec trade aspiration {name}: {err_w}")
        except Exception as e:
            logger.error(f"Erreur validation pullback pour {sym}: {e}")

    # ── Rapport global toutes les 5 min ───────────────────────────────────────
    buyers_list.sort(key=lambda x: x[0], reverse=True)
    sellers_list.sort(key=lambda x: x[0])
    balanced_list.sort(key=lambda x: x[0], reverse=True)

    def _fmt(lst):
        return " | ".join(f"*{n}* `{r}`" for r, n in lst) if lst else "—"

    send_message(
        f"📊 *Orderbook ±1.5% — {len(buyers_list)+len(sellers_list)+len(balanced_list)} cryptos*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *ACHETEURS ({len(buyers_list)}) — décroissant :*\n{_fmt(buyers_list)}\n\n"
        f"🔴 *VENDEURS ({len(sellers_list)}) — croissant :*\n{_fmt(sellers_list)}\n\n"
        f"⚖️ *EQUILIBRÉ ({len(balanced_list)}) :*\n{_fmt(balanced_list)}\n"
        f"_Prochain scan dans 5 min_"
    )

    # ── 5. Auto-trading MEXC Futures : jusqu'à 2 trades simultanés ──────────
    if use_mexc and trade_allowed and strong_signals:
        from src.mexc_trader import SYMBOL_MAP
        
        # Ne trader QUE les cryptos disponibles sur MEXC Futures et non déjà ouvertes
        tradables = [s for s in strong_signals if s.symbol in SYMBOL_MAP and SYMBOL_MAP[s.symbol] not in open_symbols]
        
        # ── Trier par PRIORITÉ VOLUME 24H MEXC / Marché (Prio Forte si Vol > 5M$ USDT, Prio Moyenne sinon) ──
        def get_crypto_volume_tier(s):
            df = all_data.get(s.symbol)
            vol_24h = 0.0
            if df is not None and not df.empty:
                sub = df.tail(288)
                vol_24h = float((sub["close"] * sub["volume"]).sum())
            return (vol_24h, s.confidence)

        tradables.sort(key=get_crypto_volume_tier, reverse=True)
        for t in tradables[:3]:
            df_t = all_data.get(t.symbol)
            v_usdt = float((df_t.tail(288)["close"] * df_t.tail(288)["volume"]).sum()) if df_t is not None else 0
            prio = "🔥 PRIORITÉ HAUTE (Forte Vol 24h)" if v_usdt >= 5_000_000 else "⚖️ PRIORITÉ MOYENNE"
            logger.info(f"📊 Tri Volume MEXC | {t.pair_name} : Vol 24h ~{v_usdt:,.0f} USDT → {prio}")
            names = ", ".join(s.pair_name for s in strong_signals[:5])
            logger.info(f"Aucun signal fort n'est tradable ou disponible sur MEXC (non déjà en position / non bloqué par BTC Guard) → pas de trade")
            send_message(
                f"ℹ️ *Signal(s) détecté(s) mais non tradable(s) sur MEXC*\n"
                f"{names}\n_Signal envoyé, aucun ordre passé (déjà en position ou crypto absente)._"
            )
        else:
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
                        df_tmp_b = compute_all_indicators(df_best)
                        if not df_tmp_b.empty:
                            last_b = df_tmp_b.iloc[-1]
                            c_p = float(last_b["Close"])
                            ema20_b = float(last_b.get("ema20", c_p))
                            ext_p = (c_p - ema20_b) / ema20_b * 100 if ema20_b > 0 else 0
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
                        logger.info(f"📊 Analyse Carnet d'ordres {symbol_mexc} | Imbalance (OBI): {imbalance:+.2f}")
                        if best.signal == "BUY" and imbalance < -0.2:
                            logger.info(f"❌ OBI trop négatif ({imbalance:+.2f} < -0.2) -> Blocage achat.")
                            send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nCarnet d'ordres défavorable (Imbalance OBI: {imbalance:+.2f})")
                            signal_valid = False
                        elif best.signal == "SELL" and imbalance > 0.2:
                            logger.info(f"❌ OBI trop positif ({imbalance:+.2f} > 0.2) -> Blocage vente.")
                            send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nCarnet d'ordres défavorable (Imbalance OBI: {imbalance:+.2f})")
                            signal_valid = False

                # 2. Vérification Funding Rate
                if signal_valid:
                    from src.mexc_trader import get_funding_rate
                    funding = get_funding_rate(symbol_mexc)
                    if funding is not None:
                        logger.info(f"💰 Funding rate {symbol_mexc}: {funding:+.4f}%")
                        if best.signal == "BUY" and funding > 0.10:
                            logger.info(f"❌ Funding trop positif ({funding:+.4f}%) -> Achat bloqué.")
                            send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nFunding rate surchauffé ({funding:+.4f}%) : longs surchargés.")
                            signal_valid = False
                        elif best.signal == "SELL" and funding < -0.10:
                            logger.info(f"❌ Funding trop négatif ({funding:+.4f}%) -> Vente bloquée.")
                            send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nFunding rate surchauffé ({funding:+.4f}%) : shorts surchargés.")
                            signal_valid = False

                # 3. Vérification Cumulative Depth et CVD
                if signal_valid and mexc_price > 0:
                    # Profondeur Cumulative (1.5%)
                    depth_ratio = get_cumulative_depth_ratio(symbol_mexc, mexc_price, depth_pct=0.015)
                    if depth_ratio is not None:
                        logger.info(f"🧱 Profondeur cumulative {symbol_mexc} | Ratio Bids/Asks (1.5%): {depth_ratio}")
                        if best.signal == "BUY" and depth_ratio < 1.2:
                            logger.info(f"❌ Profondeur cumulative défavorable ({depth_ratio} < 1.2) -> Blocage achat (murs de vente trop forts).")
                            send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nMurs de vente trop forts à proximité (Ratio Acheteurs/Vendeurs à 1.5% : {depth_ratio})")
                            signal_valid = False
                        elif best.signal == "SELL" and depth_ratio > 0.8:
                            logger.info(f"❌ Profondeur cumulative défavorable ({depth_ratio} > 0.8) -> Blocage vente (murs d'achat trop forts).")
                            send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nMurs d'achat trop forts à proximité (Ratio Acheteurs/Vendeurs à 1.5% : {depth_ratio})")
                            signal_valid = False
                    
                    # CVD (Transactions récentes)
                    if signal_valid:
                        cvd_ratio = get_recent_cvd_ratio(symbol_mexc)
                        if cvd_ratio is not None:
                            logger.info(f"📊 CVD Transactions {symbol_mexc} | Ratio Volume Achat/Vente (100 trades): {cvd_ratio}")
                            if best.signal == "BUY" and cvd_ratio < 1.15:
                                logger.info(f"❌ CVD défavorable ({cvd_ratio} < 1.15) -> Blocage achat (flux vendeur domine).")
                                send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nFlux d'achat agressif insuffisant (Ratio Achat/Vente: {cvd_ratio})")
                                signal_valid = False
                            elif best.signal == "SELL" and cvd_ratio > 0.85:
                                logger.info(f"❌ CVD défavorable ({cvd_ratio} > 0.85) -> Blocage vente (flux acheteur domine).")
                                send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nFlux de vente agressif insuffisant (Ratio Achat/Vente: {cvd_ratio})")
                                signal_valid = False

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

                    # Passer l'ordre avec pas de SL fixe (sl_price = 0.0) — seul le Trailing Stop protège
                    result = place_order(
                        api_key    = mexc_key,
                        secret_key = mexc_secret,
                        symbol_yf  = best.symbol,
                        signal     = best.signal,
                        price      = raw_price,
                        tp_price   = tp_num,
                        sl_price   = 0.0,
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
