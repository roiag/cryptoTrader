"""
BacktestEngine — מריץ סימולציית מסחר על נתונים היסטוריים.

עקרונות:
  • אפס lookahead bias — כל החלטה מבוססת רק על נתונים שקדמו לה
  • משתמש בלוגיקת הציון של MathAgent כפי שהיא (ללא שינוי)
  • Fear & Greed משמש כ-proxy ל-Sentiment (Vision = 0 בשלב 1)
  • כניסה לעסקה = close הנר שגרם לסיגנל (+ slippage)
  • יציאה = הנר הראשון שנוגע ב-SL או TP; אם שניהם — SL מנצח (שמרני)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from loguru import logger

from agents.math_agent import MathAgent
from data.indicators import calculate_all, get_latest_snapshot

# ── ציונים ל-Fear & Greed ──────────────────────────────────────────────────────
# הגישה: contrarian — fear גבוה = bullish bias, greed גבוה = bearish bias
def fg_to_score(value: int) -> float:
    """ממיר ערך F&G (0-100) לציון -10..+10 (contrarian)."""
    normalized = (value - 50) / 50.0   # -1..+1  (50 = 0)
    return round(-normalized * 6.0, 2) # scale ל-±6 (לא ±10 — פחות דומיננטי)


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class BacktestTrade:
    symbol:       str
    entry_time:   pd.Timestamp
    side:         str            # "BUY" / "SELL"
    entry_price:  float
    sl_price:     float
    tp_price:     float
    sl_pct:       float
    tp_pct:       float
    # ציונים בכניסה
    math_score:   float
    fg_score:     float
    final_score:  float
    fear_greed:   int
    # תוצאה (ממולא אחרי סגירה)
    outcome:      str            = "OPEN"   # TP_HIT / SL_HIT / TIMEOUT
    exit_time:    Optional[pd.Timestamp] = None
    exit_price:   float          = 0.0
    pnl_pct:      float          = 0.0      # % מהמחיר
    bars_held:    int            = 0


@dataclass
class BacktestConfig:
    symbol:           str   = "BTC/USDT"
    timeframe:        str   = "15m"
    start:            str   = "2022-01-01"
    end:              str   = "2025-01-01"
    # אסטרטגיה
    threshold:        float = 4.5     # סף ציון לכניסה
    math_weight:      float = 0.80    # Vision = 0, redistribute
    sentiment_weight: float = 0.20
    # ניהול סיכון
    max_risk_pct:     float = 0.01    # 1% סיכון לעסקה
    slippage_pct:     float = 0.0005  # 0.05% slippage בכניסה
    max_bars_held:    int   = 96      # timeout: 24 שעות ב-15m
    lookback:         int   = 210     # נרות warm-up לאינדיקטורים


# ── Engine ─────────────────────────────────────────────────────────────────────

class BacktestEngine:
    """
    מריץ backtest של אסטרטגיית Math+Sentiment על DataFrame היסטורי.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.cfg = config
        # יוצרים instance של MathAgent ללא exchange (לא צריך — נתונים כבר טעונים)
        self._agent = object.__new__(MathAgent)

    def run(
        self,
        df: pd.DataFrame,
        fg_df: pd.DataFrame,
    ) -> list[BacktestTrade]:
        """
        מריץ את כל הסימולציה.

        Args:
            df:    OHLCV DataFrame עם indicators מחושבים (output של calculate_all)
            fg_df: Fear & Greed DataFrame (index=timestamp, column='value')

        Returns:
            רשימת BacktestTrade (כולל פתוחות ב-timeout)
        """
        cfg = self.cfg
        logger.info(
            f"[Backtest] {cfg.symbol} {cfg.timeframe}  "
            f"{cfg.start} to {cfg.end}  "
            f"threshold=+-{cfg.threshold}"
        )

        # חישוב אינדיקטורים פעם אחת על כל ה-DataFrame
        df = calculate_all(df)

        trades:      list[BacktestTrade] = []
        open_trade:  Optional[BacktestTrade] = None

        for i in range(cfg.lookback, len(df)):
            candle    = df.iloc[i]
            timestamp = df.index[i]

            # ── סגירת עסקה פתוחה ────────────────────────────────────────────
            if open_trade is not None:
                bars = i - df.index.get_loc(open_trade.entry_time)
                closed = self._try_close(open_trade, candle, bars)
                if closed:
                    trades.append(closed)
                    open_trade = None
                elif bars >= cfg.max_bars_held:
                    # Timeout — סוגרים במחיר ה-close
                    open_trade = self._close_timeout(open_trade, candle, bars)
                    trades.append(open_trade)
                    open_trade = None
                continue   # רק עסקה אחת בו-זמנית

            # ── חישוב ציון ──────────────────────────────────────────────────
            snap = get_latest_snapshot(df.iloc[: i + 1])
            math_score = self._calc_math(snap)
            fg_val     = self._get_fg(fg_df, timestamp)
            fg_score   = fg_to_score(fg_val)
            final      = round(
                math_score * cfg.math_weight + fg_score * cfg.sentiment_weight, 2
            )

            if abs(final) < cfg.threshold:
                continue

            # ── פתיחת עסקה ──────────────────────────────────────────────────
            side   = "BUY" if final > 0 else "SELL"
            slip   = cfg.slippage_pct * (1 if side == "BUY" else -1)
            entry  = round(candle["close"] * (1 + slip), 4)
            sl_pct, tp_pct = self._agent._calc_sl_tp(snap)

            if side == "BUY":
                sl = round(entry * (1 - sl_pct), 4)
                tp = round(entry * (1 + tp_pct), 4)
            else:
                sl = round(entry * (1 + sl_pct), 4)
                tp = round(entry * (1 - tp_pct), 4)

            open_trade = BacktestTrade(
                symbol=cfg.symbol,
                entry_time=timestamp,
                side=side,
                entry_price=entry,
                sl_price=sl,
                tp_price=tp,
                sl_pct=round(sl_pct, 5),
                tp_pct=round(tp_pct, 5),
                math_score=math_score,
                fg_score=fg_score,
                final_score=final,
                fear_greed=fg_val,
            )

        # עסקה שנשארה פתוחה בסוף ה-data
        if open_trade is not None:
            last_candle = df.iloc[-1]
            bars = len(df) - 1 - df.index.get_loc(open_trade.entry_time)
            open_trade = self._close_timeout(open_trade, last_candle, bars)
            trades.append(open_trade)

        wins   = sum(1 for t in trades if t.outcome == "TP_HIT")
        losses = sum(1 for t in trades if t.outcome == "SL_HIT")
        logger.info(
            f"[Backtest] Done — {len(trades)} trades  "
            f"W:{wins} L:{losses}  "
            f"WR:{wins/max(len(trades),1):.1%}"
        )
        return trades

    # ── Private helpers ────────────────────────────────────────────────────────

    def _try_close(
        self,
        trade: BacktestTrade,
        candle: pd.Series,
        bars: int,
    ) -> Optional[BacktestTrade]:
        """בודק אם הנר הנוכחי פוגע ב-SL או TP."""
        high, low = candle["high"], candle["low"]

        if trade.side == "BUY":
            sl_hit = low  <= trade.sl_price
            tp_hit = high >= trade.tp_price
        else:
            sl_hit = high >= trade.sl_price
            tp_hit = low  <= trade.tp_price

        # אם שניהם — שמרני: SL מנצח
        if sl_hit:
            return self._close(trade, trade.sl_price, "SL_HIT", candle.name, bars)
        if tp_hit:
            return self._close(trade, trade.tp_price, "TP_HIT", candle.name, bars)
        return None

    def _close(
        self,
        trade: BacktestTrade,
        exit_price: float,
        outcome: str,
        exit_time: pd.Timestamp,
        bars: int,
    ) -> BacktestTrade:
        if trade.side == "BUY":
            pnl = (exit_price - trade.entry_price) / trade.entry_price
        else:
            pnl = (trade.entry_price - exit_price) / trade.entry_price

        trade.exit_price = exit_price
        trade.exit_time  = exit_time
        trade.outcome    = outcome
        trade.pnl_pct    = round(pnl * 100, 4)
        trade.bars_held  = bars
        return trade

    def _close_timeout(
        self,
        trade: BacktestTrade,
        candle: pd.Series,
        bars: int,
    ) -> BacktestTrade:
        return self._close(trade, candle["close"], "TIMEOUT", candle.name, bars)

    def _calc_math(self, snap: dict) -> float:
        scores = {
            "trend":         self._agent._score_trend(snap),
            "momentum_rsi":  self._agent._score_rsi(snap),
            "momentum_macd": self._agent._score_macd(snap),
            "volatility_bb": self._agent._score_bollinger(snap),
            "volume_obv":    self._agent._score_obv(snap),
        }
        return self._agent._aggregate(scores)

    def _get_fg(self, fg_df: pd.DataFrame, ts: pd.Timestamp) -> int:
        mask = fg_df.index <= ts
        if not mask.any():
            return 50
        return int(fg_df.loc[mask, "value"].iloc[-1])
