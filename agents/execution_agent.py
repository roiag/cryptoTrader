"""
Execution Agent - מחליט אם לסחור ומבצע את הפקודות.

כל החלטה - BUY / SELL / HOLD / BLOCKED - נשמרת ב-DB עם נימוק מלא.
"""

from dataclasses import dataclass

from loguru import logger

from agents.math_agent import MathResult
from agents.vision_agent import VisionResult
from agents.sentiment_agent import SentimentResult
from execution.broker import Broker, OrderResult
from notifications import telegram
from risk.guard import RiskGuard
from risk.position_sizer import calculate as size_position, SizeResult
from storage.db import log_trade, log_decision, get_daily_pnl_pct, get_open_symbols_from_db

TRADE_THRESHOLD = 3.5


@dataclass
class ExecutionDecision:
    symbol: str
    action: str             # "BUY" / "SELL" / "HOLD"
    final_score: float
    executed: bool = False
    rejection_reason: str = ""
    entry_order: OrderResult | None = None
    sl_order:    OrderResult | None = None
    tp_order:    OrderResult | None = None
    size: SizeResult | None = None
    decision_id: int = 0    # id ב-DB


class ExecutionAgent:

    def __init__(self) -> None:
        self._broker = Broker()
        self._guard  = RiskGuard()

    def execute(
        self,
        math: MathResult,
        vision: VisionResult,
        sentiment: SentimentResult,
        final_score: float,
        screenshot_file: str = "",   # שם קובץ הצילום שנלקח בסבב זה
    ) -> ExecutionDecision:
        symbol = math.symbol

        # ── שלב 1: threshold ──────────────────────────────────────────────────
        if abs(final_score) < TRADE_THRESHOLD:
            logger.info(
                f"[ExecutionAgent] HOLD {symbol} — score {final_score:+.2f} "
                f"below threshold ±{TRADE_THRESHOLD}"
            )
            dec_id = log_decision(
                symbol=symbol,
                timeframe=math.timeframe,
                math_score=math.bias_score,
                vision_score=vision.bias_score,
                sentiment_score=sentiment.bias_score,
                final_score=final_score,
                action="HOLD",
                executed=False,
                rejection_reason=f"Score {final_score:+.2f} below threshold ±{TRADE_THRESHOLD}",
                math_reasoning=math.reasoning,
                vision_reasoning=vision.key_observation,
                sentiment_summary=sentiment.summary,
                screenshot_file=screenshot_file,
            )
            return ExecutionDecision(
                symbol=symbol, action="HOLD",
                final_score=final_score,
                rejection_reason=f"Score {final_score:+.2f} below threshold ±{TRADE_THRESHOLD}",
                decision_id=dec_id,
            )

        side   = "buy"  if final_score > 0 else "sell"
        action = "BUY"  if side == "buy"  else "SELL"

        # ── שלב 2: Risk Guard ──────────────────────────────────────────────────
        daily_pnl = get_daily_pnl_pct()
        try:
            open_symbols = self._broker.get_open_position_symbols()
        except Exception as e:
            # ללא API key: נסתמך על ה-DB במקום על Bybit
            logger.warning(
                f"[ExecutionAgent] Exchange positions unavailable ({e}), "
                f"falling back to DB"
            )
            open_symbols = get_open_symbols_from_db()

        guard_result = self._guard.check(
            symbol=symbol, side=side,
            math_score=math.bias_score, vision_score=vision.bias_score,
            math_confidence=math.confidence, vision_confidence=vision.confidence,
            sl_pct=math.sl_distance_pct, tp_pct=math.tp_distance_pct,
            open_positions=open_symbols, daily_pnl_pct=daily_pnl,
        )

        if not guard_result:
            dec_id = log_decision(
                symbol=symbol, timeframe=math.timeframe,
                math_score=math.bias_score, vision_score=vision.bias_score,
                sentiment_score=sentiment.bias_score, final_score=final_score,
                action=action, executed=False,
                rejection_reason=guard_result.rejection_reason,
                math_reasoning=math.reasoning,
                vision_reasoning=vision.key_observation,
                sentiment_summary=sentiment.summary,
                screenshot_file=screenshot_file,
            )
            return ExecutionDecision(
                symbol=symbol, action=action, final_score=final_score,
                executed=False, rejection_reason=guard_result.rejection_reason,
                decision_id=dec_id,
            )

        # ── שלב 3: Position Sizer ──────────────────────────────────────────────
        balance     = self._broker.get_balance()
        entry_price = self._broker.get_current_price(symbol)

        size = size_position(
            symbol=symbol, side=side, entry_price=entry_price,
            sl_pct=math.sl_distance_pct, tp_pct=math.tp_distance_pct,
            balance=balance,
        )

        if not size.is_valid:
            dec_id = log_decision(
                symbol=symbol, timeframe=math.timeframe,
                math_score=math.bias_score, vision_score=vision.bias_score,
                sentiment_score=sentiment.bias_score, final_score=final_score,
                action=action, executed=False,
                rejection_reason=f"Sizer: {size.rejection_reason}",
                entry_price=entry_price,
                math_reasoning=math.reasoning,
                vision_reasoning=vision.key_observation,
                sentiment_summary=sentiment.summary,
                screenshot_file=screenshot_file,
            )
            return ExecutionDecision(
                symbol=symbol, action=action, final_score=final_score,
                executed=False,
                rejection_reason=f"Position sizer: {size.rejection_reason}",
                size=size, decision_id=dec_id,
            )

        # ── שלב 4: ביצוע ──────────────────────────────────────────────────────
        close_side = "sell" if side == "buy" else "buy"

        try:
            entry_order = self._broker.place_market_order(
                symbol=symbol, side=side, quantity=size.quantity
            )
            sl_order = self._broker.place_stop_loss(
                symbol=symbol, side=close_side,
                quantity=size.quantity, stop_price=size.sl_price,
            )
            tp_order = self._broker.place_take_profit(
                symbol=symbol, side=close_side,
                quantity=size.quantity, tp_price=size.tp_price,
            )

            actual_entry = entry_order.price or entry_price

            # שמירה לטבלת trades (מעקב סטטוס)
            log_trade(
                symbol=symbol, side=side, quantity=size.quantity,
                entry_price=actual_entry, sl_price=size.sl_price,
                tp_price=size.tp_price, order_id=entry_order.order_id,
                final_score=final_score,
                sl_order_id=sl_order.order_id if sl_order else "",
                tp_order_id=tp_order.order_id if tp_order else "",
            )

            # שמירה לטבלת decisions (נימוק מלא)
            dec_id = log_decision(
                symbol=symbol, timeframe=math.timeframe,
                math_score=math.bias_score, vision_score=vision.bias_score,
                sentiment_score=sentiment.bias_score, final_score=final_score,
                action=action, executed=True,
                entry_price=actual_entry,
                sl_price=size.sl_price, tp_price=size.tp_price,
                quantity=size.quantity, notional=size.notional,
                risk_amount=size.risk_amount,
                math_reasoning=math.reasoning,
                vision_reasoning=f"{vision.trend} | {vision.key_observation} | patterns: {', '.join(vision.patterns) or 'none'}",
                sentiment_summary=f"F&G={sentiment.fear_greed_value} ({sentiment.fear_greed_label}) | {sentiment.summary}",
                screenshot_file=screenshot_file,
            )

            logger.info(
                f"[ExecutionAgent] {action} {symbol} | "
                f"qty={size.quantity} entry={actual_entry} "
                f"SL={size.sl_price} TP={size.tp_price} risk=${size.risk_amount:.2f}"
            )

            telegram.notify_trade_opened(
                symbol=symbol, side=side, quantity=size.quantity,
                entry_price=actual_entry, sl_price=size.sl_price,
                tp_price=size.tp_price, notional=size.notional,
                risk_amount=size.risk_amount, final_score=final_score,
            )

            return ExecutionDecision(
                symbol=symbol, action=action, final_score=final_score,
                executed=True, entry_order=entry_order,
                sl_order=sl_order, tp_order=tp_order,
                size=size, decision_id=dec_id,
            )

        except Exception as e:
            logger.error(f"[ExecutionAgent] Order failed for {symbol}: {e}")
            dec_id = log_decision(
                symbol=symbol, timeframe=math.timeframe,
                math_score=math.bias_score, vision_score=vision.bias_score,
                sentiment_score=sentiment.bias_score, final_score=final_score,
                action=action, executed=False,
                rejection_reason=f"Order error: {e}",
                entry_price=entry_price,
                sl_price=size.sl_price, tp_price=size.tp_price,
                quantity=size.quantity, notional=size.notional,
                risk_amount=size.risk_amount,
                math_reasoning=math.reasoning,
                vision_reasoning=vision.key_observation,
                sentiment_summary=sentiment.summary,
                screenshot_file=screenshot_file,
            )
            return ExecutionDecision(
                symbol=symbol, action=action, final_score=final_score,
                executed=False, rejection_reason=f"Order error: {e}",
                size=size, decision_id=dec_id,
            )
