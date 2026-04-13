"""
BacktestDataLoader — שולף ומאחסן נתונים היסטוריים.

מקורות:
  • OHLCV  — Bybit דרך ccxt (ציבורי, ללא API key)
  • Fear & Greed — alternative.me (ציבורי, ללא API key)

כל בקשה נשמרת ב-backtest/cache/ לשימוש חוזר.
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import pandas as pd
import requests
from loguru import logger

CACHE_DIR = Path("backtest/cache")
CACHE_DIR.mkdir(parents=True, exist_ok=True)


class DataLoader:

    def __init__(self) -> None:
        self._exchange = ccxt.bybit({
            "enableRateLimit": True,
            "options": {"defaultType": "linear"},
        })

    # ── OHLCV ──────────────────────────────────────────────────────────────────

    def load_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        start: str,   # "YYYY-MM-DD"
        end: str,     # "YYYY-MM-DD"
    ) -> pd.DataFrame:
        """
        מחזיר DataFrame עם עמודות open/high/low/close/volume.
        מאחסן בקובץ parquet לשימוש חוזר.
        """
        safe = symbol.replace("/", "_")
        cache = CACHE_DIR / f"{safe}_{timeframe}_{start}_{end}.parquet"

        if cache.exists():
            logger.info(f"[DataLoader] Cache hit: {cache.name}")
            return pd.read_parquet(cache)

        logger.info(f"[DataLoader] Fetching {symbol} {timeframe} {start}→{end} ...")
        since_ms = int(datetime.fromisoformat(start).replace(tzinfo=timezone.utc).timestamp() * 1000)
        until_ms = int(datetime.fromisoformat(end).replace(tzinfo=timezone.utc).timestamp() * 1000)

        all_rows: list[list] = []
        current = since_ms

        while current < until_ms:
            batch = self._exchange.fetch_ohlcv(symbol, timeframe, since=current, limit=1000)
            if not batch:
                break
            all_rows.extend(batch)
            last_ts = batch[-1][0]
            if last_ts >= until_ms:
                break
            current = last_ts + 1
            time.sleep(self._exchange.rateLimit / 1000)

        if not all_rows:
            raise ValueError(f"No data returned for {symbol} {timeframe}")

        df = pd.DataFrame(all_rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()
        df = df[df.index < pd.Timestamp(end, tz="UTC")]
        df = df.astype(float)

        df.to_parquet(cache)
        logger.info(f"[DataLoader] Saved {len(df):,} candles → {cache.name}")
        return df

    # ── Fear & Greed ───────────────────────────────────────────────────────────

    def load_fear_greed(self, start: str, end: str) -> pd.DataFrame:
        """
        מחזיר DataFrame עם עמודת 'value' (0-100) ו-index של תאריכים יומיים.
        """
        cache = CACHE_DIR / f"fear_greed_{start}_{end}.parquet"

        if cache.exists():
            logger.info(f"[DataLoader] Cache hit: {cache.name}")
            return pd.read_parquet(cache)

        logger.info("[DataLoader] Fetching Fear & Greed history ...")
        days_needed = (
            datetime.fromisoformat(end) - datetime.fromisoformat(start)
        ).days + 30

        resp = requests.get(
            "https://api.alternative.me/fng/",
            params={"limit": days_needed, "format": "json"},
            timeout=15,
        )
        resp.raise_for_status()
        raw = resp.json().get("data", [])

        df = pd.DataFrame(raw)[["timestamp", "value", "value_classification"]]
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="s", utc=True)
        df["value"] = df["value"].astype(int)
        df = df.drop_duplicates("timestamp").set_index("timestamp").sort_index()

        df.to_parquet(cache)
        logger.info(f"[DataLoader] Saved {len(df)} F&G rows → {cache.name}")
        return df

    def get_fear_greed_at(self, fg_df: pd.DataFrame, ts: pd.Timestamp) -> int:
        """מחזיר את ערך ה-F&G הקרוב ביותר שקדם ל-ts."""
        mask = fg_df.index <= ts
        if not mask.any():
            return 50
        return int(fg_df.loc[mask, "value"].iloc[-1])
