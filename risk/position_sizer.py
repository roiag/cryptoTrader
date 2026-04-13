"""
Position Sizer - מחשב כמה לקנות/למכור.

שיטה: Fixed Fractional
  risk_amount    = balance * MAX_RISK_PER_TRADE   (למשל 1% מ-$1000 = $10)
  sl_distance_$  = entry_price * sl_pct            (למשל 1.5% מ-$60,000 = $900 לBTC)
  quantity       = risk_amount / sl_distance_$     (למשל $10 / $900 = 0.0111 BTC)
  notional       = quantity * entry_price          (למשל 0.0111 * 60,000 = $666)
"""

from dataclasses import dataclass
from loguru import logger

from config.settings import settings

# גודל פוזיציה מינימלי לכל pair
MIN_NOTIONAL: dict[str, float] = {
    "BTC/USDT": 10.0,
    "ETH/USDT": 10.0,
    "default":  5.0,
}

# גודל פוזיציה מקסימלי כ-% מהתיק (ללא קשר לחישוב)
MAX_POSITION_PCT = 0.20   # לא יותר מ-20% מהתיק בפוזיציה אחת


@dataclass
class SizeResult:
    symbol: str
    entry_price: float
    quantity: float         # כמות המטבע (0.011 BTC)
    notional: float         # ערך בדולר ($666)
    risk_amount: float      # כמה דולר בסיכון ($10)
    sl_price: float         # מחיר stop-loss
    tp_price: float         # מחיר take-profit
    side: str               # "buy" / "sell"
    is_valid: bool
    rejection_reason: str = ""


def calculate(
    symbol: str,
    side: str,               # "buy" או "sell"
    entry_price: float,
    sl_pct: float,           # מרחק SL כ-% (0.015 = 1.5%)
    tp_pct: float,           # מרחק TP כ-% (0.030 = 3.0%)
    balance: float,          # יתרת חשבון ב-USDT
) -> SizeResult:
    """
    מחשב גודל פוזיציה לפי Fixed Fractional.
    """
    risk_amount = balance * settings.MAX_RISK_PER_TRADE

    if sl_pct <= 0:
        return SizeResult(
            symbol=symbol, entry_price=entry_price,
            quantity=0, notional=0, risk_amount=0,
            sl_price=0, tp_price=0, side=side,
            is_valid=False, rejection_reason="SL distance is zero",
        )

    sl_distance_dollar = entry_price * sl_pct
    quantity = risk_amount / sl_distance_dollar
    notional = quantity * entry_price

    # בדיקת מינימום
    min_notional = MIN_NOTIONAL.get(symbol, MIN_NOTIONAL["default"])
    if notional < min_notional:
        return SizeResult(
            symbol=symbol, entry_price=entry_price,
            quantity=0, notional=0, risk_amount=risk_amount,
            sl_price=0, tp_price=0, side=side,
            is_valid=False,
            rejection_reason=f"Notional ${notional:.2f} below minimum ${min_notional}",
        )

    # בדיקת מקסימום
    max_notional = balance * MAX_POSITION_PCT
    if notional > max_notional:
        notional  = max_notional
        quantity  = notional / entry_price
        logger.debug(f"Position capped at max notional ${max_notional:.2f}")

    # חישוב SL/TP
    if side == "buy":
        sl_price = round(entry_price * (1 - sl_pct), 2)
        tp_price = round(entry_price * (1 + tp_pct), 2)
    else:
        sl_price = round(entry_price * (1 + sl_pct), 2)
        tp_price = round(entry_price * (1 - tp_pct), 2)

    # עיגול כמות לדיוק סביר
    quantity = round(quantity, 4)

    logger.debug(
        f"Position size: {quantity} {symbol.split('/')[0]} | "
        f"notional=${notional:.2f} | risk=${risk_amount:.2f} | "
        f"SL={sl_price} | TP={tp_price}"
    )

    return SizeResult(
        symbol=symbol,
        entry_price=entry_price,
        quantity=quantity,
        notional=round(notional, 2),
        risk_amount=round(risk_amount, 2),
        sl_price=sl_price,
        tp_price=tp_price,
        side=side,
        is_valid=True,
    )
