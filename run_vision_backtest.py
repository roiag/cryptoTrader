"""
Vision Backtest — נקודת כניסה.

מריץ ניתוח vision על עסקאות היסטוריות ובודק האם vision מוסיף ערך מעבר ל-math.

שימוש:
    # בדיקה מהירה — 100 עסקאות
    python run_vision_backtest.py --sample 100

    # הרצה מלאה על BTC + ETH
    python run_vision_backtest.py --input combined_results.csv

    # מודל שונה
    python run_vision_backtest.py --model qwen2-vl:7b --sample 200

    # BTC בלבד עם פלט מותאם
    python run_vision_backtest.py --input BTC_USDT_results.csv --output btc_vision.csv

דרישות:
    1. Ollama מותקן ורץ:  ollama serve
    2. מודל vision מותקן: ollama pull llama3.2-vision
    3. CSV של math backtest קיים (הרץ תחילה: python run_backtest.py --both --export results.csv)
"""

import argparse
import sys
from pathlib import Path

from loguru import logger
from rich.console import Console

from backtest.vision_engine import run_vision_backtest

console = Console()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Vision Backtester — האם ניתוח ויזואלי מוסיף ערך על math agent?",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
דוגמאות:
  python run_vision_backtest.py --sample 100
  python run_vision_backtest.py --input combined_results.csv --model qwen2-vl:7b
  python run_vision_backtest.py --sample 200 --output my_results.csv
        """,
    )

    parser.add_argument(
        "--input",
        default="combined_results.csv",
        help="קובץ CSV של תוצאות math backtest (ברירת מחדל: combined_results.csv)",
    )
    parser.add_argument(
        "--model",
        default="llama3.2-vision",
        help="מודל Ollama לשימוש (ברירת מחדל: llama3.2-vision)",
    )
    parser.add_argument(
        "--timeframe",
        default="15m",
        help="timeframe של הנרות — חייב להתאים ל-cache (ברירת מחדל: 15m)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="הרץ רק N עסקאות (sampling מאוזן בין wins ו-losses). מומלץ: 100-200 לבדיקה",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="נתיב לקובץ CSV פלט (ברירת מחדל: <input>_vision.csv)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.3,
        help="השהייה בשניות בין קריאות לOllama (ברירת מחדל: 0.3)",
    )

    args = parser.parse_args()

    # ── בדיקות קדם-הרצה ────────────────────────────────────────────────────────
    input_path = Path(args.input)
    if not input_path.exists():
        console.print(f"[red]Error:[/red] Input file not found: {input_path}")
        console.print(
            "[dim]Run math backtest first:[/dim] "
            "python run_backtest.py --both --export results.csv"
        )
        sys.exit(1)

    # ── הצג פרמטרים ────────────────────────────────────────────────────────────
    console.print()
    console.print("[bold cyan]Vision Backtest[/bold cyan]")
    console.print(f"  Input:     {input_path}")
    console.print(f"  Model:     {args.model}")
    console.print(f"  Timeframe: {args.timeframe}")
    console.print(
        f"  Sample:    {'כל העסקאות' if args.sample is None else str(args.sample)}"
    )
    console.print()

    if args.sample and args.sample > 500:
        console.print(
            "[yellow]⚠ Running more than 500 trades with a local model may take a long time.[/yellow]\n"
            "[dim]Consider starting with --sample 100 to verify everything works.[/dim]\n"
        )

    # ── הרץ ────────────────────────────────────────────────────────────────────
    try:
        result_df = run_vision_backtest(
            input_csv=input_path,
            model=args.model,
            timeframe=args.timeframe,
            sample=args.sample,
            output_csv=args.output,
            delay_sec=args.delay,
        )

        output = args.output or str(input_path.parent / f"{input_path.stem}_vision.csv")
        console.print(f"\n[green]Done![/green] Results saved to: {output}")
        console.print(
            f"[dim]Open in Excel for detailed analysis — "
            f"filter by 'agreement' column to compare AGREE vs DISAGREE trades.[/dim]"
        )

    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user.[/yellow]")
        sys.exit(0)
    except Exception as e:
        logger.exception(e)
        console.print(f"\n[red]Error:[/red] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
