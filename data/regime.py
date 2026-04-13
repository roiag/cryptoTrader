"""
Market Regime Detector — classifies current market conditions.

Uses ATR (volatility), BB width (squeeze/expansion), and EMA slope
to classify the market into one of four regimes, and returns
the recommended signal threshold for each.

Regimes:
  TRENDING_BULL  — clear uptrend, momentum-friendly
  TRENDING_BEAR  — clear downtrend, short-friendly
  RANGING        — sideways chop, reduce trading aggressively
  HIGH_VOL       — large swings, widen threshold to avoid whipsaws
"""

from dataclasses import dataclass
from enum import Enum

import pandas as pd
from loguru import logger


class Regime(str, Enum):
    TRENDING_BULL = "TRENDING_BULL"
    TRENDING_BEAR = "TRENDING_BEAR"
    RANGING       = "RANGING"
    HIGH_VOL      = "HIGH_VOL"


# Recommended signal thresholds per regime
REGIME_THRESHOLDS: dict[Regime, float] = {
    Regime.TRENDING_BULL: 4.0,   # easier entry — trend is your friend
    Regime.TRENDING_BEAR: 4.0,
    Regime.RANGING:       6.0,   # hard filter — most signals are noise
    Regime.HIGH_VOL:      5.5,   # wider filter — avoid whipsaws
}

# Regime descriptions for Telegram reports
REGIME_LABELS: dict[Regime, str] = {
    Regime.TRENDING_BULL: "Trending Bull 📈",
    Regime.TRENDING_BEAR: "Trending Bear 📉",
    Regime.RANGING:       "Ranging / Sideways ↔️",
    Regime.HIGH_VOL:      "High Volatility ⚡",
}


@dataclass
class RegimeResult:
    regime:            Regime
    label:             str
    threshold:         float
    atr_pct:           float    # ATR as % of price
    bb_width_pct:      float    # BB width as % of mid
    ema_slope:         float    # EMA20 slope over last 10 bars (% change)
    above_ema200:      bool     # price > EMA200


def detect(df: pd.DataFrame) -> RegimeResult:
    """
    Detects market regime from a pre-calculated OHLCV+indicators DataFrame.
    Call calculate_all(df) before passing here.

    Args:
        df: DataFrame with columns: close, atr, bb_upper, bb_lower, bb_mid,
            ema_20, ema_200

    Returns:
        RegimeResult with regime classification and recommended threshold.
    """
    if len(df) < 20:
        return _default()

    row  = df.iloc[-1]
    price = float(row.get("close") or 0)
    if price == 0:
        return _default()

    # ── ATR % ────────────────────────────────────────────────────────────────
    atr = float(row.get("atr") or 0)
    atr_pct = (atr / price * 100) if price else 0.0

    # ── BB width % ───────────────────────────────────────────────────────────
    bb_upper = float(row.get("bb_upper") or 0)
    bb_lower = float(row.get("bb_lower") or 0)
    bb_mid   = float(row.get("bb_mid")   or price)
    bb_width_pct = ((bb_upper - bb_lower) / bb_mid * 100) if bb_mid else 0.0

    # ── EMA20 slope (%change over last 10 bars) ──────────────────────────────
    ema_col = "ema_20"
    ema_slope = 0.0
    if ema_col in df.columns and len(df) >= 10:
        ema_now  = float(df[ema_col].iloc[-1]  or 0)
        ema_prev = float(df[ema_col].iloc[-10] or 0)
        if ema_prev > 0:
            ema_slope = (ema_now - ema_prev) / ema_prev * 100

    # ── Price vs EMA200 ──────────────────────────────────────────────────────
    ema200 = float(row.get("ema_200") or 0)
    above_ema200 = price > ema200 if ema200 > 0 else True

    # ── Classification logic ─────────────────────────────────────────────────
    #
    # HIGH_VOL:     ATR% > 3% (BTC ~$2400 daily range on $80k)
    # RANGING:      BB width narrow (<2%) AND EMA slope flat (<0.1%)
    # TRENDING:     EMA slope steep AND price clearly on one side of EMA200
    #
    if atr_pct > 3.0:
        regime = Regime.HIGH_VOL

    elif bb_width_pct < 2.0 and abs(ema_slope) < 0.10:
        regime = Regime.RANGING

    elif ema_slope >= 0.10 and above_ema200:
        regime = Regime.TRENDING_BULL

    elif ema_slope <= -0.10 and not above_ema200:
        regime = Regime.TRENDING_BEAR

    elif ema_slope >= 0.05:
        regime = Regime.TRENDING_BULL

    elif ema_slope <= -0.05:
        regime = Regime.TRENDING_BEAR

    else:
        regime = Regime.RANGING

    result = RegimeResult(
        regime=regime,
        label=REGIME_LABELS[regime],
        threshold=REGIME_THRESHOLDS[regime],
        atr_pct=round(atr_pct, 3),
        bb_width_pct=round(bb_width_pct, 3),
        ema_slope=round(ema_slope, 4),
        above_ema200=above_ema200,
    )

    logger.info(
        f"[Regime] {regime.value}  "
        f"ATR={atr_pct:.2f}%  BB_w={bb_width_pct:.2f}%  "
        f"slope={ema_slope:+.3f}%  threshold={result.threshold}"
    )
    return result


def _default() -> RegimeResult:
    return RegimeResult(
        regime=Regime.RANGING,
        label=REGIME_LABELS[Regime.RANGING],
        threshold=REGIME_THRESHOLDS[Regime.RANGING],
        atr_pct=0.0, bb_width_pct=0.0, ema_slope=0.0, above_ema200=True,
    )
