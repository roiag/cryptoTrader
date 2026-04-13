"""
VisionBacktestEngine — מריץ ניתוח vision על עסקאות היסטוריות.

תהליך:
  1. טוען CSV של תוצאות math backtest (כבר קיים)
  2. לכל עסקה: טוען OHLCV מ-cache, מייצר גרף, שולח ל-LocalVisionAgent
  3. מוסיף עמודות vision לכל שורה
  4. שומר CSV מורחב לניתוח

הפלט מאפשר לענות על:
  - כשmath + vision מסכימים — מה win rate?
  - כשהם חלוקים — מה win rate?
  - האם vision מוסיף ערך מעבר ל-math בלבד?
"""

from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
from loguru import logger
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.table import Table
from rich import box

from agents.local_vision_agent import LocalVisionAgent
from backtest.chart_renderer import render_chart

console = Console()

CACHE_DIR = Path("backtest/cache")

# כמה נרות לפני נקודת הכניסה נציג בגרף
CHART_LOOKBACK = 100


def _load_ohlcv_cache(symbol: str, timeframe: str) -> pd.DataFrame:
    """
    טוען את קובץ ה-parquet מה-cache.
    מחפש קובץ שמתאים ל-symbol + timeframe (ללא תלות בתאריכים).
    """
    safe = symbol.replace("/", "_")
    pattern = f"{safe}_{timeframe}_*.parquet"
    matches = list(CACHE_DIR.glob(pattern))

    if not matches:
        raise FileNotFoundError(
            f"No OHLCV cache found for {symbol} {timeframe}. "
            f"Run the math backtest first to populate the cache."
        )

    # אם יש כמה — קח את הגדול ביותר (הכי הרבה נתונים)
    path = max(matches, key=lambda p: p.stat().st_size)
    logger.info(f"[VisionEngine] Loading OHLCV from {path.name}")
    df = pd.read_parquet(path)

    # ודא שה-index הוא DatetimeIndex עם UTC
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")

    return df


def _get_chart_slice(
    ohlcv: pd.DataFrame,
    entry_time: pd.Timestamp,
    lookback: int = CHART_LOOKBACK,
) -> pd.DataFrame:
    """
    מחזיר slice של lookback נרות שקדמו ל-entry_time (כולל).
    מבטיח zero lookahead bias — הגרף מראה רק מה שהיה ידוע בזמן הכניסה.
    """
    mask = ohlcv.index <= entry_time
    subset = ohlcv[mask]

    if len(subset) < 10:
        raise ValueError(
            f"Not enough candles before {entry_time} (found {len(subset)})"
        )

    return subset.tail(lookback)


