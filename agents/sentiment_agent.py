"""
Sentiment Agent - ניתוח חדשות וסנטימנט שוק עם Claude.

מקורות קלט:
  • כותרות חדשות (RSS / CryptoPanic)
  • Fear & Greed Index

משתמש ב-claude-haiku-4-5 - מהיר וזול לניתוח טקסט.
מחזיר SentimentResult עם ציון -10 עד +10.
"""

import json
import re
from dataclasses import dataclass, field

import anthropic
from loguru import logger

from config.settings import settings
from data.news import fetch_all, FearGreedData, Headline

# ── Model ──────────────────────────────────────────────────────────────────────
# Haiku מספיק לניתוח טקסט - 3x מהיר מ-Opus, 10x זול
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 512

# ── Prompts ────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are a cryptocurrency market sentiment analyst.
Analyze news headlines and return structured JSON only - no extra text."""

USER_PROMPT_TEMPLATE = """Analyze the market sentiment for {coin_name} ({symbol}) based on:

FEAR & GREED INDEX: {fear_greed_value}/100 ({fear_greed_label})

NEWS HEADLINES (most recent first):
{headlines_text}

Evaluate:
1. Overall sentiment of the news (positive/negative/neutral)
2. Any major catalysts (regulatory news, partnerships, hacks, ETF, adoption, macro events)
3. Urgency - is this time-sensitive breaking news or background noise?
4. How does the Fear & Greed index align with the news?

Return ONLY this JSON:
{{
  "market_sentiment": "positive|negative|neutral",
  "news_sentiment_score": 0.0,
  "catalysts": ["catalyst1"],
  "urgency": "high|medium|low",
  "fear_greed_alignment": "confirms|contradicts|neutral",
  "bias_score": 0.0,
  "confidence": 0.0,
  "summary": "2-3 sentence summary of current sentiment"
}}

news_sentiment_score: -10.0 (very bearish news) to +10.0 (very bullish news)
bias_score: combined score considering both news and fear/greed index
confidence: 0.0 to 1.0"""

COIN_NAMES = {
    "BTC/USDT": "Bitcoin",
    "ETH/USDT": "Ethereum",
    "SOL/USDT": "Solana",
    "BNB/USDT": "BNB",
}


# ── Result ─────────────────────────────────────────────────────────────────────
@dataclass
class SentimentResult:
    symbol: str
    # Fear & Greed
    fear_greed_value: int
    fear_greed_label: str
    fear_greed_score: float         # -10 עד +10
    # News
    headlines_count: int
    market_sentiment: str           # positive / negative / neutral
    news_sentiment_score: float     # -10 עד +10
    catalysts: list[str]
    urgency: str                    # high / medium / low
    fear_greed_alignment: str       # confirms / contradicts / neutral
    # Combined
    bias_score: float               # -10 עד +10
    confidence: float               # 0.0 עד 1.0
    summary: str
    raw_response: str = field(default="", repr=False)


# ── Agent ──────────────────────────────────────────────────────────────────────
class SentimentAgent:

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._cryptopanic_token = getattr(settings, "CRYPTOPANIC_TOKEN", "")

    def analyze(self, symbol: str) -> SentimentResult:
        """
        שולף חדשות + Fear & Greed, שולח ל-Claude, מחזיר ניתוח.
        """
        logger.info(f"[SentimentAgent] Analyzing {symbol}")

        fear_greed, headlines = fetch_all(symbol, self._cryptopanic_token)

        if not headlines:
            logger.warning(f"[SentimentAgent] No headlines for {symbol}, using Fear & Greed only")
            return self._fear_greed_only(symbol, fear_greed)

        result = self._analyze_with_claude(symbol, fear_greed, headlines)

        logger.info(
            f"[SentimentAgent] {symbol} → {result.market_sentiment.upper()} "
            f"(score={result.bias_score:+.1f}, F&G={result.fear_greed_value})"
        )
        return result

    # ── Private ────────────────────────────────────────────────────────────────

    def _analyze_with_claude(
        self,
        symbol: str,
        fear_greed: FearGreedData,
        headlines: list[Headline],
    ) -> SentimentResult:
        coin_name = COIN_NAMES.get(symbol, symbol)
        headlines_text = "\n".join(
            f"• [{h.source}] {h.title}" for h in headlines
        )

        prompt = USER_PROMPT_TEMPLATE.format(
            coin_name=coin_name,
            symbol=symbol,
            fear_greed_value=fear_greed.value,
            fear_greed_label=fear_greed.classification,
            headlines_text=headlines_text,
        )

        message = self._client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text
        data = self._parse_json(raw)
        return self._build_result(symbol, fear_greed, headlines, data, raw)

    def _build_result(
        self,
        symbol: str,
        fg: FearGreedData,
        headlines: list[Headline],
        data: dict,
        raw: str,
    ) -> SentimentResult:
        return SentimentResult(
            symbol=symbol,
            fear_greed_value=fg.value,
            fear_greed_label=fg.classification,
            fear_greed_score=fg.score,
            headlines_count=len(headlines),
            market_sentiment=data.get("market_sentiment", "neutral"),
            news_sentiment_score=float(data.get("news_sentiment_score", 0.0)),
            catalysts=data.get("catalysts", []),
            urgency=data.get("urgency", "low"),
            fear_greed_alignment=data.get("fear_greed_alignment", "neutral"),
            bias_score=float(data.get("bias_score", fg.score)),
            confidence=float(data.get("confidence", 0.5)),
            summary=data.get("summary", ""),
            raw_response=raw,
        )

    def _fear_greed_only(self, symbol: str, fg: FearGreedData) -> SentimentResult:
        """Fallback כשאין חדשות - מסתמך על Fear & Greed בלבד."""
        sentiment = (
            "positive" if fg.score > 2 else
            "negative" if fg.score < -2 else
            "neutral"
        )
        return SentimentResult(
            symbol=symbol,
            fear_greed_value=fg.value,
            fear_greed_label=fg.classification,
            fear_greed_score=fg.score,
            headlines_count=0,
            market_sentiment=sentiment,
            news_sentiment_score=0.0,
            catalysts=[],
            urgency="low",
            fear_greed_alignment="neutral",
            bias_score=fg.score * 0.7,  # confidence נמוך יותר ללא חדשות
            confidence=0.3,
            summary=f"No news available. Fear & Greed: {fg.value} ({fg.classification})",
        )

    @staticmethod
    def _parse_json(text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        logger.warning(f"[SentimentAgent] JSON parse failed: {text[:200]}")
        return {}
