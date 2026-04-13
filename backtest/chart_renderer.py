"""
ChartRenderer — מייצר תמונת candlestick מנתוני OHLCV היסטוריים.

קלט:  DataFrame עם עמודות open/high/low/close/volume ו-DatetimeIndex
פלט:  PNG כ-bytes (מוכן לשליחה ל-vision model)

כולל: EMA 20, EMA 50, Volume bars
"""

from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")   # חובה לפני כל import של matplotlib.pyplot

import mplfinance as mpf
import pandas as pd
from loguru import logger


# מספר נרות ברירת מחדל להצגה בגרף
DEFAULT_LOOKBACK = 100


def render_chart(
    df: pd.DataFrame,
    symbol: str = "",
    lookback: int = DEFAULT_LOOKBACK,
    mark_entry: pd.Timestamp | None = None,
) -> bytes:
    """
    מייצר גרף candlestick כ-PNG bytes.

    Args:
        df:          OHLCV DataFrame — עם DatetimeIndex. צריך לכלול לפחות lookback שורות.
        symbol:      שם הסימבול לכותרת הגרף.
        lookback:    מספר נרות להצגה (מסוף ה-DataFrame).
        mark_entry:  timestamp של נקודת הכניסה לעסקה (להדגשה בגרף, אופציונלי).

    Returns:
        PNG image as bytes.
    """
    if len(df) < 10:
        raise ValueError(f"Not enough candles to render chart (got {len(df)})")

    chart_df = df.tail(lookback).copy()

    # ── EMAs ────────────────────────────────────────────────────────────────────
    chart_df["ema20"] = chart_df["close"].ewm(span=20, adjust=False).mean()
    chart_df["ema50"] = chart_df["close"].ewm(span=50, adjust=False).mean()

    add_plots = [
        mpf.make_addplot(chart_df["ema20"], color="#2196F3", width=1.2, label="EMA20"),
        mpf.make_addplot(chart_df["ema50"], color="#FF9800", width=1.2, label="EMA50"),
    ]

    # ── סגנון ────────────────────────────────────────────────────────────────────
    mc = mpf.make_marketcolors(
        up="#26a69a", down="#ef5350",
        wick={"up": "#26a69a", "down": "#ef5350"},
        volume={"up": "#26a69a44", "down": "#ef535044"},
    )
    style = mpf.make_mpf_style(
        marketcolors=mc,
        gridstyle="--",
        gridcolor="#333333",
        facecolor="#1a1a2e",
        edgecolor="#444444",
        figcolor="#1a1a2e",
        rc={"axes.labelcolor": "#aaaaaa", "xtick.color": "#aaaaaa", "ytick.color": "#aaaaaa"},
    )

    # ── כותרת ────────────────────────────────────────────────────────────────────
    start_str = chart_df.index[0].strftime("%Y-%m-%d")
    end_str   = chart_df.index[-1].strftime("%Y-%m-%d %H:%M")
    title = f"{symbol}  |  {start_str} → {end_str}  ({lookback} candles)"

    # ── ציור ────────────────────────────────────────────────────────────────────
    buf = io.BytesIO()
    mpf.plot(
        chart_df,
        type="candle",
        style=style,
        addplot=add_plots,
        volume=True,
        figsize=(14, 8),
        title=title,
        tight_layout=True,
        savefig=dict(fname=buf, format="png", dpi=90, bbox_inches="tight"),
    )
    buf.seek(0)
    image_bytes = buf.read()

    logger.debug(
        f"[ChartRenderer] {symbol} rendered: {len(chart_df)} candles, "
        f"{len(image_bytes) / 1024:.1f} KB"
    )
    return image_bytes
