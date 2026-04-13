"""
הרצת Backtest — נקודת כניסה.

שימוש:
    python run_backtest.py                          # הגדרות ברירת מחדל
    python run_backtest.py --symbol ETH/USDT        # pair אחר
    python run_backtest.py --start 2023-01-01       # תאריך התחלה
    python run_backtest.py --threshold 5.0          # סף ציון שונה
    python run_backtest.py --export trades.csv      # יצוא תוצאות

תהליך:
  1. שולף OHLCV היסטורי מ-Bybit (מאחסן לקובץ cache)
  2. שולף Fear & Greed מ-alternative.me (מאחסן לקובץ cache)
  3. מריץ סימולציה ללא lookahead bias
  4. מדפיס דוח מפורט
  5. (אופציונלי) יוצא לCSV
"""

import argparse
import sys
from pathlib import Path

from loguru import logger
from rich.console import Console

from backtest.data_loader import DataLoader
from backtest.engine import BacktestEngine, BacktestConfig
from backtest.report import BacktestReport

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(description="Trading Bot Backtester")
    parser.add_argument("--symbol",     default="BTC/USDT",   help="Trading pair")
    parser.add_argument("--timeframe",  default="15m",         help="Candle timeframe")
    parser.add_argument("--start",      default="2022-01-01",  help="Start date YYYY-MM-DD")
    parser.add_argument("--end",        default="2025-01-01",  help="End date YYYY-MM-DD")
    parser.add_argument("--threshold",  default=4.5,  type=float, help="Signal threshold ±")
    parser.add_argument("--export",     default="",            help="CSV export path (optional)")
    parser.add_argument("--both",       action="store_true",   help="Run BTC + ETH together")
    args = parser.parse_args()

    # ── טעינת נתונים ────────────────────────────────────────────────────────────
    loader = DataLoader()

    symbols = ["BTC/USDT", "ETH/USDT"] if args.both else [args.symbol]
    all_trades = []

    for symbol in symbols:
        console.rule(f"[bold cyan]{symbol}[/bold cyan]")

        try:
            df     = loader.load_ohlcv(symbol, args.timeframe, args.start, args.end)
            fg_df  = loader.load_fear_greed(args.start, args.end)
        except Exception as e:
            logger.error(f"Failed to load data for {symbol}: {e}")
            continue

        console.print(
            f"[dim]Loaded {len(df):,} candles for {symbol} "
            f"({args.start} to {args.end})[/dim]"
        )

        # ── Backtest ─────────────────────────────────────────────────────────────
        config = BacktestConfig(
            symbol=symbol,
            timeframe=args.timeframe,
            start=args.start,
            end=args.end,
            threshold=args.threshold,
        )
        engine = BacktestEngine(config)
        trades = engine.run(df, fg_df)
        all_trades.extend(trades)

        # ── דוח ─────────────────────────────────────────────────────────────────
        report = BacktestReport(trades, symbol)
        report.print()

        # ── יצוא ─────────────────────────────────────────────────────────────────
        if args.export:
            path = Path(args.export) if len(symbols) == 1 else Path(f"{symbol.replace('/', '_')}_{args.export}")
            report.to_dataframe().to_csv(path, index=False)
            console.print(f"[green]Exported {len(trades)} trades to {path}[/green]")

    # סיכום משולב אם הרצנו יותר מ-pair אחד
    if args.both and all_trades:
        console.rule("[bold]Combined Summary — BTC + ETH[/bold]")
        combined = BacktestReport(all_trades, "BTC+ETH")
        combined._print_summary()
        combined._print_threshold_sweep()
        if args.export:
            combined.to_dataframe().to_csv(f"combined_{args.export}", index=False)


if __name__ == "__main__":
    main()
