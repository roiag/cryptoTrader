"""
LocalVisionAgent — גרסה מקומית של VisionAgent שמשתמשת ב-Ollama במקום Claude API.

יתרון: חינם לחלוטין — אפשר להריץ על אלפי תמונות.
חיסרון: איכות נמוכה יותר מ-Claude.

מודלים מומלצים (מהטוב לפחות טוב):
  - llama3.2-vision:11b  (דורש ~8GB VRAM)
  - qwen2-vl:7b          (דורש ~6GB VRAM, מצוין בניתוח ויזואלי)
  - llava:13b            (דורש ~10GB VRAM)
  - moondream            (קטן ומהיר, איכות בסיסית)

התקנה:
  ollama pull llama3.2-vision
  (Ollama צריך לרוץ: ollama serve)

ממשק זהה ל-VisionAgent — מחזיר VisionResult.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass, field

import requests
from loguru import logger

from agents.vision_agent import VisionResult   # משתמשים באותו dataclass

# ── הגדרות ────────────────────────────────────────────────────────────────────
OLLAMA_URL     = "http://localhost:11434/api/chat"
DEFAULT_MODEL  = "llama3.2-vision"
TIMEOUT_SEC    = 120    # מודלים מקומיים יכולים להיות איטיים

# ── Prompt (זהה ל-vision_agent, רק עם הוראה חזקה יותר ל-JSON) ────────────────
SYSTEM_PROMPT = """You are an expert cryptocurrency technical analyst.
Analyze trading chart images and respond ONLY with valid JSON — no markdown, no extra text, no explanation.
Your entire response must be a single JSON object."""

USER_PROMPT_TEMPLATE = """Analyze this {symbol} cryptocurrency chart ({timeframe} timeframe).

Return ONLY this JSON object (nothing else — no text before or after):
{{
  "trend": "uptrend|downtrend|sideways",
  "trend_strength": "weak|moderate|strong",
  "patterns": ["pattern1"],
  "support_levels": [0.0],
  "resistance_levels": [0.0],
  "candle_signals": ["signal1"],
  "ema_analysis": "brief EMA description",
  "volume_analysis": "brief volume description or null",
  "bias_score": 0.0,
  "confidence": 0.0,
  "key_observation": "most important observation"
}}

Rules:
- bias_score: float -10.0 (strongly bearish) to +10.0 (strongly bullish)
- confidence: float 0.0 to 1.0 (how clear the chart is)
- lists can be empty []
- respond with JSON ONLY"""


class LocalVisionAgent:
    """
    Vision agent מקומי שמשתמש ב-Ollama.
    ממשק זהה ל-VisionAgent.
    """

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self.model = model
        self._check_ollama()

    def analyze(self, screenshot: bytes, symbol: str, timeframe: str) -> VisionResult:
        """
        שולח screenshot ל-Ollama ומחזיר VisionResult.
        """
        logger.info(
            f"[LocalVision] Analyzing {symbol} [{timeframe}] "
            f"with {self.model} (image: {len(screenshot)/1024:.1f} KB)"
        )

        b64_image = base64.standard_b64encode(screenshot).decode("utf-8")
        prompt    = USER_PROMPT_TEMPLATE.format(symbol=symbol, timeframe=timeframe)

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                    "images": [b64_image],
                },
            ],
            "stream": False,
            "options": {
                "temperature": 0.1,   # נמוך = עקבי יותר
                "num_predict": 512,
            },
        }

        try:
            resp = requests.post(OLLAMA_URL, json=payload, timeout=TIMEOUT_SEC)
            resp.raise_for_status()
        except requests.exceptions.ConnectionError:
            raise RuntimeError(
                "Cannot connect to Ollama. Make sure it's running: 'ollama serve'"
            )
        except requests.exceptions.Timeout:
            raise RuntimeError(
                f"Ollama timed out after {TIMEOUT_SEC}s. "
                "Try a smaller model (e.g., moondream) or increase TIMEOUT_SEC."
            )

        raw = resp.json()["message"]["content"]
        logger.debug(f"[LocalVision] Raw response: {raw[:300]}")

        data   = self._parse_json(raw)
        result = self._build_result(symbol, timeframe, data, raw)

        logger.info(
            f"[LocalVision] {symbol} → {result.trend.upper()} "
            f"(score={result.bias_score:+.1f}, conf={result.confidence:.0%})"
        )
        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _check_ollama(self) -> None:
        """בודק שOllama רץ ושהמודל קיים."""
        try:
            resp = requests.get("http://localhost:11434/api/tags", timeout=5)
            models = [m["name"] for m in resp.json().get("models", [])]
            available = [m.split(":")[0] for m in models]
            requested = self.model.split(":")[0]
            if requested not in available:
                logger.warning(
                    f"[LocalVision] Model '{self.model}' not found in Ollama. "
                    f"Available: {models}. "
                    f"Pull it with: ollama pull {self.model}"
                )
        except requests.exceptions.ConnectionError:
            logger.warning(
                "[LocalVision] Ollama is not running. Start it with: ollama serve"
            )

    def _parse_json(self, text: str) -> dict:
        """מחלץ JSON מהתשובה — מודלים מקומיים לא תמיד עוטים נקי."""
        text = text.strip()

        # ניסיון ישיר
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # הסר code block markers
        text_clean = re.sub(r"```(?:json)?", "", text).strip().rstrip("`").strip()
        try:
            return json.loads(text_clean)
        except json.JSONDecodeError:
            pass

        # חלץ את ה-{} הראשון שמוצא
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass

        logger.warning(f"[LocalVision] Could not parse JSON, using fallback. Raw: {text[:200]}")
        return self._fallback_result()

    def _build_result(
        self, symbol: str, timeframe: str, data: dict, raw: str
    ) -> VisionResult:
        def safe_float_list(key: str) -> list[float]:
            try:
                return [float(x) for x in data.get(key, [])]
            except (TypeError, ValueError):
                return []

        return VisionResult(
            symbol=symbol,
            timeframe=timeframe,
            trend=data.get("trend", "sideways"),
            trend_strength=data.get("trend_strength", "weak"),
            patterns=data.get("patterns", []) or [],
            support_levels=safe_float_list("support_levels"),
            resistance_levels=safe_float_list("resistance_levels"),
            candle_signals=data.get("candle_signals", []) or [],
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
            "key_observation": "Analysis failed",
        }
