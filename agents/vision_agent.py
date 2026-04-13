"""
Vision Agent - ניתוח ויזואלי של גרף קריפטו עם Claude Vision.

זרימה:
  1. מקבל screenshot כ-bytes
  2. שולח ל-Claude claude-opus-4-6 עם prompt מובנה
  3. מחלץ JSON מהתשובה
  4. מחזיר VisionResult
"""

import base64
import json
import re
from dataclasses import dataclass, field

import anthropic
from loguru import logger

from config.settings import settings

# ── Claude Model ───────────────────────────────────────────────────────────────
MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024   # תשובה קצרה = JSON בלבד

# ── Prompt ─────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert cryptocurrency technical analyst.
Your task is to analyze trading chart screenshots and provide structured analysis.
Always respond with valid JSON only - no markdown, no extra text."""

USER_PROMPT_TEMPLATE = """Analyze this {symbol} cryptocurrency chart ({timeframe} timeframe).

Provide a detailed technical analysis covering:
1. Overall trend (uptrend / downtrend / sideways)
2. Chart patterns you can identify (e.g., head & shoulders, double top/bottom, flag, wedge, triangle, cup & handle)
3. Clear support price levels (specific numbers)
4. Clear resistance price levels (specific numbers)
5. Notable candlestick signals (e.g., doji, engulfing, hammer, shooting star, morning/evening star)
6. EMA / moving average positioning (if visible on chart)
7. Volume analysis (if volume bars are visible)

Return ONLY this JSON structure, with no other text:
{{
  "trend": "uptrend|downtrend|sideways",
  "trend_strength": "weak|moderate|strong",
  "patterns": ["pattern1", "pattern2"],
  "support_levels": [12345.0],
  "resistance_levels": [12345.0],
  "candle_signals": ["signal1"],
  "ema_analysis": "brief description of EMA positioning",
  "volume_analysis": "brief description or null",
  "bias_score": 0.0,
  "confidence": 0.0,
  "key_observation": "single most important thing you see"
}}

bias_score: float from -10.0 (strongly bearish) to +10.0 (strongly bullish)
confidence: float from 0.0 to 1.0 (how clear/readable the chart is)"""


# ── Result Dataclass ───────────────────────────────────────────────────────────
@dataclass
class VisionResult:
    symbol: str
    timeframe: str
    trend: str                          # uptrend / downtrend / sideways
    trend_strength: str                 # weak / moderate / strong
    patterns: list[str]
    support_levels: list[float]
    resistance_levels: list[float]
    candle_signals: list[str]
    ema_analysis: str
    volume_analysis: str | None
    bias_score: float                   # -10 עד +10
    confidence: float                   # 0.0 עד 1.0
    key_observation: str
    raw_response: str = field(default="", repr=False)


# ── Agent ──────────────────────────────────────────────────────────────────────
class VisionAgent:

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def analyze(self, screenshot: bytes, symbol: str, timeframe: str) -> VisionResult:
        """
        שולח screenshot ל-Claude ומחזיר ניתוח מובנה.
        """
        logger.info(f"[VisionAgent] Analyzing {symbol} [{timeframe}] "
                    f"(image: {len(screenshot)/1024:.1f} KB)")

        b64_image = base64.standard_b64encode(screenshot).decode("utf-8")
        prompt    = USER_PROMPT_TEMPLATE.format(symbol=symbol, timeframe=timeframe)

        message = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": b64_image,
                            },
                        },
                        {
                            "type": "text",
                            "text": prompt,
                        },
                    ],
                }
            ],
        )

        raw = message.content[0].text
        logger.debug(f"[VisionAgent] Raw response: {raw[:200]}...")

        data = self._parse_json(raw)
        result = self._build_result(symbol, timeframe, data, raw)

        logger.info(
            f"[VisionAgent] {symbol} → {result.trend.upper()} "
            f"(score={result.bias_score:+.1f}, conf={result.confidence:.0%})"
        )
        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _parse_json(self, text: str) -> dict:
        """
        מחלץ JSON מהתשובה.
        Claude לפעמים עוטף ב-```json ... ``` - מטפלים בזה.
        """
        # נסה parse ישיר
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # נסה לחלץ מתוך code block
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass

        # נסה לחלץ כל {} block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"[VisionAgent] Could not parse JSON, using fallback. Raw: {text[:300]}")
        return self._fallback_result()

    def _build_result(
        self, symbol: str, timeframe: str, data: dict, raw: str
    ) -> VisionResult:
        return VisionResult(
            symbol=symbol,
            timeframe=timeframe,
            trend=data.get("trend", "sideways"),
            trend_strength=data.get("trend_strength", "weak"),
            patterns=data.get("patterns", []),
            support_levels=[float(x) for x in data.get("support_levels", [])],
            resistance_levels=[float(x) for x in data.get("resistance_levels", [])],
            candle_signals=data.get("candle_signals", []),
            ema_analysis=data.get("ema_analysis", ""),
            volume_analysis=data.get("volume_analysis"),
            bias_score=float(data.get("bias_score", 0.0)),
            confidence=float(data.get("confidence", 0.5)),
            key_observation=data.get("key_observation", ""),
            raw_response=raw,
        )

    @staticmethod
    def _fallback_result() -> dict:
        return {
            "trend": "sideways",
            "trend_strength": "weak",
            "patterns": [],
            "support_levels": [],
            "resistance_levels": [],
            "candle_signals": [],
            "ema_analysis": "Could not analyze",
            "volume_analysis": None,
            "bias_score": 0.0,
            "confidence": 0.0,
            "key_observation": "Analysis failed - chart could not be parsed",
        }
