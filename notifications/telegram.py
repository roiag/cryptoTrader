"""
Telegram Notifier - שולח התראות לערוץ Telegram.

איך להקים:
  1. צור בוט דרך @BotFather ב-Telegram → קבל BOT_TOKEN
  2. שלח הודעה לבוט שלך
  3. גש ל: https://api.telegram.org/bot<TOKEN>/getUpdates → קבל CHAT_ID
  4. הזן שניהם ב-.env

כל הפונקציות silent - לא זורקות exception אם Telegram לא זמין.
"""

import requests
from datetime import datetime
from loguru import logger

from config.settings import settings

_BASE_URL = "https://api.telegram.org/bot{token}/sendMessage"


def _chat_ids() -> list[str]:
    """
    מחזיר רשימת chat IDs מתוך TELEGRAM_CHAT_ID.
    תומך בערך יחיד או ברשימה מופרדת בפסיקים:
      TELEGRAM_CHAT_ID=579233544
      TELEGRAM_CHAT_ID=579233544,987654321
    """
    raw = settings.TELEGRAM_CHAT_ID
    if not raw:
        return []
    return [cid.strip() for cid in str(raw).split(",") if cid.strip()]


def _send(text: str, parse_mode: str = "HTML") -> bool:
    """שולח הודעה לכל ה-chat IDs המוגדרים."""
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.debug("Telegram not configured, skipping notification")
        return False

    ids = _chat_ids()
    if not ids:
        logger.debug("No Telegram chat IDs configured")
        return False

    url = _BASE_URL.format(token=settings.TELEGRAM_BOT_TOKEN)
    success = True
    for chat_id in ids:
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
                timeout=5,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(f"Telegram send failed (chat_id={chat_id}): {e}")
            success = False
    return success


def send_text(text: str) -> bool:
    """Send a raw HTML-formatted message — used by ReviewAgent."""
    return _send(text)


# ── Notification templates ────────────────────────────────────────────────────

def notify_trade_opened(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    notional: float,
    risk_amount: float,
    final_score: float,
) -> None:
    emoji = "🟢" if side == "buy" else "🔴"
    action = "BUY ▲" if side == "buy" else "SELL ▼"
    rr = round((tp_price - entry_price) / (entry_price - sl_price), 2) if side == "buy" \
         else round((entry_price - tp_price) / (sl_price - entry_price), 2)

    _send(
        f"{emoji} <b>TRADE OPENED</b>\n\n"
        f"<b>{action} {symbol}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Entry   : <code>{entry_price:,.2f}</code>\n"
        f"SL      : <code>{sl_price:,.2f}</code>\n"
        f"TP      : <code>{tp_price:,.2f}</code>\n"
        f"R:R     : <code>1:{rr}</code>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Qty     : <code>{quantity}</code>\n"
        f"Notional: <code>${notional:,.2f}</code>\n"
        f"Risk    : <code>${risk_amount:.2f}</code>\n"
        f"Score   : <code>{final_score:+.2f}</code>\n"
        f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
    )


def notify_trade_closed(
    symbol: str,
    side: str,
    close_reason: str,   # "SL" / "TP" / "manual"
    entry_price: float,
    close_price: float,
    pnl: float,
    pnl_pct: float,
) -> None:
    won  = pnl >= 0
    emoji = "✅" if won else "❌"
    _send(
        f"{emoji} <b>TRADE CLOSED — {close_reason}</b>\n\n"
        f"<b>{symbol}</b> ({side.upper()})\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Entry  : <code>{entry_price:,.2f}</code>\n"
        f"Close  : <code>{close_price:,.2f}</code>\n"
        f"PnL    : <code>{'+'if won else ''}{pnl:.2f} USDT ({pnl_pct:+.2%})</code>\n"
        f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
    )


def notify_circuit_breaker(daily_loss_pct: float) -> None:
    _send(
        f"🚨 <b>CIRCUIT BREAKER TRIGGERED</b>\n\n"
        f"Daily loss: <code>{daily_loss_pct:.2%}</code>\n"
        f"Trading halted for the rest of the day.\n"
        f"<i>{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}</i>"
    )


def notify_daily_summary(
    date: str,
    total_trades: int,
    wins: int,
    losses: int,
    total_pnl: float,
    win_rate: float,
    max_drawdown: float,
) -> None:
    emoji = "📈" if total_pnl >= 0 else "📉"
    _send(
        f"{emoji} <b>DAILY SUMMARY — {date}</b>\n\n"
        f"Trades   : <code>{total_trades}</code> "
        f"(W:{wins} / L:{losses})\n"
        f"Win Rate : <code>{win_rate:.0%}</code>\n"
        f"Total PnL: <code>{'+'if total_pnl>=0 else ''}{total_pnl:.2f} USDT</code>\n"
        f"Max DD   : <code>{max_drawdown:.2%}</code>"
    )


def notify_error(context: str, error: str) -> None:
    _send(
        f"⚠️ <b>ERROR</b>\n\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> <code>{error[:300]}</code>\n"
        f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
    )


def notify_trailing_sl(
    symbol: str,
    old_sl: float,
    new_sl: float,
    current_price: float,
) -> None:
    _send(
        f"🔃 <b>TRAILING STOP UPDATED</b>\n\n"
        f"<b>{symbol}</b>\n"
        f"Old SL : <code>{old_sl:,.2f}</code>\n"
        f"New SL : <code>{new_sl:,.2f}</code>\n"
        f"Price  : <code>{current_price:,.2f}</code>\n"
        f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
    )


def notify_pipeline_start(pairs: list[str], timeframe: str) -> None:
    _send(
        f"🤖 <b>Pipeline started</b>\n"
        f"Pairs: <code>{', '.join(pairs)}</code>  |  TF: <code>{timeframe}</code>\n"
        f"<i>{datetime.utcnow().strftime('%H:%M UTC')}</i>"
    )
