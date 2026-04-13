"""
נקודת כניסה - Pipeline מלא
Math + Vision + Sentiment → Verdict → Risk Guard → Execute
"""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from config.pairs import TRADING_PAIRS
from config.settings import settings
from agents.math_agent import MathAgent, MathResult
from agents.vision_agent import VisionAgent, VisionResult
from agents.sentiment_agent import SentimentAgent, SentimentResult
from agents.execution_agent import ExecutionAgent, ExecutionDecision
from capture.screenshot import ChartCapture
from data.indicators import calculate_all
from data.regime import detect as detect_regime, RegimeResult
from storage.db import (
    init_db,
    log_math_analysis,
    log_sentiment_analysis,
    log_combined,
)

console = Console()
WEIGHTS = {"math": 0.45, "vision": 0.45, "sentiment": 0.10}
DEFAULT_THRESHOLD = 4.0


# ── Score helpers ──────────────────────────────────────────────────────────────

def _sc(score: float) -> str:
    """Color string for a score."""
    if score >= 3:  return "green"
    if score <= -3: return "red"
    return "yellow"


def _bar(score: float, width: int = 10) -> str:
    """Return colored bar markup string."""
    filled = max(1, round(abs(score) / 10 * width))
    empty  = width - filled
    c = _sc(score)
    return f"[bold {c}]{'█' * filled}[/bold {c}][dim]{'░' * empty}[/dim]"


def calc_final_score(
    math: MathResult, vision: VisionResult, sentiment: SentimentResult
) -> float:
    return round(
        math.bias_score      * WEIGHTS["math"] +
        vision.bias_score    * WEIGHTS["vision"] +
        sentiment.bias_score * WEIGHTS["sentiment"],
        2,
    )


# ── Main display function ─────────────────────────────────────────────────────

def print_symbol_panel(
    math: MathResult,
    vision: VisionResult,
    sentiment: SentimentResult,
    decision: ExecutionDecision,
    final_score: float,
    elapsed: float,
    screenshot_file: str,
) -> None:
    """פאנל אחד, נקי, לכל symbol."""

    now = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

    # Verdict
    if final_score >= 4.0:
        verdict, border = "[bold green]▲  BULLISH[/bold green]", "green"
    elif final_score <= -4.0:
        verdict, border = "[bold red]▼  BEARISH[/bold red]", "red"
    else:
        verdict, border = "[bold yellow]◆  NEUTRAL[/bold yellow]", "yellow"

    # ── Math key bullets ──────────────────────────────────────────────────────
    snap = math.raw
    math_pts = []
    if snap.get("price") and snap.get("ema_20") and snap.get("ema_50"):
        if snap["price"] > snap["ema_20"] > snap["ema_50"]:
            math_pts.append("Price > EMA20 > EMA50")
        elif snap["price"] < snap["ema_20"]:
            math_pts.append("Price below EMA20")
    if snap.get("rsi"):
        rsi = snap["rsi"]
        tag = "overbought" if rsi > 70 else ("oversold" if rsi < 30 else "neutral")
        math_pts.append(f"RSI {rsi:.0f} ({tag})")
    if snap.get("macd_hist") is not None:
        prev = snap.get("macd_hist_prev") or 0
        math_pts.append(f"MACD {'▲' if snap['macd_hist'] > prev else '▼'}")
    math_line = "  ·  ".join(math_pts) if math_pts else "—"

    # ── Vision key bullets ────────────────────────────────────────────────────
    vis_pts = [f"{vision.trend} ({vision.trend_strength})"]
    if vision.patterns:
        vis_pts.append(vision.patterns[0])
    if vision.candle_signals:
        vis_pts.append(vision.candle_signals[0])
    vis_line = "  ·  ".join(vis_pts)[:60]
    key_obs  = (vision.key_observation or "")[:68]

    # ── Sentiment key bullets ─────────────────────────────────────────────────
    fg       = sentiment.fear_greed_value
    fg_color = "red" if fg < 30 else ("green" if fg > 70 else "yellow")
    sent_pts = [f"F&G {fg}/100 ({sentiment.fear_greed_label})"]
    if sentiment.catalysts:
        sent_pts.append(sentiment.catalysts[0][:40])
    sent_line = "  ·  ".join(sent_pts)

    # ── Levels ────────────────────────────────────────────────────────────────
    price    = snap.get("price") or 0
    sl_price = round(price * (1 - math.sl_distance_pct), 2) if price else 0
    tp_price = round(price * (1 + math.tp_distance_pct), 2) if price else 0
    rr       = math.tp_distance_pct / math.sl_distance_pct if math.sl_distance_pct else 0

    # ── Execution line ────────────────────────────────────────────────────────
    if decision.executed and decision.size:
        s = decision.size
        coin = math.symbol.split("/")[0]
        exec_line = (
            f"[bold green]✓ {decision.action}  {s.quantity} {coin}  "
            f"@ {s.entry_price:,.2f}  "
            f"(${s.notional:.0f}  ·  risk ${s.risk_amount:.2f})[/bold green]"
        )
    elif decision.action == "HOLD":
        exec_line = f"[dim]↷  HOLD  —  {decision.rejection_reason}[/dim]"
    else:
        exec_line = (
            f"[yellow]⚠  {decision.action} BLOCKED  —  "
            f"{decision.rejection_reason}[/yellow]"
        )

    # ── Assemble markup string ────────────────────────────────────────────────
    mc, vc, sc = _sc(math.bias_score), _sc(vision.bias_score), _sc(sentiment.bias_score)
    fc = _sc(final_score)

    body = (
        f"\n"
        f"  [dim]Math     [/dim] {_bar(math.bias_score)}  "
        f"[bold {mc}]{math.bias_score:+.1f}[/bold {mc}]"
        f"[dim]  ·  {math.confidence:.0%} conf[/dim]\n"

        f"  [dim]Vision   [/dim] {_bar(vision.bias_score)}  "
        f"[bold {vc}]{vision.bias_score:+.1f}[/bold {vc}]"
        f"[dim]  ·  {vision.confidence:.0%} conf[/dim]\n"

        f"  [dim]Sentiment[/dim] {_bar(sentiment.bias_score)}  "
        f"[bold {sc}]{sentiment.bias_score:+.1f}[/bold {sc}]"
        f"[dim]  ·  {sentiment.confidence:.0%} conf[/dim]\n"

        f"  [dim]──────────────────────────────────────[/dim]\n"

        f"  [dim]Final    [/dim] {_bar(final_score)}  "
        f"[bold {fc}]{final_score:+.1f}[/bold {fc}]"
        f"  →  {verdict}\n"

        f"\n"
        f"  [dim]Math    [/dim]  {math_line}\n"
        f"  [dim]Vision  [/dim]  {vis_line}\n"
        + (f"  [dim]         [/dim]  [italic dim]{key_obs}[/italic dim]\n" if key_obs else "")
        + f"  [dim]News    [/dim]  [{fg_color}]{sent_line}[/{fg_color}]\n"

        f"\n"
        + (
            f"  [dim]Entry ≈ {price:,.0f}  "
            f"·  SL {sl_price:,.0f} ({math.sl_distance_pct:.1%})"
            f"  ·  TP {tp_price:,.0f} ({math.tp_distance_pct:.1%})"
            f"  ·  R:R {rr:.1f}×[/dim]\n\n"
            if price else ""
        )
        + f"  {exec_line}\n"

        f"\n"
        f"  [dim]⏱ {elapsed:.1f}s  ·  📸 {screenshot_file}[/dim]"
    )

    console.print(Panel(
        body,
        title=f"[bold]{math.symbol}[/bold]  [dim]{math.timeframe}  ·  {now}[/dim]",
        border_style=border,
        padding=(0, 1),
    ))


