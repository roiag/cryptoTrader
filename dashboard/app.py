"""
Trading Dashboard - FastAPI server.

הרצה:
  python dashboard/app.py
  או: uvicorn dashboard.app:app --port 8000 --reload
"""

import json
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse, JSONResponse

# ── Paths ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
DB_PATH = ROOT / "storage" / "trading.db"
HTML_PATH = Path(__file__).parent / "index.html"

app = FastAPI(title="Crypto Trading Dashboard", docs_url=None, redoc_url=None)


# ── DB helper ──────────────────────────────────────────────────────────────────

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _db_exists() -> bool:
    return DB_PATH.exists()


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_PATH.read_text(encoding="utf-8")


@app.get("/api/latest")
async def api_latest():
    """
    ציון אחרון לכל pair מ-combined_log.
    כולל גם הסנטימנט האחרון מ-sentiment_log.
    """
    if not _db_exists():
        return JSONResponse({"pairs": [], "error": "DB not found - run the bot first"})

    pairs_data = []
    with _db() as conn:
        for symbol in ["BTC/USDT", "ETH/USDT"]:
            row = conn.execute(
                """
                SELECT timestamp, math_score, vision_score, sentiment_score,
                       final_score, verdict, timeframe
                FROM combined_log
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()

            sent = conn.execute(
                """
                SELECT fear_greed_value, fear_greed_label, market_sentiment,
                       urgency, catalysts, summary, confidence
                FROM sentiment_log
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()

            last_decision = conn.execute(
                """
                SELECT action, executed, rejection_reason, entry_price,
                       sl_price, tp_price, quantity, notional, risk_amount,
                       math_reasoning, vision_reasoning, screenshot_file
                FROM decisions
                WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol,),
            ).fetchone()

            if row:
                pairs_data.append({
                    "symbol": symbol,
                    "timestamp": row["timestamp"],
                    "timeframe": row["timeframe"],
                    "math_score": row["math_score"],
                    "vision_score": row["vision_score"],
                    "sentiment_score": row["sentiment_score"],
                    "final_score": row["final_score"],
                    "verdict": row["verdict"],
                    "sentiment": dict(sent) if sent else None,
                    "last_decision": {
                        **dict(last_decision),
                        "math_reasoning": _safe_json(last_decision["math_reasoning"]),
                    } if last_decision else None,
                })
            else:
                pairs_data.append({
                    "symbol": symbol,
                    "timestamp": None,
                    "verdict": "NO DATA",
                    "final_score": 0,
                    "math_score": 0,
                    "vision_score": 0,
                    "sentiment_score": 0,
                })

    return JSONResponse({"pairs": pairs_data})


@app.get("/api/decisions")
async def api_decisions(
    limit: int = Query(default=50, ge=1, le=200),
    symbol: Optional[str] = Query(default=None),
):
    """החלטות אחרונות עם נימוק מלא."""
    if not _db_exists():
        return JSONResponse({"decisions": []})

    with _db() as conn:
        if symbol:
            rows = conn.execute(
                """
                SELECT id, timestamp, symbol, timeframe,
                       math_score, vision_score, sentiment_score, final_score,
                       action, executed, rejection_reason,
                       entry_price, sl_price, tp_price, quantity, notional, risk_amount,
                       math_reasoning, vision_reasoning, sentiment_summary, screenshot_file
                FROM decisions WHERE symbol = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, timestamp, symbol, timeframe,
                       math_score, vision_score, sentiment_score, final_score,
                       action, executed, rejection_reason,
                       entry_price, sl_price, tp_price, quantity, notional, risk_amount,
                       math_reasoning, vision_reasoning, sentiment_summary, screenshot_file
                FROM decisions
                ORDER BY timestamp DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()

    decisions = []
    for r in rows:
        d = dict(r)
        d["math_reasoning"] = _safe_json(d["math_reasoning"])
        d["executed"] = bool(d["executed"])
        decisions.append(d)

    return JSONResponse({"decisions": decisions})


@app.get("/api/trades")
async def api_trades():
    """פוזיציות פתוחות + 20 עסקאות אחרונות שנסגרו."""
    if not _db_exists():
        return JSONResponse({"open": [], "closed": []})

    with _db() as conn:
        open_rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'open' ORDER BY timestamp DESC"
        ).fetchall()
        closed_rows = conn.execute(
            """
            SELECT * FROM trades WHERE status != 'open'
            ORDER BY timestamp DESC LIMIT 20
            """
        ).fetchall()

    return JSONResponse({
        "open": [dict(r) for r in open_rows],
        "closed": [dict(r) for r in closed_rows],
    })


@app.get("/api/chart")
async def api_chart(
    symbol: str = Query(default="BTC/USDT"),
    limit: int = Query(default=48, ge=5, le=200),
):
    """היסטוריית ציונים לגרף - לפי symbol."""
    if not _db_exists():
        return JSONResponse({"labels": [], "datasets": {}})

    with _db() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, math_score, vision_score,
                   sentiment_score, final_score, verdict
            FROM combined_log WHERE symbol = ?
            ORDER BY timestamp DESC LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()

    rows = list(reversed(rows))  # מהישן לחדש
    labels = [_fmt_time(r["timestamp"]) for r in rows]

    return JSONResponse({
        "labels": labels,
        "math":      [r["math_score"]      for r in rows],
        "vision":    [r["vision_score"]    for r in rows],
        "sentiment": [r["sentiment_score"] for r in rows],
        "final":     [r["final_score"]     for r in rows],
        "verdicts":  [r["verdict"]         for r in rows],
    })


@app.get("/api/daily")
async def api_daily():
    """סיכום יומי של היום."""
    if not _db_exists():
        return JSONResponse({
            "date": str(date.today()),
            "total_trades": 0, "wins": 0, "losses": 0,
            "total_pnl": 0.0, "win_rate": 0.0, "max_drawdown": 0.0,
        })

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db() as conn:
        row = conn.execute(
            "SELECT * FROM daily_summary WHERE date = ?", (today,)
        ).fetchone()

        # גם HOLD stats
        hold_count = conn.execute(
            """
            SELECT COUNT(*) FROM decisions
            WHERE DATE(timestamp) = ? AND action = 'HOLD'
            """,
            (today,),
        ).fetchone()[0]

        executed_count = conn.execute(
            """
            SELECT COUNT(*) FROM decisions
            WHERE DATE(timestamp) = ? AND executed = 1
            """,
            (today,),
        ).fetchone()[0]

    if row:
        total = row["total_trades"]
        wins = row["wins"]
        return JSONResponse({
            "date": today,
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"],
            "total_pnl": row["total_pnl"],
            "win_rate": wins / total if total > 0 else 0.0,
            "max_drawdown": row["max_drawdown"] if "max_drawdown" in row.keys() else 0.0,
            "hold_count": hold_count,
            "executed_count": executed_count,
        })

    return JSONResponse({
        "date": today,
        "total_trades": 0, "wins": 0, "losses": 0,
        "total_pnl": 0.0, "win_rate": 0.0, "max_drawdown": 0.0,
        "hold_count": hold_count,
        "executed_count": executed_count,
    })


@app.get("/api/score_distribution")
async def api_score_distribution(days: int = Query(default=7, ge=1, le=30)):
    """התפלגות ציונים לפי pair (לסטטיסטיקה)."""
    if not _db_exists():
        return JSONResponse({"btc": [], "eth": []})

    cutoff = f"datetime('now', '-{days} days')"
    with _db() as conn:
        btc = conn.execute(
            f"SELECT final_score FROM combined_log WHERE symbol='BTC/USDT' AND timestamp >= {cutoff}"
        ).fetchall()
        eth = conn.execute(
            f"SELECT final_score FROM combined_log WHERE symbol='ETH/USDT' AND timestamp >= {cutoff}"
        ).fetchall()

    return JSONResponse({
        "btc": [r[0] for r in btc],
        "eth": [r[0] for r in eth],
    })


# ── Utils ──────────────────────────────────────────────────────────────────────

def _safe_json(text: str | None) -> list:
    if not text:
        return []
    try:
        return json.loads(text)
    except Exception:
        return [str(text)]


def _fmt_time(ts: str) -> str:
    """2025-01-15T12:30:00 → 12:30"""
    try:
        return ts[11:16]
    except Exception:
        return ts


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import webbrowser
    print("\n  Trading Dashboard → http://localhost:8000\n")
    webbrowser.open("http://localhost:8000")
    uvicorn.run("dashboard.app:app", host="0.0.0.0", port=8000, reload=False)
