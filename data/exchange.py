"""
חיבור ל-Bybit - שליפת נתוני שוק בזמן אמת.
נתוני OHLCV הם public - לא צריך API key לקריאה.
API key נדרש רק לביצוע פקודות מסחר.
"""

from dataclasses import dataclass

import ccxt
import pandas as pd
from loguru import logger
from config.settings import settings


@dataclass
class FuturesData:
    symbol:          str
    funding_rate:    float   # current rate, e.g. 0.0001 = 0.01%
    funding_rate_8h: float   # annualised proxy: rate * 3 * 365
    next_funding_ts: int     # unix ms
    open_interest:   float   # USD notional
    oi_change_pct:   float   # % change vs 1h ago (positive = growing)
    # Derived signal: -10..+10
    # High positive funding = overcrowded longs = bearish signal
    # High negative funding = overcrowded shorts = bullish signal
    signal_score:    float


class ExchangeClient:
    def __init__(self):
        params = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "linear",  # USDT perpetual futures
            },
        }
        if settings.BYBIT_API_KEY:
            params["apiKey"] = settings.BYBIT_API_KEY
            params["secret"] = settings.BYBIT_SECRET

        self.exchange = ccxt.bybit(params)

        if settings.PAPER_TRADING:
            self.exchange.set_sandbox_mode(True)
            logger.info("Exchange: Bybit TESTNET (paper trading)")
        else:
            logger.info("Exchange: Bybit LIVE")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str | None = None,
        limit: int | None = None,
    ) -> pd.DataFrame:
        """
        שולף נרות OHLCV.
        מחזיר DataFrame עם עמודות: open, high, low, close, volume
        """
        tf = timeframe or settings.TIMEFRAME
        lim = limit or settings.CANDLES_LIMIT

        raw = self.exchange.fetch_ohlcv(symbol, tf, limit=lim)
        df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        df = df.astype(float)

        logger.debug(f"Fetched {len(df)} candles for {symbol} [{tf}]")
        return df

    def fetch_ticker(self, symbol: str) -> dict:
        """מחיר נוכחי + נתוני 24 שעות"""
        ticker = self.exchange.fetch_ticker(symbol)
        return {
            "symbol": symbol,
            "price": ticker["last"],
            "change_24h_pct": ticker["percentage"],
            "volume_24h": ticker["quoteVolume"],
            "high_24h": ticker["high"],
            "low_24h": ticker["low"],
        }

    def fetch_futures_data(self, symbol: str) -> FuturesData:
        """
        Fetches funding rate + open interest for a perpetual futures pair.
        Both are free public endpoints — no API key required.
        """
        # Funding rate
        try:
            fr_data = self.exchange.fetch_funding_rate(symbol)
            funding_rate = float(fr_data.get("fundingRate") or 0)
            next_ts      = int(fr_data.get("nextFundingDatetime") or 0)
        except Exception as e:
            logger.warning(f"[Exchange] Funding rate fetch failed {symbol}: {e}")
            funding_rate, next_ts = 0.0, 0

        # Open interest — current and 1h ago
        try:
            oi_now = self.exchange.fetch_open_interest(symbol)
            oi_val = float(oi_now.get("openInterestValue") or oi_now.get("openInterest") or 0)
        except Exception as e:
            logger.warning(f"[Exchange] OI fetch failed {symbol}: {e}")
            oi_val = 0.0

        # OI 1h ago (for change %)
        oi_change_pct = 0.0
        try:
            history = self.exchange.fetch_open_interest_history(
                symbol, timeframe="1h", limit=2
            )
            if len(history) >= 2:
                oi_prev = float(history[-2].get("openInterestValue") or history[-2].get("openInterest") or 0)
                if oi_prev > 0:
                    oi_change_pct = round((oi_val - oi_prev) / oi_prev * 100, 3)
        except Exception as e:
            logger.debug(f"[Exchange] OI history failed {symbol}: {e}")

        # Signal score: contrarian on funding, confirmatory on OI trend
        # Funding: +0.1% = overcrowded longs → bearish → score -5
        funding_signal = max(-8.0, min(8.0, -funding_rate / 0.001 * 4))
        # OI growing with bullish price = confirming; shrinking = weakening
        oi_signal = max(-2.0, min(2.0, oi_change_pct * 0.5))
        signal_score = round(funding_signal + oi_signal, 2)

        result = FuturesData(
            symbol=symbol,
            funding_rate=funding_rate,
            funding_rate_8h=round(funding_rate * 3 * 365 * 100, 2),  # annualised %
            next_funding_ts=next_ts,
            open_interest=oi_val,
            oi_change_pct=oi_change_pct,
            signal_score=signal_score,
        )
        logger.info(
            f"[Exchange] {symbol} futures: funding={funding_rate:.4%} "
            f"OI_chg={oi_change_pct:+.2f}% signal={signal_score:+.1f}"
        )
        return result

    def fetch_balance(self) -> dict:
        """יתרת חשבון - דורש API key"""
        if not settings.BYBIT_API_KEY:
            raise ValueError("API key required for balance check")
        balance = self.exchange.fetch_balance()
        usdt = balance.get("USDT", {})
        return {
            "total": usdt.get("total", 0),
            "free": usdt.get("free", 0),
            "used": usdt.get("used", 0),
        }