# ── Core pipeline ──────────────────────────────────────────────────────────────

def _run_math(symbol: str) -> MathResult:
    return MathAgent().analyze(symbol, settings.TIMEFRAME)


def _run_sentiment(symbol: str) -> SentimentResult:
    return SentimentAgent().analyze(symbol)


async def run_pipeline_for_symbol(
    symbol: str,
    capture: ChartCapture,
    executor: ThreadPoolExecutor,
) -> tuple[MathResult, VisionResult, SentimentResult, str]:
    loop = asyncio.get_event_loop()

    math_fut      = loop.run_in_executor(executor, _run_math, symbol)
    sentiment_fut = loop.run_in_executor(executor, _run_sentiment, symbol)
    screenshot_t  = asyncio.create_task(capture.capture(symbol, settings.TIMEFRAME))

    math_r, sentiment_r, screenshot = await asyncio.gather(
        math_fut, sentiment_fut, screenshot_t
    )
    vision_r = await loop.run_in_executor(
        executor,
        lambda: VisionAgent().analyze(screenshot, symbol, settings.TIMEFRAME),
    )

    safe_sym = symbol.replace("/", "_")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    screenshot_file = f"{safe_sym}_{settings.TIMEFRAME}_{ts}.jpg"

    return math_r, vision_r, sentiment_r, screenshot_file


async def run_all() -> None:
    init_db()

    console.print(
        f"\n[bold cyan]Crypto Trading Pipeline[/bold cyan]  "
        f"[dim]{', '.join(TRADING_PAIRS)}  ·  {settings.TIMEFRAME}  ·  "
        f"{'PAPER' if settings.PAPER_TRADING else 'LIVE'}[/dim]\n"
    )

    capture    = await ChartCapture.instance()
    executor   = ThreadPoolExecutor(max_workers=len(TRADING_PAIRS) * 3)
    exec_agent = ExecutionAgent()

    try:
        for symbol in TRADING_PAIRS:
            t0 = time.perf_counter()

            try:
                math_r, vision_r, sentiment_r, screenshot_file = (
                    await run_pipeline_for_symbol(symbol, capture, executor)
                )

                log_math_analysis(math_r)
                log_sentiment_analysis(sentiment_r)

                final_score = calc_final_score(math_r, vision_r, sentiment_r)

                # Dynamic threshold from market regime
                df_for_regime = calculate_all(MathAgent().exchange.fetch_ohlcv(symbol, settings.TIMEFRAME))
                regime: RegimeResult = detect_regime(df_for_regime)

                threshold = regime.threshold
                if final_score >= threshold:
                    verdict = "BULLISH"
                elif final_score <= -threshold:
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

                decision = await asyncio.get_event_loop().run_in_executor(
                    executor,
                    lambda: exec_agent.execute(
                        math_r, vision_r, sentiment_r,
                        final_score, screenshot_file,
                    ),
                )

                elapsed = time.perf_counter() - t0
                print_symbol_panel(
                    math_r, vision_r, sentiment_r,
                    decision, final_score,
                    elapsed, screenshot_file,
                )

            except Exception as e:
                logger.error(f"Pipeline failed for {symbol}: {e}")
                console.print(
                    Panel(f"[red]Pipeline error: {e}[/red]",
                          title=f"[bold]{symbol}[/bold]", border_style="red")
                )

    finally:
        executor.shutdown(wait=False)
        await capture.close()


if __name__ == "__main__":
    asyncio.run(run_all())
