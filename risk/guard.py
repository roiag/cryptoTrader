"""
Risk Guard - שומר הסף לפני כל פקודה.

בדיקות לפי סדר:
  1. Circuit Breaker   - האם הגענו להפסד יומי מקסימלי?
  2. Open Positions    - האם יש כבר יותר מדי פוזיציות פתוחות?
  3. Duplicate         - האם יש כבר פוזיציה פתוחה על אותו symbol?
  4. Confidence        - האם שני הסוכנים הראשיים (Math + Vision) בטוחים מספיק?
  5. Conflict          - האם Math ו-Vision מנוגדים לחלוטין?
  6. Min R:R           - האם יחס סיכון/תשואה מינימלי מתקיים?

כל בדיקה מחזירה GuardResult עם approved / rejection_reason.
"""

from dataclasses import dataclass

from loguru import logger

from config.settings import settings
from notifications import telegram

# ── סף קונפידנס מינימלי לביצוע עסקה ─────────────────────────────────────────
MIN_CONFIDENCE = 0.55

# ── יחס R:R מינימלי ───────────────────────────────────────────────────────────
MIN_RR = 1.8   # TP צריך להיות לפחות 1.8x גדול מה-SL

# ── סף Conflict - אם שני הסוכנים האלה מנוגדים → HOLD ────────────────────────
CONFLICT_THRESHOLD = 4.0   # bias_score מעל זה = חוות דעת ברורה


@dataclass
class GuardResult:
    approved: bool
    rejection_reason: str = ""

    def __bool__(self) -> bool:
        return self.approved


class RiskGuard:
    """
    מריץ את כל הבדיקות לפי סדר.
    עוצר בבדיקה הראשונה שנכשלת.
    """

    def check(
        self,
        symbol: str,
        side: str,
        math_score: float,
        vision_score: float,
        math_confidence: float,
        vision_confidence: float,
        sl_pct: float,
        tp_pct: float,
        open_positions: list[str],   # רשימת symbols עם פוזיציות פתוחות
        daily_pnl_pct: float,        # P&L יומי כ-% מהתיק (שלילי = הפסד)
    ) -> GuardResult:

        checks = [
            self._check_circuit_breaker(daily_pnl_pct),
            self._check_max_positions(open_positions),
            self._check_duplicate(symbol, open_positions),
            self._check_confidence(math_confidence, vision_confidence),
            self._check_conflict(math_score, vision_score),
            self._check_rr(sl_pct, tp_pct),
        ]

        for result in checks:
            if not result.approved:
                logger.warning(f"[RiskGuard] BLOCKED {symbol} {side}: {result.rejection_reason}")
                return result

        logger.info(f"[RiskGuard] APPROVED {symbol} {side}")
        return GuardResult(approved=True)

    # ── בדיקות פרטניות ─────────────────────────────────────────────────────────

    def _check_circuit_breaker(self, daily_pnl_pct: float) -> GuardResult:
        """עצור אם הגענו להפסד יומי מקסימלי."""
        if daily_pnl_pct <= -settings.MAX_DAILY_LOSS:
            telegram.notify_circuit_breaker(daily_pnl_pct)
            return GuardResult(
                approved=False,
                rejection_reason=(
                    f"Daily circuit breaker triggered: "
                    f"{daily_pnl_pct:.2%} loss (limit: {-settings.MAX_DAILY_LOSS:.2%})"
                ),
            )
        return GuardResult(approved=True)

    def _check_max_positions(self, open_positions: list[str]) -> GuardResult:
        """לא לפתוח יותר פוזיציות מהמקסימום המוגדר."""
        count = len(open_positions)
        if count >= settings.MAX_OPEN_POSITIONS:
            return GuardResult(
                approved=False,
                rejection_reason=(
                    f"Max open positions reached: {count}/{settings.MAX_OPEN_POSITIONS}"
                ),
            )
        return GuardResult(approved=True)

    def _check_duplicate(self, symbol: str, open_positions: list[str]) -> GuardResult:
        """לא לפתוח פוזיציה נוספת על symbol שכבר פתוח."""
        if symbol in open_positions:
            return GuardResult(
                approved=False,
                rejection_reason=f"Position already open for {symbol}",
            )
        return GuardResult(approved=True)

    def _check_confidence(
        self, math_confidence: float, vision_confidence: float
    ) -> GuardResult:
        """שני הסוכנים הראשיים חייבים להיות בטוחים מספיק."""
        avg_confidence = (math_confidence + vision_confidence) / 2
        if avg_confidence < MIN_CONFIDENCE:
            return GuardResult(
                approved=False,
                rejection_reason=(
                    f"Low confidence: math={math_confidence:.0%}, "
                    f"vision={vision_confidence:.0%}, "
                    f"avg={avg_confidence:.0%} < {MIN_CONFIDENCE:.0%}"
                ),
            )
        return GuardResult(approved=True)

    def _check_conflict(self, math_score: float, vision_score: float) -> GuardResult:
        """
        אם Math ו-Vision מנוגדים לחלוטין - אין הסכמה, אל תסחר.
        לדוגמה: Math=+7 (bullish חזק) ו-Vision=-6 (bearish חזק) = conflict.
        """
        math_strong   = abs(math_score)   >= CONFLICT_THRESHOLD
        vision_strong = abs(vision_score) >= CONFLICT_THRESHOLD
        opposite_dirs = (math_score > 0) != (vision_score > 0)

        if math_strong and vision_strong and opposite_dirs:
            return GuardResult(
                approved=False,
                rejection_reason=(
                    f"Agent conflict: Math={math_score:+.1f} vs Vision={vision_score:+.1f} - "
                    "strong disagreement, holding"
                ),
            )
        return GuardResult(approved=True)

    def _check_rr(self, sl_pct: float, tp_pct: float) -> GuardResult:
        """יחס סיכון/תשואה חייב להיות לפחות MIN_RR."""
        if sl_pct <= 0:
            return GuardResult(
                approved=False, rejection_reason="SL distance is zero"
            )
        rr = tp_pct / sl_pct
        if rr < MIN_RR:
            return GuardResult(
                approved=False,
                rejection_reason=f"R:R ratio {rr:.2f} below minimum {MIN_RR}",
            )
        return GuardResult(approved=True)
