"""
BacktestReport — מנתח תוצאות backtest ומייצר דוח מפורט.

מה הדוח מראה:
  • סטטיסטיקות בסיסיות: win rate, P&L, profit factor, max drawdown
  • ניתוח לפי תנאים: F&G level, שעת יום, day of week
  • Equity curve
  • ניתוח regime שוק (volatility-based)
  • הצעות לכוונון threshold
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import TYPE_CHECKING

import pandas as pd
from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

if TYPE_CHECKING:
    from backtest.engine import BacktestTrade

console = Console()


def _pct(n: float) -> str:
    return f"{n:+.2f}%"


def _sign_color(v: float) -> str:
    return "green" if v >= 0 else "red"


class BacktestReport:

    def __init__(self, trades: list["BacktestTrade"], symbol: str) -> None:
        self.trades = trades
        self.symbol = symbol
        self.closed = [t for t in trades if t.outcome != "OPEN"]
        self.wins   = [t for t in self.closed if t.outcome == "TP_HIT"]
        self.losses = [t for t in self.closed if t.outcome == "SL_HIT"]

    # ── Public ─────────────────────────────────────────────────────────────────

    def print(self) -> None:
        """מדפיס דוח מלא לטרמינל."""
        self._print_summary()
        self._print_by_fg()
        self._print_by_hour()
        self._print_by_regime()
        self._print_threshold_sweep()
        self._print_equity()

    def to_dataframe(self) -> pd.DataFrame:
        """מחזיר את כל ה-trades כ-DataFrame."""
        rows = []
        for t in self.trades:
            rows.append({
                "symbol":      t.symbol,
                "entry_time":  t.entry_time,
                "exit_time":   t.exit_time,
                "side":        t.side,
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "sl_price":    t.sl_price,
                "tp_price":    t.tp_price,
                "sl_pct":      t.sl_pct,
                "tp_pct":      t.tp_pct,
                "outcome":     t.outcome,
                "pnl_pct":     t.pnl_pct,
                "bars_held":   t.bars_held,
                "math_score":  t.math_score,
                "fg_score":    t.fg_score,
                "final_score": t.final_score,
                "fear_greed":  t.fear_greed,
            })
        return pd.DataFrame(rows)

    # ── Summary ────────────────────────────────────────────────────────────────

    def _print_summary(self) -> None:
        n  = len(self.closed)
        if n == 0:
            console.print("[red]No closed trades.[/red]")
            return

        win_rate    = len(self.wins) / n
        total_pnl   = sum(t.pnl_pct for t in self.closed)
        avg_win     = sum(t.pnl_pct for t in self.wins)   / max(len(self.wins),   1)
        avg_loss    = sum(t.pnl_pct for t in self.losses) / max(len(self.losses), 1)
        gross_win   = sum(t.pnl_pct for t in self.wins   if t.pnl_pct > 0)
        gross_loss  = abs(sum(t.pnl_pct for t in self.losses if t.pnl_pct < 0))
        pf          = gross_win / gross_loss if gross_loss > 0 else float("inf")
        max_dd      = self._max_drawdown()
        avg_bars    = sum(t.bars_held for t in self.closed) / n
        ev_per_trade = total_pnl / n

        wr_color = "green" if win_rate >= 0.52 else "red"
        pf_color = "green" if pf >= 1.0        else "red"
        dd_color = "red"   if max_dd < -10     else "yellow"
        ev_color = _sign_color(ev_per_trade)

        body = (
            f"\n"
            f"  [dim]Trades closed     [/dim]  {n}  "
            f"([green]{len(self.wins)} wins[/green] / [red]{len(self.losses)} losses[/red] / "
            f"[dim]{len([t for t in self.closed if t.outcome=='TIMEOUT'])} timeout[/dim])\n"
            f"  [dim]Win Rate          [/dim]  [{wr_color}][bold]{win_rate:.1%}[/bold][/{wr_color}]\n"
            f"  [dim]Profit Factor     [/dim]  [{pf_color}][bold]{pf:.2f}[/bold][/{pf_color}]\n"
            f"  [dim]EV / trade        [/dim]  [{ev_color}]{_pct(ev_per_trade)}[/{ev_color}]\n"
            f"  [dim]Total P&L         [/dim]  [{_sign_color(total_pnl)}]{_pct(total_pnl)}[/{_sign_color(total_pnl)}]"
            f"  [dim](sum of individual trade %)[/dim]\n"
            f"  [dim]Avg Win           [/dim]  [green]{_pct(avg_win)}[/green]\n"
            f"  [dim]Avg Loss          [/dim]  [red]{_pct(avg_loss)}[/red]\n"
            f"  [dim]Avg Bars Held     [/dim]  {avg_bars:.1f} נרות ({avg_bars*15/60:.1f} שעות)\n"
            f"  [dim]Max Drawdown      [/dim]  [{dd_color}]{_pct(max_dd)}[/{dd_color}]\n"
        )

        console.print(Panel(
            body,
            title=f"[bold]{self.symbol}[/bold]  Backtest Summary",
            border_style="cyan",
        ))

    # ── By Fear & Greed level ──────────────────────────────────────────────────

    def _print_by_fg(self) -> None:
        buckets = {
            "Extreme Fear (0-25)":  lambda v: v <= 25,
            "Fear (26-45)":         lambda v: 26 <= v <= 45,
            "Neutral (46-55)":      lambda v: 46 <= v <= 55,
            "Greed (56-75)":        lambda v: 56 <= v <= 75,
            "Extreme Greed (76+)":  lambda v: v >= 76,
        }

        table = Table(title="ביצועים לפי Fear & Greed", box=box.SIMPLE_HEAD)
        table.add_column("Regime", style="dim")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("EV/trade", justify="right")
        table.add_column("Avg Bars", justify="right")

        for label, fn in buckets.items():
            group = [t for t in self.closed if fn(t.fear_greed)]
            if not group:
                continue
            wr  = sum(1 for t in group if t.outcome == "TP_HIT") / len(group)
            ev  = sum(t.pnl_pct for t in group) / len(group)
            ab  = sum(t.bars_held for t in group) / len(group)
            wr_c = "green" if wr >= 0.52 else "red"
            ev_c = _sign_color(ev)
            table.add_row(
                label, str(len(group)),
                f"[{wr_c}]{wr:.1%}[/{wr_c}]",
                f"[{ev_c}]{_pct(ev)}[/{ev_c}]",
                f"{ab:.0f}",
            )

        console.print(table)

    # ── By hour of day ─────────────────────────────────────────────────────────

    def _print_by_hour(self) -> None:
        buckets: dict[str, list] = defaultdict(list)
        for t in self.closed:
            hour = t.entry_time.hour
            if 0 <= hour < 6:
                buckets["00-06 UTC"].append(t)
            elif 6 <= hour < 12:
                buckets["06-12 UTC"].append(t)
            elif 12 <= hour < 18:
                buckets["12-18 UTC"].append(t)
            else:
                buckets["18-24 UTC"].append(t)

        table = Table(title="ביצועים לפי שעה (UTC)", box=box.SIMPLE_HEAD)
        table.add_column("Window", style="dim")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("EV/trade", justify="right")

        for label in ["00-06 UTC", "06-12 UTC", "12-18 UTC", "18-24 UTC"]:
            group = buckets.get(label, [])
            if not group:
                continue
            wr  = sum(1 for t in group if t.outcome == "TP_HIT") / len(group)
            ev  = sum(t.pnl_pct for t in group) / len(group)
            wr_c = "green" if wr >= 0.52 else "red"
            ev_c = _sign_color(ev)
            table.add_row(
                label, str(len(group)),
                f"[{wr_c}]{wr:.1%}[/{wr_c}]",
                f"[{ev_c}]{_pct(ev)}[/{ev_c}]",
            )

        console.print(table)

    # ── By market regime (volatility-based) ───────────────────────────────────

    def _print_by_regime(self) -> None:
        """
        מחלק לפי sl_pct כ-proxy ל-volatility:
          low vol:  sl < 1%
          mid vol:  1% ≤ sl < 2.5%
          high vol: sl ≥ 2.5%
        """
        regimes = {
            "Low Vol  (SL < 1%)":     lambda t: t.sl_pct < 0.01,
            "Mid Vol  (1-2.5%)":      lambda t: 0.01 <= t.sl_pct < 0.025,
            "High Vol (SL >= 2.5%)":  lambda t: t.sl_pct >= 0.025,
        }

        table = Table(title="ביצועים לפי Volatility Regime", box=box.SIMPLE_HEAD)
        table.add_column("Regime", style="dim")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("EV/trade", justify="right")
        table.add_column("Avg R:R actual", justify="right")

        for label, fn in regimes.items():
            group = [t for t in self.closed if fn(t)]
            if not group:
                continue
            wr    = sum(1 for t in group if t.outcome == "TP_HIT") / len(group)
            ev    = sum(t.pnl_pct for t in group) / len(group)
            rr    = (sum(t.tp_pct for t in group) / sum(t.sl_pct for t in group)) if sum(t.sl_pct for t in group) > 0 else 0
            wr_c  = "green" if wr >= 0.52 else "red"
            ev_c  = _sign_color(ev)
            table.add_row(
                label, str(len(group)),
                f"[{wr_c}]{wr:.1%}[/{wr_c}]",
                f"[{ev_c}]{_pct(ev)}[/{ev_c}]",
                f"{rr:.2f}×",
            )

        console.print(table)

    # ── Threshold sweep ────────────────────────────────────────────────────────

    def _print_threshold_sweep(self) -> None:
        """
        מדמה threshold שונים על אותם signals.
        עוזר להבין מה הסף האופטימלי.
        """
        table = Table(title="ניתוח Threshold — כמה עסקאות ואיזה win rate לכל סף", box=box.SIMPLE_HEAD)
        table.add_column("Threshold", style="dim", justify="right")
        table.add_column("Trades", justify="right")
        table.add_column("Win Rate", justify="right")
        table.add_column("EV/trade", justify="right")
        table.add_column("Total P&L", justify="right")

        for thr in [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0, 7.0]:
            group = [t for t in self.closed if abs(t.final_score) >= thr]
            if not group:
                table.add_row(f"±{thr}", "0", "—", "—", "—")
                continue
            wr  = sum(1 for t in group if t.outcome == "TP_HIT") / len(group)
            ev  = sum(t.pnl_pct for t in group) / len(group)
            tot = sum(t.pnl_pct for t in group)
            wr_c  = "green" if wr >= 0.52 else "red"
            ev_c  = _sign_color(ev)
            tot_c = _sign_color(tot)
            table.add_row(
                f"±{thr}", str(len(group)),
                f"[{wr_c}]{wr:.1%}[/{wr_c}]",
                f"[{ev_c}]{_pct(ev)}[/{ev_c}]",
                f"[{tot_c}]{_pct(tot)}[/{tot_c}]",
            )

        console.print(table)

    # ── Equity curve (text) ────────────────────────────────────────────────────

    def _print_equity(self) -> None:
        if not self.closed:
            return

        # ממיין לפי זמן יציאה ומחשב equity curve עם מינוף 1:1
        sorted_t = sorted(self.closed, key=lambda t: t.exit_time or t.entry_time)
        equity   = 100.0
        curve    = [equity]
        for t in sorted_t:
            equity *= (1 + t.pnl_pct / 100)
            curve.append(equity)

        max_eq = max(curve)
        min_eq = min(curve)

        console.print(Panel(
            f"\n"
            f"  [dim]Final equity      [/dim]  [{_sign_color(equity-100)}][bold]{equity:.1f}[/bold] "
            f"(התחלנו מ-100)[/{_sign_color(equity-100)}]\n"
            f"  [dim]Peak equity       [/dim]  [green]{max_eq:.1f}[/green]\n"
            f"  [dim]Trough equity     [/dim]  [red]{min_eq:.1f}[/red]\n"
            f"  [dim]Max Drawdown      [/dim]  [red]{self._max_drawdown():.2f}%[/red]\n",
            title="Equity Curve Summary",
            border_style="dim",
        ))

    # ── Utils ──────────────────────────────────────────────────────────────────

    def _max_drawdown(self) -> float:
        """Maximum drawdown כ-% מהפסגה."""
        if not self.closed:
            return 0.0
        sorted_t = sorted(self.closed, key=lambda t: t.exit_time or t.entry_time)
        equity = 100.0
        peak   = 100.0
        max_dd = 0.0
        for t in sorted_t:
            equity *= (1 + t.pnl_pct / 100)
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
        return round(max_dd, 2)
