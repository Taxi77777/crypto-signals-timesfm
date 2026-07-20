"""
src/telegram_bot.py — Envoi des signaux crypto sur Telegram
"""

import logging
import asyncio
from datetime import datetime
import pytz
from telegram import Bot
from telegram.constants import ParseMode
import config
from src.signal_generator import TradingSignal

logger   = logging.getLogger(__name__)
PARIS_TZ = pytz.timezone("Europe/Paris")


def _confidence_bar(confidence: int) -> str:
    filled = int(confidence / 10)
    return "█" * filled + "░" * (10 - filled)


def format_signal_message(signal: TradingSignal) -> str:
    emoji     = "🟢" if signal.signal == "BUY" else "🔴"
    conf_bar  = _confidence_bar(signal.confidence)
    time_str  = datetime.now(PARIS_TZ).strftime("%d/%m/%Y %H:%M")
    tp_sign   = "+" if signal.signal == "BUY" else "-"
    sl_sign   = "-" if signal.signal == "BUY" else "+"

    return (
        f"🤖 *CRYPTO SIGNAL — {signal.pair_name}* 🚨 *SIGNAL FORT*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Signal      : {emoji} *{signal.signal}*\n"
        f"💰 Prix actuel : `{signal.current_price}`\n"
        f"🎯 Take Profit : `{signal.take_profit}` ({tp_sign}{signal.tp_pct}%)\n"
        f"🛑 Stop Loss   : `{signal.stop_loss}` ({sl_sign}{signal.sl_pct}%)\n"
        f"🔮 Confiance   : `{conf_bar}` {signal.confidence}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"RSI        : `{signal.rsi}` {signal.rsi_status}\n"
        f"Fisher     : `{signal.fisher:+.2f}` {signal.fisher_status}\n"
        f"MACD       : {signal.macd_trend}\n"
        f"EMA 20/50  : {signal.ema_trend}\n"
        f"Bollinger  : {signal.bb_position}\n"
        f"🎯 Zone SMC : *{signal.smc_zone}*\n"
        f"⚡ Confluence OTE : *{'Oui (61.8%-79%)' if signal.is_ote else 'Non'}*\n"
        f"🧱 Carnet d'ordres (1.5%) :\n{signal.orderbook_walls}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {time_str} (Paris)\n"
        f"⚠️ _Usage éducatif uniquement_"
    )


async def _send_async(text: str, chat_id: str = None) -> bool:
    try:
        bot = Bot(token=config.TELEGRAM_BOT_TOKEN)
        target = chat_id if chat_id else config.TELEGRAM_CHAT_ID
        await bot.send_message(
            chat_id=target,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info("✅ Message Telegram envoyé")
        return True
    except Exception as e:
        logger.error(f"❌ Erreur Telegram: {e}")
        return False


def send_message(text: str, chat_id: str = None) -> bool:
    try:
        return asyncio.run(_send_async(text, chat_id))
    except Exception:
        return False


def send_signal(signal: TradingSignal) -> bool:
    return send_message(format_signal_message(signal))
