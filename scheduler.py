"""
Scheduler - נקודת הכניסה לריצה רציפה 24/7.

Jobs:
  • pipeline      - כל RUN_INTERVAL_MINUTES (ניתוח + עסקאות)
  • monitor       - כל 5 דקות (מעקב פוזיציות)
  • daily_summary - כל יום ב-23:55 UTC

הרצה:
  python scheduler.py
"""

import asyncio
import signal
import sys
import time

# Windows: force UTF-8 output so Unicode chars (█ ░ ▲) print correctly
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger
from rich.console import Console

from config.pairs import TRADING_PAIRS
from config.settings import settings
from agents.review_agent import ReviewAgent
from execution.position_monitor import PositionMonitor
from notifications import telegram
from storage.db import init_db, DB_PATH

# pipeline מיובא עצלנית כדי לשתף את ChartCapture instance
import main as pipeline_module

console = Console()

# ── Logging setup ──────────────────────────────────────────────────────────────
logger.remove()
logger.add(sys.stderr, level="INFO", colorize=True,
           format="<green>{time:HH:mm:ss}</green> | <level>{level:<7}</level> | {message}")
logger.add(
    "storage/logs/trading_{time:YYYY-MM-DD}.log",
    level="DEBUG",
    rotation="00:00",     # קובץ חדש בחצות
    retention="30 days",
    compression="zip",
)


# ── State ──────────────────────────────────────────────────────────────────────
_capture      = None
_executor     = None
_monitor      = PositionMonitor()
_pipeline_running = False   # מונע ריצות מקבילות של pipeline


# ── Job: Pipeline ──────────────────────────────────────────────────────────────

async def job_pipeline() -> None:
    global _pipeline_running, _capture, _executor

    if _pipeline_running:
        logger.warning("Pipeline already running, skipping this tick")
        return

    _pipeline_running = True
    try:
        from capture.screenshot import ChartCapture
        if _capture is None:
            _capture  = await ChartCapture.instance()
            _executor = ThreadPoolExecutor(max_workers=len(TRADING_PAIRS) * 3)

        logger.info("=" * 50)
        logger.info(f"Pipeline tick @ {datetime.utcnow().strftime('%H:%M:%S UTC')}")

        for symbol in TRADING_PAIRS:
            try:
                t0 = time.perf_counter()

                math_r, vision_r, sentiment_r, screenshot_file = (
                    await pipeline_module.run_pipeline_for_symbol(
                        symbol, _capture, _executor
                    )
                )

                from storage.db import log_math_analysis, log_sentiment_analysis, log_combined
                log_math_analysis(math_r)
                log_sentiment_analysis(sentiment_r)

                final_score = pipeline_module.calc_final_score(math_r, vision_r, sentiment_r)

                if final_score >= 4.0:
                    verdict = "BULLISH"
                elif final_score <= -4.0:
                    verdict = "BEARISH"
                else:
                    verdict = "NEUTRAL"

                log_combined(
                    symbol=symbol, timeframe=settings.TIMEFRAME,
                    math_score=math_r.bias_score,
                    vision_score=vision_r.bias_score,
                    sentiment_score=sentiment_r.bias_score,
                    final_score=final_score, verdict=verdict,
                )

                from agents.execution_agent import ExecutionAgent
                loop = asyncio.get_event_loop()
                exec_agent = ExecutionAgent()
                decision = await loop.run_in_executor(
                    _executor,
                    lambda: exec_agent.execute(
                        math_r, vision_r, sentiment_r, final_score, screenshot_file
                    ),
                )

                elapsed = time.perf_counter() - t0
                pipeline_module.print_symbol_panel(
                    math_r, vision_r, sentiment_r,
                    decision, final_score, elapsed, screenshot_file,
                )

            except Exception as e:
                logger.error(f"Pipeline error for {symbol}: {e}")
                telegram.notify_error(f"Pipeline {symbol}", str(e))

    finally:
        _pipeline_running = False


# ── Job: Position Monitor ──────────────────────────────────────────────────────

