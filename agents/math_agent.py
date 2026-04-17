"""
Math Agent - ניתוח טכני-כמותי טהור.
מחשב ציון bias בין -10 (bearish) ל-+10 (bullish) על בסיס אינדיקטורים.
לא משתמש ב-Claude - מהיר, דטרמיניסטי, אובייקטיבי.
"""

from dataclasses import dataclass, field

import pandas as pd
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
    TRADE_THRESHOLD = 3.5      # ציון מינימלי לפתיחת עסקה

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

        # חישוב ציון ראשי (ללא confluence)
        result = self.analyze_df(df, symbol, tf)

        # ── Multi-timeframe confluence ────────────────────────────────────────
        confluence, conf_reasoning = self._multi_tf_confluence(symbol)
        result.reasoning.extend(conf_reasoning)

        # Blend: primary 80%, confluence 20%
        blended = round(result.bias_score * 0.80 + confluence * 0.20, 2)
        result.bias_score = blended
        result.signal     = self._to_signal(blended)
        result.component_scores["confluence_htf"] = round(confluence, 2)

        logger.info(
            f"[MathAgent] {symbol} → {result.signal} "
            f"(blended={blended:+.1f}, conf={result.confidence:.0%})"
        )
        return result

    def analyze_df(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
        params: dict | None = None,
    ) -> MathResult:
        """
        מנתח DataFrame מוכן — משמש בעיקר ל-backtest ו-optimizer.
        df צריך לכלול עמודות אינדיקטורים (calculate_all כבר רץ עליו).
        אם העמודות חסרות — מחשב אוטומטית.

        params (אופציונלי):
          - ATR_SL_MULTIPLIER: float
          - RR_RATIO: float
          - weights: dict  (override ל-WEIGHTS)
        """
        params = params or {}

        if "ema_20" not in df.columns:
            df = calculate_all(df)

        snap = get_latest_snapshot(df)

        atr_mul  = params.get("ATR_SL_MULTIPLIER", self.ATR_SL_MULTIPLIER)
        rr_ratio = params.get("RR_RATIO", self.RR_RATIO)
        weights  = params.get("weights", None)

        scores = {
            "trend":         self._score_trend(snap),
            "momentum_rsi":  self._score_rsi(snap),
            "momentum_macd": self._score_macd(snap),
            "volatility_bb": self._score_bollinger(snap),
            "volume_obv":    self._score_obv(snap),
        }

        # אינדיקטורים אופציונליים — רק אם זמינים
        fib_score = self._score_fibonacci(snap)
        if snap.get("fib_high") is not None:
            scores["fibonacci"] = fib_score

        vwap_score = self._score_vwap(snap)
        if snap.get("vwap") is not None:
            scores["vwap"] = vwap_score

        reasoning  = self._build_reasoning(snap, scores)
        bias_score = self._aggregate(scores, weights)
        signal     = self._to_signal(bias_score)
        confidence = self._calc_confidence(scores)
        sl_pct, tp_pct = self._calc_sl_tp(snap, atr_mul, rr_ratio)

        return MathResult(
            symbol=symbol,
            timeframe=timeframe,
            bias_score=round(bias_score, 2),
            signal=signal,
            confidence=round(confidence, 2),
            component_scores={k: round(v, 2) for k, v in scores.items()},
            reasoning=reasoning,
            sl_distance_pct=round(sl_pct, 4),
            tp_distance_pct=round(tp_pct, 4),
            raw=snap,
        )

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

    def _score_fibonacci(self, s: dict) -> float:
        """
        Fibonacci Retracement score: -1.5 עד +1.5
        בודק אם המחיר נמצא ליד רמת Fibonacci מרכזית.
        רמות 38.2% ו-61.8% הן החשובות ביותר.
        """
        price    = s.get("price")
        fib_high = s.get("fib_high")
        fib_low  = s.get("fib_low")
        fib_mid  = s.get("fib_050")

        if None in (price, fib_high, fib_low, fib_mid):
            return 0.0

        diff = fib_high - fib_low
        if diff <= 0:
            return 0.0

        # רמות Fibonacci ומשקלי החשיבות שלהן
        levels = [
            (s.get("fib_0236"), 0.5),
            (s.get("fib_0382"), 1.5),
            (fib_mid,           1.0),
            (s.get("fib_0618"), 1.5),
            (s.get("fib_0786"), 0.5),
        ]

        best = 0.0
        for level_price, weight in levels:
            if level_price is None:
                continue
            proximity = abs(price - level_price) / diff
            if proximity > 0.015:   # יותר מ-1.5% מהטווח — לא ברמה
                continue
            # כיוון: מעל fib_050 → bullish context (רמות הן support)
            #        מתחת fib_050 → bearish context (רמות הן resistance)
            direction = 1.0 if price >= fib_mid else -1.0
            score = direction * weight
            if abs(score) > abs(best):
                best = score

        return best

    def _score_vwap(self, s: dict) -> float:
        """
        VWAP score: -1.0 עד +1.0
        מחיר מעל VWAP → bias מוסדי bullish
        מחיר מתחת VWAP → bias מוסדי bearish
        """
        price = s.get("price")
        vwap  = s.get("vwap")

        if None in (price, vwap) or vwap == 0:
            return 0.0

        pct_diff = (price - vwap) / vwap

        if pct_diff >= 0.005:    # >0.5% מעל VWAP
            return 1.0
        if pct_diff >= 0.001:    # 0.1–0.5% מעל
            return 0.5
        if pct_diff <= -0.005:   # >0.5% מתחת VWAP
            return -1.0
        if pct_diff <= -0.001:   # 0.1–0.5% מתחת
            return -0.5
        return 0.0  # צמוד ל-VWAP

    # ── Aggregation ────────────────────────────────────────────────────────────

    WEIGHTS = {
        "trend":         0.30,
        "momentum_rsi":  0.18,
        "momentum_macd": 0.22,
        "volatility_bb": 0.08,
        "volume_obv":    0.08,
        "fibonacci":     0.08,   # חדש
        "vwap":          0.06,   # חדש
    }

    MAX_RAW = {  # ציון מקסימלי אפשרי לכל קומפוננטה
        "trend":         3.0,
        "momentum_rsi":  2.0,
        "momentum_macd": 2.0,
        "volatility_bb": 1.0,
        "volume_obv":    1.0,
        "fibonacci":     1.5,   # חדש
        "vwap":          1.0,   # חדש
    }

    def _aggregate(self, scores: dict, weights: dict | None = None) -> float:
        """
        ממיר את הציונים למספר אחד בין -10 ל-+10.
        weights: אם None — משתמש ב-WEIGHTS הסטנדרטי.
                 אם מועבר — מאפשר override ל-optimizer.
        """
        w = weights or self.WEIGHTS
        total = 0.0
        weight_sum = 0.0
        for key, weight in w.items():
            if key not in scores:
                continue
            max_r = self.MAX_RAW.get(key, 1.0)
            normalized = scores[key] / max_r   # -1 עד +1
            total += normalized * weight
            weight_sum += weight
        # נרמול אם לא כל הקומפוננטות קיימות (למשל VWAP לא זמין)
        if weight_sum > 0 and weight_sum < 1.0:
            total = total / weight_sum
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

    def _calc_sl_tp(
        self,
        s: dict,
        atr_mul: float | None = None,
        rr: float | None = None,
    ) -> tuple[float, float]:
        """
        SL = atr_mul × ATR כ-% מהמחיר
        TP = SL × rr
        """
        atr_multiplier = atr_mul if atr_mul is not None else self.ATR_SL_MULTIPLIER
        rr_ratio       = rr      if rr      is not None else self.RR_RATIO

        atr   = s["atr"]
        price = s["price"]

        if atr is None or price is None or price == 0:
            return 0.01, 0.01 * rr_ratio

        sl_pct = (atr * atr_multiplier) / price
        tp_pct = sl_pct * rr_ratio
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