def run_vision_backtest(
    input_csv: str | Path,
    model: str = "llama3.2-vision",
    timeframe: str = "15m",
    sample: int | None = None,
    output_csv: str | Path | None = None,
    delay_sec: float = 0.5,
) -> pd.DataFrame:
    """
    מריץ vision backtest על תוצאות math backtest קיימות.

    Args:
        input_csv:   קובץ CSV של תוצאות math backtest.
        model:       שם מודל Ollama (e.g. 'llama3.2-vision', 'qwen2-vl:7b').
        timeframe:   timeframe של הנרות (חייב להתאים ל-cache).
        sample:      אם מוגדר — בודק רק N עסקאות (sampling אקראי).
        output_csv:  נתיב לשמירת תוצאות (אם None — נגזר אוטומטית).
        delay_sec:   השהייה בין קריאות (מונע עומס על ה-GPU).

    Returns:
        DataFrame מורחב עם עמודות vision.
    """
    # ── טעינת CSV ────────────────────────────────────────────────────────────────
    df = pd.read_csv(input_csv, parse_dates=["entry_time", "exit_time"])
    df["entry_time"] = pd.to_datetime(df["entry_time"], utc=True)
    logger.info(f"[VisionEngine] Loaded {len(df)} trades from {input_csv}")

    # ── sampling ─────────────────────────────────────────────────────────────────
    if sample and sample < len(df):
        # sampling מאוזן: שווה חלוקה בין TP_HIT ו-SL_HIT
        wins   = df[df["outcome"] == "TP_HIT"].sample(
            min(sample // 2, len(df[df["outcome"] == "TP_HIT"])), random_state=42
        )
        losses = df[df["outcome"] == "SL_HIT"].sample(
            min(sample // 2, len(df[df["outcome"] == "SL_HIT"])), random_state=42
        )
        df = pd.concat([wins, losses]).sample(frac=1, random_state=42).reset_index(drop=True)
        logger.info(f"[VisionEngine] Sampled {len(df)} trades ({len(wins)} wins / {len(losses)} losses)")

    # ── טעינת OHLCV cache לכל סימבול ─────────────────────────────────────────────
    symbols  = df["symbol"].unique()
    ohlcv_by_symbol: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        ohlcv_by_symbol[sym] = _load_ohlcv_cache(sym, timeframe)

    # ── vision agent ──────────────────────────────────────────────────────────────
    agent = LocalVisionAgent(model=model)

    # עמודות חדשות
    vision_scores: list[float]      = []
    vision_trends: list[str]        = []
    vision_confs:  list[float]      = []
    vision_errors: list[str]        = []
    agreements:    list[str]        = []   # AGREE / DISAGREE / NEUTRAL

    total = len(df)
    console.rule(f"[bold cyan]Vision Backtest — {total} trades[/bold cyan]")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Analyzing charts...", total=total)

        for idx, row in df.iterrows():
            sym        = row["symbol"]
            entry_time = row["entry_time"]
            math_score = float(row["math_score"])

            try:
                # חתוך slice של נרות
                chart_slice = _get_chart_slice(ohlcv_by_symbol[sym], entry_time)

                # ייצר תמונה
                img_bytes = render_chart(chart_slice, symbol=sym)

                # ניתוח vision
                result = agent.analyze(img_bytes, symbol=sym, timeframe=timeframe)

                vision_scores.append(result.bias_score)
                vision_trends.append(result.trend)
                vision_confs.append(result.confidence)
                vision_errors.append("")

                # הסכמה: math ו-vision מצביעים לאותו כיוון?
                math_dir   = "bull" if math_score > 0 else "bear"
                vision_dir = "bull" if result.bias_score > 1.0 else (
                             "bear" if result.bias_score < -1.0 else "neutral"
                )
                if vision_dir == "neutral":
                    agreements.append("NEUTRAL")
                elif math_dir == vision_dir:
                    agreements.append("AGREE")
                else:
                    agreements.append("DISAGREE")

            except Exception as e:
                logger.warning(f"[VisionEngine] Error on trade {idx}: {e}")
                vision_scores.append(0.0)
                vision_trends.append("sideways")
                vision_confs.append(0.0)
                vision_errors.append(str(e))
                agreements.append("ERROR")

            progress.advance(task)

            # השהייה בין קריאות
            if delay_sec > 0:
                time.sleep(delay_sec)

    # ── הוסף עמודות לDF ──────────────────────────────────────────────────────────
    df["vision_score"]     = vision_scores
    df["vision_trend"]     = vision_trends
    df["vision_confidence"] = vision_confs
    df["vision_error"]     = vision_errors
    df["agreement"]        = agreements

    # ── שמירה ────────────────────────────────────────────────────────────────────
    if output_csv is None:
        stem       = Path(input_csv).stem
        output_csv = Path(input_csv).parent / f"{stem}_vision.csv"

    df.to_csv(output_csv, index=False)
    logger.info(f"[VisionEngine] Saved results → {output_csv}")

    # ── דוח מיידי ────────────────────────────────────────────────────────────────
    _print_report(df)

    return df


def _print_report(df: pd.DataFrame) -> None:
    """מדפיס ניתוח השוואתי: math vs vision vs combined."""
    closed = df[df["outcome"].isin(["TP_HIT", "SL_HIT"])]
    if closed.empty:
        return

    def wr(subset: pd.DataFrame) -> str:
        if subset.empty:
            return "—"
        rate = (subset["outcome"] == "TP_HIT").mean()
        color = "green" if rate >= 0.40 else "red"
        return f"[{color}]{rate:.1%}[/{color}]"

    def ev(subset: pd.DataFrame) -> str:
        if subset.empty:
            return "—"
        val = subset["pnl_pct"].mean()
        color = "green" if val > 0 else "red"
        return f"[{color}]{val:+.3f}%[/{color}]"

    agree    = closed[closed["agreement"] == "AGREE"]
    disagree = closed[closed["agreement"] == "DISAGREE"]
    neutral  = closed[closed["agreement"] == "NEUTRAL"]
    errors   = closed[closed["agreement"] == "ERROR"]

    table = Table(
        title="Vision Backtest — השוואת הסכמה math vs vision",
        box=box.SIMPLE_HEAD,
    )
    table.add_column("קבוצה",         style="dim")
    table.add_column("עסקאות",        justify="right")
    table.add_column("Win Rate",      justify="right")
    table.add_column("EV/trade",      justify="right")

    table.add_row("כל העסקאות",      str(len(closed)),    wr(closed),    ev(closed))
    table.add_row("Math + Vision מסכימים  ✓", str(len(agree)),  wr(agree),  ev(agree))
    table.add_row("Math + Vision חלוקים  ✗",  str(len(disagree)), wr(disagree), ev(disagree))
    table.add_row("Vision ניטרלי",    str(len(neutral)), wr(neutral), ev(neutral))
    if not errors.empty:
        table.add_row(f"שגיאות ({len(errors)})", str(len(errors)), "—", "—")

    console.print(table)

    # ── סיכום ──────────────────────────────────────────────────────────────────
    if not agree.empty and not disagree.empty:
        wr_agree    = (agree["outcome"] == "TP_HIT").mean()
        wr_disagree = (disagree["outcome"] == "TP_HIT").mean()
        delta       = wr_agree - wr_disagree

        color = "green" if delta > 0.05 else ("yellow" if delta > 0 else "red")
        console.print(
            f"\n  Vision adds [{color}]{delta:+.1%}[/{color}] WR when it agrees vs. disagrees.\n"
            f"  {'✓ Vision IS a useful filter.' if delta > 0.05 else '✗ Vision does NOT significantly improve results.'}"
        )
