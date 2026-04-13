"""
Position Monitor - עוקב אחר פוזיציות פתוחות.

רץ כל 5 דקות (בנפרד מ-pipeline הניתוח).

זרימה לכל פוזיציה:
  1. בדיקת סטטוס פקודות SL/TP על Bybit (primary)
  2. fallback: השוואת מחיר נוכחי ל-SL/TP
  3. אם לא נסגר: trailing stop (אם מופעל) + לוג unrealized PnL

EOD close: נשלט דרך .env (EOD_CLOSE_ENABLED=True, ברירת מחדל כבוי).
"""

import sqlite3
from datetime import datetime, timezone
from loguru import logger

from config.settings import settings
from execution.broker import Broker
from notifications import telegram
from storage.db import DB_PATH, update_trailing_sl, record_outcome


class PositionMonitor:

    def __init__(self) -> None:
        self._broker = Broker()

    def run(self) -> None:
        """בדיקה אחת של כל הפוזיציות הפתוחות."""
        open_trades = self._get_open_trades()

        if not open_trades:
            logger.debug("[Monitor] No open trades")
            return

        logger.info(f"[Monitor] Checking {len(open_trades)} open trades")

        if settings.EOD_CLOSE_ENABLED and self._is_eod():
            self._close_all_eod(open_trades)
            return

        for trade in open_trades:
            try:
                self._check_trade(trade)
            except Exception as e:
                logger.error(f"[Monitor] Error checking {trade['symbol']}: {e}")

    # ── בדיקת פוזיציה ──────────────────────────────────────────────────────────

    def _check_trade(self, trade: dict) -> None:
        symbol = trade["symbol"]
        side   = trade["side"]

        # ── שלב 1: מחיר נוכחי (נדרש גם ל-trailing) ────────────────────────────
        try:
            current_price = self._broker.get_current_price(symbol)
        except Exception as e:
            logger.error(f"[Monitor] Price fetch failed {symbol}: {e}")
            return

        # ── שלב 2: בדיקת fill על Bybit (primary) ──────────────────────────────
        close_reason = None
        close_price  = current_price

        sl_fill = self._broker.fetch_order_fill(symbol, trade.get("sl_order_id") or "")
        if sl_fill:
            close_reason = "SL"
            close_price  = sl_fill["price"] or trade["sl_price"]
            logger.info(f"[Monitor] SL order filled for {symbol} @ {close_price}")

        if not close_reason:
            tp_fill = self._broker.fetch_order_fill(symbol, trade.get("tp_order_id") or "")
            if tp_fill:
                close_reason = "TP"
                close_price  = tp_fill["price"] or trade["tp_price"]
                logger.info(f"[Monitor] TP order filled for {symbol} @ {close_price}")

        # ── שלב 3: fallback - השוואת מחיר ─────────────────────────────────────
        if not close_reason:
            if side == "buy":
                if current_price <= trade["sl_price"]:
                    close_reason = "SL"
                elif current_price >= trade["tp_price"]:
                    close_reason = "TP"
            else:
                if current_price >= trade["sl_price"]:
                    close_reason = "SL"
                elif current_price <= trade["tp_price"]:
                    close_reason = "TP"

        # ── סגירה ──────────────────────────────────────────────────────────────
        if close_reason:
            pnl     = self._calc_pnl(side, trade["price"], close_price, trade["quantity"])
            pnl_pct = pnl / (trade["price"] * trade["quantity"])
            self._close_trade(trade["id"], close_price, pnl, close_reason, trade)
            telegram.notify_trade_closed(
                symbol=symbol, side=side,
                close_reason=close_reason,
                entry_price=trade["price"],
                close_price=close_price,
                pnl=pnl, pnl_pct=pnl_pct,
            )
            logger.info(
                f"[Monitor] {symbol} closed via {close_reason} | "
                f"entry={trade['price']} close={close_price} PnL={pnl:+.2f}"
            )
            return

        # ── Trailing Stop (אם מופעל ואין סגירה) ────────────────────────────────
        if settings.TRAILING_STOP_ENABLED:
            self._update_trailing_stop(trade, current_price)
        else:
            unrealized = self._calc_pnl(side, trade["price"], current_price, trade["quantity"])
            logger.debug(
                f"[Monitor] {symbol} {side} | "
                f"entry={trade['price']} current={current_price} "
                f"unrealized={unrealized:+.2f} USDT"
            )

    # ── Trailing Stop ──────────────────────────────────────────────────────────

    def _update_trailing_stop(self, trade: dict, current_price: float) -> None:
        """
        מזיז SL למעלה (עבור buy) או למטה (עבור sell) כשהמחיר זז לטובתנו.
        לא נוגע ב-SL אם הוא כבר גבוה יותר.
        """
        side        = trade["side"]
        trail_pct   = settings.TRAILING_STOP_PCT
        trail_peak  = trade.get("trail_peak") or trade["price"]

        if side == "buy":
            new_peak = max(trail_peak, current_price)
            new_sl   = round(new_peak * (1 - trail_pct), 2)
            if new_sl <= trade["sl_price"]:
                return  # SL לא השתפר

        else:  # sell
            new_peak = min(trail_peak, current_price)
            new_sl   = round(new_peak * (1 + trail_pct), 2)
            if new_sl >= trade["sl_price"]:
                return  # SL לא השתפר

        # SL השתפר → עדכן בבורסה ו-DB
        close_side = "sell" if side == "buy" else "buy"
        new_order = self._broker.update_stop_loss(
            symbol=trade["symbol"],
            old_sl_order_id=trade.get("sl_order_id") or "",
            side=close_side,
            quantity=trade["quantity"],
            new_sl_price=new_sl,
        )

        if new_order:
            update_trailing_sl(
                trade_id=trade["id"],
                new_sl_price=new_sl,
                new_sl_order_id=new_order.order_id,
                trail_peak=new_peak,
            )
            logger.info(
                f"[Monitor] Trailing SL updated {trade['symbol']} "
                f"{trade['sl_price']} → {new_sl} (peak={new_peak})"
            )
            telegram.notify_trailing_sl(
                symbol=trade["symbol"],
                old_sl=trade["sl_price"],
                new_sl=new_sl,
                current_price=current_price,
            )

    # ── EOD ────────────────────────────────────────────────────────────────────

    def _close_all_eod(self, open_trades: list[dict]) -> None:
        logger.info("[Monitor] EOD: closing all positions")
        for trade in open_trades:
            try:
                close_price = self._broker.get_current_price(trade["symbol"])
                pnl         = self._calc_pnl(
                    trade["side"], trade["price"], close_price, trade["quantity"]
                )
                pnl_pct = pnl / (trade["price"] * trade["quantity"])
                self._close_trade(trade["id"], close_price, pnl, "EOD", trade)
                telegram.notify_trade_closed(
                    symbol=trade["symbol"], side=trade["side"],
                    close_reason="EOD (End of Day)",
                    entry_price=trade["price"], close_price=close_price,
                    pnl=pnl, pnl_pct=pnl_pct,
                )
            except Exception as e:
                logger.error(f"[Monitor] EOD close failed {trade['symbol']}: {e}")
                telegram.notify_error(f"EOD close {trade['symbol']}", str(e))

    # ── DB helpers ─────────────────────────────────────────────────────────────

    def _get_open_trades(self) -> list[dict]:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT id, symbol, side, quantity, price,
                       sl_price, tp_price, sl_order_id, tp_order_id, trail_peak
                FROM trades WHERE status = 'open'
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def _close_trade(
        self,
        trade_id: int,
        close_price: float,
        pnl: float,
        reason: str,
        trade: dict | None = None,
    ) -> None:
        today    = datetime.utcnow().strftime("%Y-%m-%d")
        close_ts = datetime.utcnow().isoformat()
        won      = 1 if pnl >= 0 else 0
        outcome  = {"TP": "TP_HIT", "SL": "SL_HIT"}.get(reason, reason)

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE trades SET status=?, pnl=? WHERE id=?",
                (f"closed_{reason.lower()}", round(pnl, 4), trade_id),
            )
            conn.execute(
                """
                INSERT INTO daily_summary (date, total_trades, wins, losses, total_pnl)
                VALUES (?, 1, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    total_trades = total_trades + 1,
                    wins         = wins + excluded.wins,
                    losses       = losses + excluded.losses,
                    total_pnl    = total_pnl + excluded.total_pnl
                """,
                (today, won, 1 - won, round(pnl, 4)),
            )
            # Fetch entry context for outcome record
            row = conn.execute(
                "SELECT timestamp, price, sl_price, tp_price, quantity FROM trades WHERE id=?",
                (trade_id,),
            ).fetchone()
            conn.commit()

        if row:
            entry_time, entry_price, sl_price, tp_price, qty = row
            notional  = (entry_price or 0) * (qty or 0)
            pnl_pct   = (pnl / notional * 100) if notional else 0.0
            entry_dt  = datetime.fromisoformat(entry_time) if entry_time else datetime.utcnow()
            duration  = (datetime.utcnow() - entry_dt).total_seconds() / 60

            # Pull signal scores from most recent decision for this symbol
            decision_id = math_s = vision_s = sent_s = final_s = fg = None
            with sqlite3.connect(DB_PATH) as conn:
                dec = conn.execute(
                    """
                    SELECT id, math_score, vision_score, sentiment_score, final_score
                    FROM decisions
                    WHERE symbol = (SELECT symbol FROM trades WHERE id=?)
                      AND executed = 1
                      AND timestamp <= ?
                    ORDER BY timestamp DESC LIMIT 1
                    """,
                    (trade_id, entry_time),
                ).fetchone()
            if dec:
                decision_id, math_s, vision_s, sent_s, final_s = dec

            record_outcome(
                trade_id=trade_id,
                symbol=trade["symbol"] if trade else "",
                side=trade["side"] if trade else "",
                outcome=outcome,
                entry_price=entry_price or 0,
                close_price=close_price,
                sl_price=sl_price or 0,
                tp_price=tp_price or 0,
                pnl_usdt=round(pnl, 4),
                pnl_pct=round(pnl_pct, 4),
                entry_time=entry_time or "",
                close_time=close_ts,
                duration_minutes=duration,
                decision_id=decision_id,
                math_score=math_s,
                vision_score=vision_s,
                sentiment_score=sent_s,
                final_score=final_s,
                fear_greed=fg,
            )

    # ── Utils ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_pnl(side: str, entry: float, close: float, qty: float) -> float:
        if side == "buy":
            return round((close - entry) * qty, 4)
        return round((entry - close) * qty, 4)

    @staticmethod
    def _is_eod() -> bool:
        now = datetime.now(timezone.utc)
        return (
            now.hour == settings.EOD_CLOSE_HOUR and
            now.minute >= settings.EOD_CLOSE_MINUTE
        )