async def job_monitor() -> None:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _monitor.run)
    except Exception as e:
        logger.error(f"Position monitor error: {e}")
        telegram.notify_error("Position Monitor", str(e))


# ── Job: Daily Summary ─────────────────────────────────────────────────────────

async def job_daily_summary() -> None:
    import sqlite3
    today = datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT * FROM daily_summary WHERE date = ?", (today,)
            ).fetchone()

        if row:
            total, wins, losses, pnl = row[1], row[2], row[3], row[4]
            win_rate = wins / total if total > 0 else 0.0
            telegram.notify_daily_summary(
                date=today,
                total_trades=total,
                wins=wins,
                losses=losses,
                total_pnl=pnl,
                win_rate=win_rate,
                max_drawdown=row[5] if len(row) > 5 else 0.0,
            )
        else:
            telegram.notify_daily_summary(
                date=today, total_trades=0, wins=0,
                losses=0, total_pnl=0.0, win_rate=0.0, max_drawdown=0.0,
            )
    except Exception as e:
        logger.error(f"Daily summary error: {e}")


# ── Job: Weekly Review ────────────────────────────────────────────────────────

async def job_weekly_review() -> None:
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: ReviewAgent().run(days=7))
    except Exception as e:
        logger.error(f"Weekly review error: {e}")
        telegram.notify_error("Weekly Review", str(e))


# ── Shutdown ───────────────────────────────────────────────────────────────────

async def shutdown(scheduler: AsyncIOScheduler) -> None:
    logger.info("Shutting down...")
    scheduler.shutdown(wait=False)
    if _capture:
        await _capture.close()
    if _executor:
        _executor.shutdown(wait=False)
    logger.info("Bye.")


# ── Main ───────────────────────────────────────────────────────────────────────

async def main() -> None:
    init_db()

    console.print(
        f"\n[bold cyan]=== Crypto Trading Bot - Starting ===[/bold cyan]\n"
        f"Pairs    : [yellow]{', '.join(TRADING_PAIRS)}[/yellow]\n"
        f"Timeframe: [yellow]{settings.TIMEFRAME}[/yellow]  |  "
        f"Interval : [yellow]{settings.RUN_INTERVAL_MINUTES}m[/yellow]  |  "
        f"Paper    : [yellow]{settings.PAPER_TRADING}[/yellow]\n"
    )

    telegram.notify_pipeline_start(TRADING_PAIRS, settings.TIMEFRAME)

    scheduler = AsyncIOScheduler(timezone="UTC")

    # Pipeline - כל X דקות
    scheduler.add_job(
        job_pipeline,
        trigger=IntervalTrigger(minutes=settings.RUN_INTERVAL_MINUTES),
        id="pipeline",
        name="Analysis + Trading Pipeline",
        max_instances=1,
        misfire_grace_time=60,
    )

    # Position Monitor - כל 5 דקות
    scheduler.add_job(
        job_monitor,
        trigger=IntervalTrigger(minutes=5),
        id="monitor",
        name="Position Monitor",
        max_instances=1,
    )

    # Daily Summary - 23:55 UTC every day
    scheduler.add_job(
        job_daily_summary,
        trigger=CronTrigger(hour=23, minute=55),
        id="daily_summary",
        name="Daily P&L Summary",
    )

    # Weekly Performance Review - every Monday at 08:00 UTC
    scheduler.add_job(
        job_weekly_review,
        trigger=CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="weekly_review",
        name="Weekly Performance Review",
    )

    scheduler.start()
    logger.info(
        f"Scheduler started. "
        f"Pipeline every {settings.RUN_INTERVAL_MINUTES}m, "
        f"Monitor every 5m."
    )

    # הרצה ראשונה מיידית
    await job_pipeline()

    # Loop עד Ctrl+C
    loop = asyncio.get_event_loop()
    stop = loop.create_future()

    def _signal_handler():
        if not stop.done():
            stop.set_result(None)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass  # Windows לא תומך ב-add_signal_handler לכמה סיגנלים

    try:
        await stop
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await shutdown(scheduler)


if __name__ == "__main__":
    asyncio.run(main())
