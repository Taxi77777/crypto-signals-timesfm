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

    walls_part = ""
    if getattr(config, "ENABLE_WALLS_IN_SIGNAL", True) and signal.orderbook_walls != "N/A":
        walls_part = f"🧱 *Murs dans ±1.5% du prix :*\n{signal.orderbook_walls}\n"

    return (
        f"👑 *MOSTAFA BELKHAYATE & IA SYSTEM* 👑\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Signal      : {emoji} *{signal.signal}*\n"
        f"🪙 Paire       : *{signal.pair_name}* [MEXC FUTURES x80]\n"
        f"💰 Prix d'Entrée : `{signal.current_price}`\n"
        f"🎯 Take Profit : `{signal.take_profit}` ({tp_sign}{signal.tp_pct}%)\n"
        f"🛑 Stop Loss   : `{signal.stop_loss}` ({sl_sign}{signal.sl_pct}%)\n"
        f"🔮 Confiance IA : `{conf_bar}` {signal.confidence}%\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🏛️ *STRATÉGIE MOSTAFA BELKHAYATE :*\n"
        f"📍 Zone Barycentre  : *{'Zone Verte (Achat Bon Marché)' if signal.signal == 'BUY' else 'Zone Rouge (Vente Chère)'}*\n"
        f"⏱️ Timing Oscillator : `{signal.fisher:+.2f}` (Zone Extrême Validée)\n"
        f"🕯️ Mèche de Rejet    : *Physique ≥ 15% Confirmée*\n"
        f"📈 Croisement MA     : *MA 30/60 Valide (0-1 bougie)*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🤖 Consensus IA : *100% Validé (Google TimesFM & Chronos)*\n"
        f"🕐 {time_str} (Heure de Paris)\n"
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
        logger.info("Message Telegram envoye avec succes")
        return True
    except Exception as e:
        logger.error(f"Erreur Telegram: {e}")
        return False


def send_message(text: str, chat_id: str = None) -> bool:
    try:
        return asyncio.run(_send_async(text, chat_id))
    except Exception:
        return False


def send_signal(signal: TradingSignal) -> bool:
    return send_message(format_signal_message(signal))
