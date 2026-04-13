"""
Math Agent - ניתוח טכני-כמותי טהור.
מחשב ציון bias בין -10 (bearish) ל-+10 (bullish) על בסיס אינדיקטורים.
לא משתמש ב-Claude - מהיר, דטרמיניסטי, אובייקטיבי.
"""

from dataclasses import dataclass, field
from loguru import logger

from data.exchange import ExchangeClient
from data.indicators import calculate_all, get_latest_snapshot


@dataclass
class MathResult:
    symbol: str
    timeframe: str
    bias_score: float          # -10 עד +10
    signal: str                # BULLISH / BEARISH / NEUTRAL
    confidence: float          # 0.0 עד 1.0
    component_scores: dict     # ציון לכל קבוצת אינדיקטורים
    reasoning: list[str]       # הסברים בשפה פשוטה
    sl_distance_pct: float     # מרחק Stop-Loss כ-% מהמחיר (מבוסס ATR)
    tp_distance_pct: float     # מרחק Take-Profit (x2 מה-SL)
    raw: dict = field(default_factory=dict)  # snapshot גולמי לשמירה ב-DB


class MathAgent:
    """
    מריץ ניתוח טכני מלא על pair נתון.
    """

    ATR_SL_MULTIPLIER = 1.5   # SL = 1.5x ATR
    RR_RATIO = 2.0             # TP = RR * SL

    def __init__(self):
        self.exchange = ExchangeClient()

    # Higher timeframes to check for confluence (fetched in parallel context)
    CONFLUENCE_TFS: list[str] = ["1h", "4h"]

    def analyze(self, symbol: str, timeframe: str | None = None) -> MathResult:
        """Main entry point — scores the primary TF and adds multi-TF confluence."""
        tf = timeframe or "15m"
        logger.info(f"[MathAgent] Analyzing {symbol} [{tf}]")

        df   = self.exchange.fetch_ohlcv(symbol, tf)
        df   = calculate_all(df)
        snap = get_latest_snapshot(df)

        scores = {
            "trend":         self._score_trend(snap),
            "momentum_rsi":  self._score_rsi(snap),
            "momentum_macd": self._score_macd(snap),
            "volatility_bb": self._score_bollinger(snap),
            "volume_obv":    self._score_obv(snap),
        }

        reasoning  = self._build_reasoning(snap, scores)
        bias_score = self._aggregate(scores)

        # ── Multi-timeframe confluence ────────────────────────────────────────
        confluence, conf_reasoning = self._multi_tf_confluence(symbol)
        reasoning.extend(conf_reasoning)

        # Blend: primary 80%, confluence 20%
        blended_score = round(bias_score * 0.80 + confluence * 0.20, 2)

        signal     = self._to_signal(blended_score)
        confidence = self._calc_confidence(scores)
        sl_pct, tp_pct = self._calc_sl_tp(snap)

        result = MathResult(
            symbol=symbol,
            timeframe=tf,
            bias_score=blended_score,
            signal=signal,
            confidence=round(confidence, 2),
            component_scores={
                **{k: round(v, 2) for k, v in scores.items()},
                "confluence_htf": round(confluence, 2),
            },
            reasoning=reasoning,
            sl_distance_pct=round(sl_pct, 4),
            tp_distance_pct=round(tp_pct, 4),
            raw=snap,
        )

        logger.info(
            f"[MathAgent] {symbol} → {signal} "
            f"(primary={bias_score:+.1f} confluence={confluence:+.1f} "
            f"blended={blended_score:+.1f}, conf={confidence:.0%})"
        )
        return result

    def _multi_tf_confluence(self, symbol: str) -> tuple[float, list[str]]:
        """
        Fetches 1H and 4H, computes a trend score for each, returns average.
        Only uses EMA trend + RSI (fast, no full indicator suite needed).
        """
        tf_scores: list[float] = []
        reasoning: list[str]   = []

        for htf in self.CONFLUENCE_TFS:
            try:
                df   = self.exchange.fetch_ohlcv(symbol, htf, limit=60)
                df   = calculate_all(df)
                snap = get_latest_snapshot(df)
                score = self._score_trend(snap) / 3.0 * 10  # normalise to ±10
                tf_scores.append(score)
                direction = "bullish" if score > 2 else ("bearish" if score < -2 else "neutral")
                reasoning.append(f"{htf.upper()} trend: {direction} ({score:+.1f})")
            except Exception as e:
                logger.warning(f"[MathAgent] HTF {htf} fetch failed: {e}")

        avg = round(sum(tf_scores) / len(tf_scores), 2) if tf_scores else 0.0
        return avg, reasoning

    # ── Scoring components ─────────────────────────────────────────────────────

    def _score_trend(self, s: dict) -> float:
        """
        EMA alignment score: -3 עד +3
        +3 = price > EMA20 > EMA50 > EMA200 (perfect uptrend)
        -3 = price < EMA20 < EMA50 < EMA200 (perfect downtrend)
        """
        price = s["price"]
        e20, e50, e200 = s["ema_20"], s["ema_50"], s["ema_200"]

        if None in (price, e20, e50, e200):
            return 0.0

        score = 0.0
        if price > e20:
            score += 1.0
        else:
            score -= 1.0

        if e20 > e50:
            score += 1.0
        else:
            score -= 1.0

        if e50 > e200:
            score += 1.0
        else:
            score -= 1.0

        return score  # -3 עד +3

    def _score_rsi(self, s: dict) -> float:
        """
        RSI score: -2 עד +2
        לוגיקה מונוטונית: bullish מעל 50, bearish מתחת ל-50.
        קצוות (overbought/oversold) מקבלים signal הפוך (mean-reversion).
        """
        rsi = s["rsi"]
        if rsi is None:
            return 0.0

        if rsi >= 80:
            return -2.0   # overbought חזק - סיגנל הפוך
        if rsi >= 70:
            return -1.0   # overbought
        if rsi >= 60:
            return +0.5   # bullish (לא overbought עדיין)
        if rsi >= 50:
            return +1.0   # bullish momentum - מעל midpoint
        if rsi >= 40:
            return -0.5   # bearish momentum
        if rsi >= 30:
            return -1.0   # bearish
        if rsi >= 20:
            return +1.5   # oversold - פוטנציאל bounce
        return +2.0       # oversold חזק - סיגנל הפוך

    def _score_macd(self, s: dict) -> float:
        """
        MACD score: -2 עד +2
        בודק: מיקום line vs signal + כיוון histogram
        """
        line = s["macd_line"]
        signal = s["macd_signal"]
        hist = s["macd_hist"]
        hist_prev = s["macd_hist_prev"]

        if None in (line, signal, hist):
            return 0.0

        score = 0.0

        # MACD line מעל signal line
        if line > signal:
            score += 1.0
        else:
            score -= 1.0

        # histogram גדל (momentum מתחזק)
        if hist_prev is not None:
            if hist > hist_prev:
                score += 1.0
            else:
                score -= 1.0

        return max(-2.0, min(2.0, score))

    def _score_bollinger(self, s: dict) -> float:
        """
        Bollinger Bands score: -1 עד +1
        מיקום המחיר בתוך הרצועה
        """
        price = s["price"]
        upper = s["bb_upper"]
        lower = s["bb_lower"]
        mid = s["bb_mid"]

        if None in (price, upper, lower, mid):
            return 0.0

        band_range = upper - lower
        if band_range == 0:
            return 0.0

        # position בין 0 (lower) ל-1 (upper)
        position = (price - lower) / band_range

        if position >= 0.85:
            return -1.0   # מחיר קרוב לרצועה עליונה - overbought
        if position >= 0.65:
            return -0.5
        if position <= 0.15:
            return 1.0    # מחיר קרוב לרצועה תחתונה - oversold
        if position <= 0.35:
            return 0.5
        return 0.0        # middle band - neutral

    def _score_obv(self, s: dict) -> float:
        """
        OBV score: -1 עד +1
        האם volume מאשר את המגמה?
        """
        obv = s["obv"]
        obv_prev = s["obv_prev_5"]

        if None in (obv, obv_prev):
            return 0.0

        if obv > obv_prev * 1.02:
            return 1.0    # volume גדל - bullish confirmation
        if obv < obv_prev * 0.98:
            return -1.0   # volume יורד - bearish confirmation
        return 0.0

    # ── Aggregation ────────────────────────────────────────────────────────────

    WEIGHTS = {
        "trend": 0.35,
        "momentum_rsi": 0.20,
        "momentum_macd": 0.25,
        "volatility_bb": 0.10,
        "volume_obv": 0.10,
    }

    MAX_RAW = {  # ציון מקסימלי אפשרי לכל קומפוננטה
        "trend": 3.0,
        "momentum_rsi": 2.0,
        "momentum_macd": 2.0,
        "volatility_bb": 1.0,
        "volume_obv": 1.0,
    }

    def _aggregate(self, scores: dict) -> float:
        """ממיר את הציונים למספר אחד בין -10 ל-+10."""
        total = 0.0
        for key, weight in self.WEIGHTS.items():
            normalized = scores[key] / self.MAX_RAW[key]  # -1 עד +1
            total += normalized * weight
        return round(total * 10, 2)  # scale ל-±10

    def _to_signal(self, score: float) -> str:
        if score >= 4.0:
            return "BULLISH"
        if score <= -4.0:
            return "BEARISH"
        return "NEUTRAL"

    def _calc_confidence(self, scores: dict) -> float:
        """
        Confidence = כמה הסוכנים מסכימים זה עם זה.
        אם כולם באותו כיוון → confidence גבוה.
        אם מחצית bullish ומחצית bearish → confidence נמוך.
        """
        vals = list(scores.values())
        positives = sum(1 for v in vals if v > 0)
        negatives = sum(1 for v in vals if v < 0)
        agreement = abs(positives - negatives) / len(vals)
        return round(0.5 + agreement * 0.5, 2)

    def _calc_sl_tp(self, s: dict) -> tuple[float, float]:
        """
        SL = 1.5x ATR כ-% מהמחיר
        TP = SL * RR_RATIO
        """
        atr = s["atr"]
        price = s["price"]

        if atr is None or price is None or price == 0:
            return 0.01, 0.02  # fallback: 1% SL, 2% TP

        sl_pct = (atr * self.ATR_SL_MULTIPLIER) / price
        tp_pct = sl_pct * self.RR_RATIO
        return sl_pct, tp_pct

    def _build_reasoning(self, s: dict, scores: dict) -> list[str]:
        """בונה רשימת הסברים קריאה."""
        lines = []

        price = s["price"]
        e20, e50 = s["ema_20"], s["ema_50"]
        rsi = s["rsi"]
        macd_hist = s["macd_hist"]
        macd_hist_prev = s["macd_hist_prev"]

        if price and e20 and e50:
            if price > e20 > e50:
                lines.append(f"Price ({price}) above EMA20 ({e20}) and EMA50 ({e50}) - uptrend structure")
            elif price < e20 < e50:
                lines.append(f"Price ({price}) below EMA20 ({e20}) and EMA50 ({e50}) - downtrend structure")
            else:
                lines.append("EMAs mixed - no clear trend direction")

        if rsi:
            if rsi > 70:
                lines.append(f"RSI {rsi:.1f} - overbought territory, caution for longs")
            elif rsi < 30:
                lines.append(f"RSI {rsi:.1f} - oversold territory, potential bounce")
            else:
                lines.append(f"RSI {rsi:.1f} - neutral zone")

        if macd_hist is not None and macd_hist_prev is not None:
            direction = "growing" if macd_hist > macd_hist_prev else "shrinking"
            lines.append(f"MACD histogram {direction} ({macd_hist:.4f})")

        return lines
