"""
צילום גרף מ-TradingView באמצעות Playwright.

עקרונות מהירות:
1. Browser ו-Context נשמרים בין קריאות (נפתחים פעם אחת)
2. כל symbol פותח page נפרד, נסגר בסוף
3. תמונה מוקטנת ל-1280x720 JPEG לפני שליחה ל-Claude
4. אין sleep קבוע - מחכים לאלמנטים ספציפיים
"""

import asyncio
import io
from datetime import datetime
from pathlib import Path

from PIL import Image
from loguru import logger
from playwright.async_api import async_playwright, Browser, BrowserContext

from capture.chart_config import build_chart_url

# ── הגדרות ────────────────────────────────────────────────────────────────────
SCREENSHOT_DIR = Path("storage/screenshots")
CHART_WIDTH    = 1280
CHART_HEIGHT   = 720
JPEG_QUALITY   = 85

# CSS selector של אזור הגרף ב-TradingView
CHART_SELECTOR = ".chart-container"

# אלמנטים שצריך לדחות אם מופיעים (cookies / login popups)
DISMISS_SELECTORS = [
    '[id="overlap-manager-root"] [data-name="close"]',
    '[data-role="toast-close-button"]',
    'button[class*="acceptAll"]',
    '#overlap-manager-root button',
]


class ChartCapture:
    """
    Singleton-style - Browser וContext נוצרים פעם אחת ונשמרים.
    קרא ל-`await ChartCapture.instance()` לקבלת האובייקט.
    """

    _instance: "ChartCapture | None" = None

    def __init__(self) -> None:
        self._playwright = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    @classmethod
    async def instance(cls) -> "ChartCapture":
        if cls._instance is None:
            cls._instance = ChartCapture()
            await cls._instance._start()
        return cls._instance

    async def _start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
            ],
        )
        self._context = await self._browser.new_context(
            viewport={"width": CHART_WIDTH, "height": CHART_HEIGHT},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        # Block מדיה ומודעות - מאיץ טעינה
        await self._context.route(
            "**/*.{png,jpg,gif,svg,woff,woff2}",
            lambda r: r.abort() if "tradingview" not in r.request.url else r.continue_(),
        )
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        logger.info("ChartCapture: browser started")

    async def capture(self, symbol: str, timeframe: str) -> bytes:
        """
        מצלם את הגרף של symbol/timeframe.
        מחזיר bytes של JPEG מוקטן.
        """
        url = build_chart_url(symbol, timeframe)
        logger.debug(f"Capturing {symbol} [{timeframe}] → {url}")

        page = await self._context.new_page()
        try:
            # טעינת הדף
            await page.goto(url, wait_until="domcontentloaded", timeout=30_000)

            # דחיית popups
            await self._dismiss_popups(page)

            # המתנה לגרף
            await page.wait_for_selector(CHART_SELECTOR, timeout=20_000)

            # המתנה נוספת קצרה לרינדור הנרות (אנימציה)
            await page.wait_for_timeout(1_500)

            # צילום אזור הגרף בלבד
            chart_el = await page.query_selector(CHART_SELECTOR)
            if chart_el is None:
                raise RuntimeError("Chart element not found")

            raw_bytes = await chart_el.screenshot(type="jpeg", quality=JPEG_QUALITY)
            compressed = self._compress(raw_bytes)

            # שמירה עם timestamp + עדכון _latest
            safe_sym  = symbol.replace("/", "_")
            ts        = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            ts_path   = SCREENSHOT_DIR / f"{safe_sym}_{timeframe}_{ts}.jpg"
            latest_path = SCREENSHOT_DIR / f"{safe_sym}_{timeframe}_latest.jpg"

            ts_path.write_bytes(compressed)
            latest_path.write_bytes(compressed)   # תמיד עדכני לצפייה מהירה

            logger.debug(
                f"Screenshot saved → {ts_path.name} ({len(compressed)/1024:.1f} KB)"
            )
            return compressed

        finally:
            await page.close()

    async def _dismiss_popups(self, page) -> None:
        """מנסה לסגור popups נפוצים - ממשיך בכל מקרה."""
        for sel in DISMISS_SELECTORS:
            try:
                await page.click(sel, timeout=1_500)
                logger.debug(f"Dismissed popup: {sel}")
            except Exception:
                pass

    @staticmethod
    def _compress(raw: bytes) -> bytes:
        """
        מקטין תמונה ל-1280x720 ומשמר יחס גובה-רוחב.
        מוריד גודל ומפחית tokens ב-Claude API.
        """
        img = Image.open(io.BytesIO(raw))
        img.thumbnail((CHART_WIDTH, CHART_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
        return buf.getvalue()

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        ChartCapture._instance = None
        logger.info("ChartCapture: browser closed")


# ── Helper סינכרוני לשימוש קל ─────────────────────────────────────────────────
def capture_sync(symbol: str, timeframe: str) -> bytes:
    """
    Wrapper סינכרוני - מריץ async בתוך event loop חדש.
    נוח אם הקוד הקורא אינו async.
    """
    async def _run():
        cap = await ChartCapture.instance()
        return await cap.capture(symbol, timeframe)

    return asyncio.run(_run())
