"""
שליפת חדשות וסנטימנט מהאינטרנט.

מקורות (ללא API key, חינמיים לגמרי):
  1. Fear & Greed Index  - alternative.me
  2. RSS feeds           - CoinTelegraph, CoinDesk, Decrypt
  3. CryptoPanic         - אופציונלי, דורש token חינמי

כל הקריאות synchronous - ירוצו ב-thread pool.
"""

import time
from dataclasses import dataclass

import feedparser
import requests
from loguru import logger

# ── Timeouts ───────────────────────────────────────────────────────────────────
HTTP_TIMEOUT = 8   # שניות

# ── Fear & Greed ───────────────────────────────────────────────────────────────
FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"

# ── RSS Feeds לכל pair ─────────────────────────────────────────────────────────
RSS_FEEDS: dict[str, list[str]] = {
    "BTC/USDT": [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://www.coindesk.com/arc/outboundfeeds/rss/?category=bitcoin",
        "https://decrypt.co/feed/tag/bitcoin",
    ],
    "ETH/USDT": [
        "https://cointelegraph.com/rss/tag/ethereum",
        "https://www.coindesk.com/arc/outboundfeeds/rss/?category=ethereum",
        "https://decrypt.co/feed/tag/ethereum",
    ],
    # fallback כללי - חדשות קריפטו כלליות
    "_general": [
        "https://cointelegraph.com/rss",
        "https://decrypt.co/feed",
    ],
}

# ── CryptoPanic (אופציונלי) ────────────────────────────────────────────────────
CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/"
CRYPTOPANIC_SYMBOL_MAP = {
    "BTC/USDT": "BTC",
    "ETH/USDT": "ETH",
}


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass
class Headline:
    title: str
    source: str
    published: str   # ISO string או שרשרת גולמית


@dataclass
class FearGreedData:
    value: int               # 0-100
    classification: str      # "Extreme Fear", "Fear", "Neutral", "Greed", "Extreme Greed"
    score: float             # -10 עד +10 (המרה ליניארית)


# ── Fetchers ───────────────────────────────────────────────────────────────────

def fetch_fear_greed() -> FearGreedData:
    """
    שולף את מדד הפחד-וחמדנות הנוכחי.
    0 = Extreme Fear → score -10
    100 = Extreme Greed → score +10
    """
    try:
        resp = requests.get(FEAR_GREED_URL, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        entry = resp.json()["data"][0]
        value = int(entry["value"])
        classification = entry["value_classification"]
        # המרה: 0→-10, 50→0, 100→+10
        score = round((value - 50) / 5.0, 2)
        logger.debug(f"Fear & Greed: {value} ({classification}) → score {score:+.1f}")
        return FearGreedData(value=value, classification=classification, score=score)
    except Exception as e:
        logger.warning(f"Fear & Greed fetch failed: {e}")
        return FearGreedData(value=50, classification="Neutral", score=0.0)


def fetch_headlines_rss(symbol: str, max_per_feed: int = 5) -> list[Headline]:
    """
    שולף כותרות חדשות מ-RSS feeds.
    מנסה feeds מרובים, מחזיר רשימה מאוחדת.
    """
    feeds_to_try = RSS_FEEDS.get(symbol, []) + RSS_FEEDS["_general"]
    headlines: list[Headline] = []
    seen: set[str] = set()

    for feed_url in feeds_to_try:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:max_per_feed]:
                title = entry.get("title", "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                headlines.append(Headline(
                    title=title,
                    source=feed.feed.get("title", feed_url),
                    published=entry.get("published", ""),
                ))
        except Exception as e:
            logger.debug(f"RSS feed failed ({feed_url}): {e}")

    logger.debug(f"Fetched {len(headlines)} headlines for {symbol}")
    return headlines


def fetch_headlines_cryptopanic(
    symbol: str,
    auth_token: str,
    max_articles: int = 10,
) -> list[Headline]:
    """
    אופציונלי: CryptoPanic API (token חינמי זמין ב-cryptopanic.com).
    מחזיר רשימה ריקה אם אין token.
    """
    if not auth_token:
        return []

    coin = CRYPTOPANIC_SYMBOL_MAP.get(symbol)
    if not coin:
        return []

    try:
        resp = requests.get(
            CRYPTOPANIC_URL,
            params={
                "auth_token": auth_token,
                "currencies": coin,
                "filter": "news",
                "public": "true",
            },
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json().get("results", [])
        headlines = [
            Headline(
                title=r["title"],
                source=r.get("source", {}).get("title", "CryptoPanic"),
                published=r.get("published_at", ""),
            )
            for r in results[:max_articles]
        ]
        logger.debug(f"CryptoPanic: {len(headlines)} articles for {symbol}")
        return headlines
    except Exception as e:
        logger.warning(f"CryptoPanic fetch failed: {e}")
        return []


def fetch_all(
    symbol: str,
    cryptopanic_token: str = "",
) -> tuple[FearGreedData, list[Headline]]:
    """
    נקודת כניסה ראשית - שולף הכל.
    מחזיר (FearGreedData, [Headline])
    """
    t0 = time.perf_counter()

    fear_greed = fetch_fear_greed()

    # נסה CryptoPanic ראשון (עשיר יותר), fallback ל-RSS
    headlines = fetch_headlines_cryptopanic(symbol, cryptopanic_token, max_articles=15)
    if len(headlines) < 5:
        headlines += fetch_headlines_rss(symbol)

    # הסר כפילויות לאחר מיזוג
    seen: set[str] = set()
    unique: list[Headline] = []
    for h in headlines:
        if h.title not in seen:
            seen.add(h.title)
            unique.append(h)

    elapsed = time.perf_counter() - t0
    logger.debug(f"News fetch done in {elapsed:.1f}s: {len(unique)} headlines")
    return fear_greed, unique[:15]
