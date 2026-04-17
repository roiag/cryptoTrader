"""
WalkForwardValidator — אימות walk-forward למניעת overfitting.

עקרון:
  • מחלק את ההיסטוריה ל-N חלונות
  • בכל חלון: אופטימיזציה על train → בדיקה על test (out-of-sample)
  • תוצאות test הן הנתון האמין — לא train

    |—— Train 1 ——|— Test 1 —|
            |—— Train 2 ——|— Test 2 —|
                    |—— Train 3 ——|— Test 3 —|

מדד Consistency: עד כמה ה-profit factor יציב בין החלונות?
  • > 0.7 → אסטרטגיה יציבה
  • < 0.5 → אסטרטגיה לא יציבה, כנראה overfitting

שימוש:
    validator = WalkForwardValidator()
    result = validator.run("BTC/USDT", "15m", "2022-01-01", "2025-01-01", n_windows=4)
    result.print_summary()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from backtest.data_loader import DataLoader
from backtest.engine import BacktestConfig, BacktestEngine
from backtest.optimizer import ParameterOptimizer

console = Console()


@dataclass
class WalkForwardWindow:
    window_num:   int
    train_start:  str
    train_end:    str
    test_start:   str
    test_end:     str
    best_params:  dict               # פרמטרים שנמצאו ב-train
    # תוצאות test (out-of-sample)
    test_trades:  int
    test_wr:      float
    test_pf:      float
    test_ev:      float              # expected value לעסקה
    # תוצאות train (in-sample, להשוואה)
    train_pf:     float


@dataclass
class WalkForwardResult:
    symbol:      str
    timeframe:   str
    n_windows:   int
    windows:     list[WalkForwardWindow] = field(default_factory=list)
    # מדדים מצטברים
    oos_win_rate:   float = 0.0   # out-of-sample win rate ממוצע
    oos_pf:         float = 0.0   # out-of-sample profit factor ממוצע
    consistency:    float = 0.0   # יחס oos_pf / is_pf (קרוב ל-1 = טוב)
    is_robust:      bool  = False  # האם האסטרטגיה נחשבת יציבה?

    def print_summary(self) -> None:
        """מדפיס דוח מלא."""
        _print_wf_report(self)


class WalkForwardValidator:
    """
    מריץ walk-forward validation על האסטרטגיה.
    """

    # חלוקת חלון: 70% train, 30% test
    TRAIN_RATIO = 0.70

    def __init__(self) -> None:
        self._loader    = DataLoader()
        self._optimizer = ParameterOptimizer()

    def run(
        self,
        symbol:    str,
        timeframe: str,
        start:     str,   # "YYYY-MM-DD"
        end:       str,
        n_windows: int = 4,
    ) -> WalkForwardResult:
        """
        מריץ N חלונות של walk-forward.

        Args:
            n_windows: מספר חלונות (4 = 4 תקופות train/test)
        """
        logger.info(
            f"[WalkForward] {symbol} [{timeframe}] {start}→{end} | {n_windows} windows"
        )

        # חשב גבולות כל חלון
        windows_dates = self._build_windows(start, end, n_windows)

        result = WalkForwardResult(
            symbol=symbol, timeframe=timeframe, n_windows=n_windows
        )

        for i, (win_start, win_end, test_start, test_end) in enumerate(windows_dates):
            logger.info(
                f"[WalkForward] Window {i+1}/{n_windows}: "
                f"train={win_start}→{win_end}  test={test_start}→{test_end}"
            )

            # ── Train: מצא פרמטרים אופטימליים ──────────────────────────────
            try:
                opt = self._optimizer.optimize(
                    symbol, timeframe, win_start, win_end
                )
                best = opt.best_params
                train_pf = opt.best_pf
            except Exception as e:
                logger.warning(f"[WalkForward] Optimizer failed for window {i+1}: {e}")
                best = {"threshold": 4.5, "ATR_SL_MULTIPLIER": 1.5, "RR_RATIO": 2.0}
                train_pf = 0.0

            # ── Test: בדוק על out-of-sample ─────────────────────────────────
            try:
                test_trades, test_wr, test_pf, test_ev = self._run_test(
                    symbol, timeframe, test_start, test_end, best
                )
            except Exception as e:
                logger.warning(f"[WalkForward] Test failed for window {i+1}: {e}")
                test_trades, test_wr, test_pf, test_ev = 0, 0.0, 0.0, 0.0

            result.windows.append(WalkForwardWindow(
                window_num=i + 1,
                train_start=win_start, train_end=win_end,
                test_start=test_start, test_end=test_end,
                best_params=best,
                test_trades=test_trades,
                test_wr=test_wr,
                test_pf=test_pf,
                test_ev=test_ev,
                train_pf=train_pf,
            ))

        # ── חישוב מדדים מצטברים ───────────────────────────────────────────────
        self._calc_aggregate(result)
        result.print_summary()
        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _build_windows(
        self,
        start: str,
        end:   str,
        n:     int,
    ) -> list[tuple[str, str, str, str]]:
        """
        מחזיר רשימה של (train_start, train_end, test_start, test_end).
        חלונות חופפים (expanding window approach).
        """
        start_dt = datetime.fromisoformat(start)
        end_dt   = datetime.fromisoformat(end)
        total    = (end_dt - start_dt).days

        # כל חלון: train_pct מהזמן הכולל, test: הנותר / n
        # גישה: rolling window שזז קדימה
        window_size  = total // n          # גודל כל יחידת זמן
        train_size   = int(window_size * self.TRAIN_RATIO * (n - 1) / (n - 1))

        # פשוט יותר: expanding windows
        # window 1: train=[0, 70%], test=[70%, 70%+step]
        # window 2: train=[0, 70%+step], test=[70%+step, 70%+2step]
        # ...
        test_size = total // (n + 1)        # כל test period
        base_train = total - n * test_size  # train ראשוני

        windows = []
        for i in range(n):
            train_end_days  = base_train + i * test_size
            test_start_days = train_end_days
            test_end_days   = train_end_days + test_size

            train_start_dt  = start_dt
            train_end_dt    = start_dt + timedelta(days=train_end_days)
            test_start_dt   = train_end_dt
            test_end_dt     = min(start_dt + timedelta(days=test_end_days), end_dt)

            windows.append((
                train_start_dt.strftime("%Y-%m-%d"),
                train_end_dt.strftime("%Y-%m-%d"),
                test_start_dt.strftime("%Y-%m-%d"),
                test_end_dt.strftime("%Y-%m-%d"),
            ))

        return windows

    def _run_test(
        self,
        symbol:    str,
        timeframe: str,
        start:     str,
        end:       str,
        params:    dict,
    ) -> tuple[int, float, float, float]:
        """מריץ backtest על תקופת test עם פרמטרים נתונים. מחזיר (trades, wr, pf, ev)."""
        df    = self._loader.load_ohlcv(symbol, timeframe, start, end)
        fg_df = self._loader.load_fear_greed(start, end)

        cfg = BacktestConfig(
            symbol=symbol, timeframe=timeframe, start=start, end=end,
            threshold=params.get("threshold", 4.5),
            atr_sl_multiplier=params.get("ATR_SL_MULTIPLIER", 1.5),
            rr_ratio=params.get("RR_RATIO", 2.0),
        )
        engine = BacktestEngine(cfg)
        trades = engine.run(df, fg_df)

        closed = [t for t in trades if t.outcome in ("TP_HIT", "SL_HIT")]
        if not closed:
            return 0, 0.0, 0.0, 0.0

        wins  = [t for t in closed if t.outcome == "TP_HIT"]
        wr    = len(wins) / len(closed)
        gw    = sum(t.pnl_pct for t in wins if t.pnl_pct > 0)
        gl    = abs(sum(t.pnl_pct for t in closed if t.pnl_pct < 0))
        pf    = gw / gl if gl > 0 else 99.0
        ev    = sum(t.pnl_pct for t in closed) / len(closed)

        return len(closed), round(wr, 3), round(pf, 3), round(ev, 4)

    def _calc_aggregate(self, result: WalkForwardResult) -> None:
        valid = [w for w in result.windows if w.test_trades > 0]
        if not valid:
            return

        result.oos_win_rate = round(
            sum(w.test_wr for w in valid) / len(valid), 3
        )
        result.oos_pf = round(
            sum(w.test_pf for w in valid) / len(valid), 3
        )

        is_pfs  = [w.train_pf for w in valid if w.train_pf > 0]
        oos_pfs = [w.test_pf  for w in valid if w.test_pf  > 0]
        if is_pfs and oos_pfs:
            avg_is  = sum(is_pfs)  / len(is_pfs)
            avg_oos = sum(oos_pfs) / len(oos_pfs)
            result.consistency = round(min(avg_oos / avg_is, 1.0), 3) if avg_is > 0 else 0.0

        result.is_robust = (result.oos_pf >= 1.0 and result.consistency >= 0.5)


# ── Rich report ────────────────────────────────────────────────────────────────

def _print_wf_report(result: WalkForwardResult) -> None:
    console.rule(f"[bold cyan]Walk-Forward Results — {result.symbol} [{result.timeframe}][/bold cyan]")

    table = Table(box=box.SIMPLE_HEAD)
    table.add_column("Window",      style="dim")
    table.add_column("Train period")
    table.add_column("Test period")
    table.add_column("Best params", style="dim")
    table.add_column("Test trades", justify="right")
    table.add_column("Test WR",     justify="right")
    table.add_column("Test PF",     justify="right")
    table.add_column("Test EV",     justify="right")

    for w in result.windows:
        p = w.best_params
        pf_c = "green" if w.test_pf >= 1.0 else "red"
        wr_c = "green" if w.test_wr >= 0.52 else "red"
        ev_c = "green" if w.test_ev > 0 else "red"
        table.add_row(
            f"#{w.window_num}",
            f"{w.train_start} → {w.train_end}",
            f"{w.test_start} → {w.test_end}",
            f"thr={p.get('threshold')} atr={p.get('ATR_SL_MULTIPLIER')} rr={p.get('RR_RATIO')}",
            str(w.test_trades),
            f"[{wr_c}]{w.test_wr:.1%}[/{wr_c}]",
            f"[{pf_c}]{w.test_pf:.2f}[/{pf_c}]",
            f"[{ev_c}]{w.test_ev:+.3f}%[/{ev_c}]",
        )

    console.print(table)

    robust_color = "green" if result.is_robust else "red"
    robust_label = "✓ ROBUST" if result.is_robust else "✗ NOT ROBUST"

    console.print(Panel(
        f"\n"
        f"  [dim]OOS Win Rate    [/dim]  {result.oos_win_rate:.1%}\n"
        f"  [dim]OOS Prof. Factor[/dim]  {result.oos_pf:.2f}\n"
        f"  [dim]Consistency     [/dim]  {result.consistency:.0%}  "
        f"[dim](OOS PF / IS PF)[/dim]\n"
        f"  [dim]Verdict         [/dim]  [{robust_color}][bold]{robust_label}[/bold][/{robust_color}]\n",
        title="Walk-Forward Summary",
        border_style="cyan",
    ))
