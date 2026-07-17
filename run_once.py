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
from src.signal_generator  import generate_signal
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
    if use_mexc:
        balance = get_usdt_balance(mexc_key, mexc_secret)
        logger.info(f"Solde MEXC Futures : {balance:.2f} USDT")

        if has_open_position(mexc_key, mexc_secret):
            # Position ouverte → appliquer trailing stop software
            logger.info("Position active → Vérification trailing stop...")
            trail_result = check_and_trail(mexc_key, mexc_secret)
            if trail_result:
                msg = format_trail_telegram(trail_result)
                send_message(msg)
                logger.info(f"Trailing stop appliqué : {trail_result}")
            trade_allowed = False  # Pas de nouveau trade
        else:
            logger.info("Aucune position → Recherche du meilleur signal...")
            trade_allowed = True
    else:
        logger.warning("Clés MEXC absentes — Mode analyse seule.")

    # ── 2. Analyse TimesFM des 25 cryptos ────────────────────────────────────
    logger.info(f"Analyse de {len(config.CRYPTO_PAIRS)} cryptos avec TimesFM 2.5...")
    all_data = fetch_all_pairs()
    logger.info("Téléchargement des données 4h pour le filtre de tendance...")
    all_data_4h = fetch_all_pairs(period="60d", interval="4h")

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

    # ── Enregistrer les prédictions du scan pour vérification future ─────────
    from src.signal_generator import _ai_direction
    for sym, series in series_map.items():
        cur_price = float(series[-1])
        for key, mp in (("TFM", "tfm"), ("CHO", "cho"), ("MOI", "moi"), ("LLA", "lla"), ("GRA", "gra")):
            d = _ai_direction(cur_price, ai_preds[mp].get(sym))
            if d in ("BUY", "SELL"):
                waiting.append({"ts": now_ts, "model": key, "symbol": sym, "dir": d, "price": cur_price})
    save_pending(waiting)
    save_track(track)   # garantit l'existence du fichier pour le commit

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
                df_4h=all_data_4h.get(symbol) if all_data_4h else None,
            )
            if signal:
                signals.append(signal)
        except Exception as e:
            logger.error(f"Erreur {pair_name}: {e}")
            continue

    # Trier par confiance décroissante
    strong_signals = [s for s in signals if s.is_strong and s.signal != "HOLD"]
    strong_signals.sort(key=lambda s: s.confidence, reverse=True)

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
            }
            for s in strong_signals
        ]
    }
    with open("signals.json", "w", encoding="utf-8") as f:
        json.dump(web_data, f, indent=2, ensure_ascii=False)
    logger.info("signals.json mis à jour.")

    # ── 4. Envoi Telegram des signaux forts ───────────────────────────────────
    if strong_signals:
        logger.info(f"Envoi de {len(strong_signals)} signaux forts sur Telegram...")
        for s in strong_signals:
            send_signal(s)
            time.sleep(0.5)
    else:
        logger.info("Aucun signal fort ce scan.")
        # Heartbeat : confirme que le bot tourne même sans signal
        send_message(
            f"🔍 *Scan Crypto terminé*\n"
            f"📊 {len(all_data)} cryptos scannées | {len(ind_map)} analysées\n"
            f"🤖 {len(signals)} pré-signaux, 0 signal fort\n"
            f"⚖️ Consensus majoritaire (>=3/5) IA + pondération dynamique\n"
            f"_Prochain scan dans 5 min_"
        )

    # ── 5. Auto-trading MEXC Futures : 1 seul trade, meilleur signal ──────────
    if use_mexc and trade_allowed and strong_signals:
        from src.mexc_trader import SYMBOL_MAP
        # Ne trader QUE les cryptos réellement disponibles sur MEXC Futures.
        # Les autres restent des signaux Telegram (pas d'ordre, pas d'erreur).
        tradables = [s for s in strong_signals if s.symbol in SYMBOL_MAP]
        if not tradables:
            names = ", ".join(s.pair_name for s in strong_signals[:5])
            logger.info(f"Aucun signal fort n'est tradable sur MEXC (non mappés: {names}) → pas de trade")
            send_message(
                f"ℹ️ *Signal(s) détecté(s) mais non tradable(s) sur MEXC*\n"
                f"{names}\n_Signal envoyé, aucun ordre passé (crypto absente de MEXC Futures)._"
            )
            best = None
        else:
            best = tradables[0]
            logger.info(f"→ Meilleur signal tradable : {best.pair_name} {best.signal} {best.confidence}%")

        symbol_mexc = SYMBOL_MAP.get(best.symbol) if best else None
        if best and symbol_mexc:
            imbalance = get_order_book_imbalance(symbol_mexc)
            if imbalance is not None:
                logger.info(f"📊 Analyse Carnet d'ordres {symbol_mexc} | Imbalance (OBI): {imbalance:+.2f}")
                if best.signal == "BUY" and imbalance < -0.2:
                    logger.info(f"❌ OBI trop négatif ({imbalance:+.2f} < -0.2) -> Blocage achat contre mur de vente.")
                    send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nCarnet d'ordres défavorable (Imbalance: {imbalance:+.2f})")
                    trade_allowed = False
                elif best.signal == "SELL" and imbalance > 0.2:
                    logger.info(f"❌ OBI trop positif ({imbalance:+.2f} > 0.2) -> Blocage vente contre mur d'achat.")
                    send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nCarnet d'ordres défavorable (Imbalance: {imbalance:+.2f})")
                    trade_allowed = False
            else:
                logger.warning("Impossible de récupérer l'OBI (Ignoré, trading autorisé)")

            # ── Filtre Funding Rate (sentiment de levier) ──
            from src.mexc_trader import get_funding_rate
            funding = get_funding_rate(symbol_mexc)
            if funding is not None:
                logger.info(f"💰 Funding rate {symbol_mexc}: {funding:+.4f}%")
                if best.signal == "BUY" and funding > 0.10:
                    logger.info(f"❌ Funding trop positif ({funding:+.4f}% > +0.10%) -> Longs surchargés, achat bloqué.")
                    send_message(f"⚠️ *Signal {best.pair_name} BUY bloqué*\nFunding rate surchauffé ({funding:+.4f}%) : trop de longs à levier.")
                    trade_allowed = False
                elif best.signal == "SELL" and funding < -0.10:
                    logger.info(f"❌ Funding trop négatif ({funding:+.4f}% < -0.10%) -> Shorts surchargés, vente bloquée.")
                    send_message(f"⚠️ *Signal {best.pair_name} SELL bloqué*\nFunding rate surchauffé ({funding:+.4f}%) : trop de shorts à levier.")
                    trade_allowed = False
            else:
                logger.warning("Funding rate indisponible (Ignoré, trading autorisé)")

        if best:
            raw_price = raw_prices.get(best.symbol, 0)

            def parse_price(s: str) -> float:
                if s == "Aucun":
                    return 0.0
                return float(s.replace("$", "").replace(",", ""))

            tp_num = parse_price(best.take_profit)
            sl_num = parse_price(best.stop_loss)

            result = place_order(
                api_key    = mexc_key,
                secret_key = mexc_secret,
                symbol_yf  = best.symbol,
                signal     = best.signal,
                price      = raw_price,
                tp_price   = tp_num,
                sl_price   = sl_num,
            ) if trade_allowed else None

            if result and result.get("success"):
                send_message(format_order_telegram(result, best))
                logger.info("✅ Ordre MEXC Futures ouvert et notifié sur Telegram !")
            elif trade_allowed:
                err = result.get("error", "Inconnue") if result else "Réponse MEXC vide (vérifie clés API / solde ≥ 1 USDT / KYC)"
                logger.error(f"❌ Échec ordre : {err}")
                send_message(f"❌ *Erreur MEXC Futures — {best.pair_name}*\n`{err}`\n_Position non ouverte._")
    elif use_mexc and trade_allowed and not strong_signals:
        logger.info("Aucun signal fort → Pas de trade ce scan.")

    logger.info("=== Analyse terminée ===")


if __name__ == "__main__":
    main()
