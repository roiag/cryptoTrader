"""
חישוב אינדיקטורים טכניים על DataFrame של OHLCV.
משתמש ב-pandas-ta - מהיר, פשוט, ללא תלויות חיצוניות כבדות.
"""

import pandas as pd
import pandas_ta as ta
from loguru import logger


FIBONACCI_LOOKBACK = 50   # נרות לחישוב swing high/low


def calculate_all(df: pd.DataFrame) -> pd.DataFrame:
    """
    מחשב את כל האינדיקטורים על DataFrame נתון.
    מחזיר את אותו DataFrame עם עמודות נוספות.
    """
    df = df.copy()

    # ── Trend: EMA ─────────────────────────────────────────────────────────────
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200)

    # ── Momentum: RSI ──────────────────────────────────────────────────────────
    df["rsi"] = ta.rsi(df["close"], length=14)

    # ── Momentum: MACD ─────────────────────────────────────────────────────────
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None:
        df["macd_line"] = macd["MACD_12_26_9"]
        df["macd_signal"] = macd["MACDs_12_26_9"]
        df["macd_hist"] = macd["MACDh_12_26_9"]

    # ── Volatility: Bollinger Bands ────────────────────────────────────────────
    bb = ta.bbands(df["close"], length=20, std=2)
    if bb is not None:
        # column names differ between pandas-ta versions - find them dynamically
        cols = bb.columns.tolist()
        upper = next((c for c in cols if c.startswith("BBU")), None)
        mid   = next((c for c in cols if c.startswith("BBM")), None)
        lower = next((c for c in cols if c.startswith("BBL")), None)
        if upper and mid and lower:
            df["bb_upper"] = bb[upper]
            df["bb_mid"]   = bb[mid]
            df["bb_lower"] = bb[lower]
            df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # ── Volatility: ATR ────────────────────────────────────────────────────────
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # ── Volume: OBV ────────────────────────────────────────────────────────────
    df["obv"] = ta.obv(df["close"], df["volume"])

    # ── Volume: VWAP (יומי) ────────────────────────────────────────────────────
    try:
        df["vwap"] = ta.vwap(df["high"], df["low"], df["close"], df["volume"])
    except Exception:
        pass  # VWAP דורש index עם timezone - לא תמיד זמין

    # ── Fibonacci Retracement ──────────────────────────────────────────────────
    rolling_high = df["high"].rolling(FIBONACCI_LOOKBACK).max()
    rolling_low  = df["low"].rolling(FIBONACCI_LOOKBACK).min()
    diff = rolling_high - rolling_low
    df["fib_high"] = rolling_high
    df["fib_low"]  = rolling_low
    df["fib_0236"] = rolling_high - diff * 0.236
    df["fib_0382"] = rolling_high - diff * 0.382
    df["fib_050"]  = rolling_high - diff * 0.500
    df["fib_0618"] = rolling_high - diff * 0.618
    df["fib_0786"] = rolling_high - diff * 0.786

    logger.debug(f"Indicators calculated on {len(df)} candles")
    return df


def get_latest_snapshot(df: pd.DataFrame) -> dict:
    """
    מחזיר dict עם הערכים העדכניים ביותר של כל האינדיקטורים.
    נוח להעביר לסוכנים.
    """
    row = df.iloc[-1]
    prev = df.iloc[-2]

    def safe(val):
        return round(float(val), 4) if pd.notna(val) else None

    return {
        "price": safe(row["close"]),
        "volume": safe(row["volume"]),
        "ema_20": safe(row.get("ema_20")),
        "ema_50": safe(row.get("ema_50")),
        "ema_200": safe(row.get("ema_200")),
        "rsi": safe(row.get("rsi")),
        "macd_line": safe(row.get("macd_line")),
        "macd_signal": safe(row.get("macd_signal")),
        "macd_hist": safe(row.get("macd_hist")),
        "macd_hist_prev": safe(prev.get("macd_hist")),  # לכיוון ה-histogram
        "bb_upper": safe(row.get("bb_upper")),
        "bb_mid": safe(row.get("bb_mid")),
        "bb_lower": safe(row.get("bb_lower")),
        "bb_width": safe(row.get("bb_width")),
        "atr": safe(row.get("atr")),
        "obv": safe(row.get("obv")),
        "obv_prev_5": safe(df["obv"].iloc[-6]) if "obv" in df.columns else None,
        "vwap": safe(row.get("vwap")),
        # Fibonacci retracement levels
        "fib_high": safe(row.get("fib_high")),
        "fib_low":  safe(row.get("fib_low")),
        "fib_0236": safe(row.get("fib_0236")),
        "fib_0382": safe(row.get("fib_0382")),
        "fib_050":  safe(row.get("fib_050")),
        "fib_0618": safe(row.get("fib_0618")),
        "fib_0786": safe(row.get("fib_0786")),
    }
