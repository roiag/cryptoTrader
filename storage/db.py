"""
SQLite logger - שומר כל ניתוח ועסקה לבסיס נתונים מקומי.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from loguru import logger

DB_PATH = Path("storage/trading.db")


def init_db() -> None:
    """יוצר את טבלאות ה-DB אם לא קיימות."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analysis_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                timeframe   TEXT    NOT NULL,
                agent       TEXT    NOT NULL,
                signal      TEXT,
                bias_score  REAL,
                confidence  REAL,
                reasoning   TEXT,
                raw_data    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp    TEXT    NOT NULL,
                symbol       TEXT    NOT NULL,
                side         TEXT    NOT NULL,
                quantity     REAL,
                price        REAL,
                sl_price     REAL,
                tp_price     REAL,
                order_id     TEXT,
                sl_order_id  TEXT,
                tp_order_id  TEXT,
                trail_peak   REAL,
                status       TEXT,
                pnl          REAL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sentiment_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp           TEXT    NOT NULL,
                symbol              TEXT    NOT NULL,
                fear_greed_value    INTEGER,
                fear_greed_label    TEXT,
                market_sentiment    TEXT,
                news_score          REAL,
                bias_score          REAL,
                confidence          REAL,
                urgency             TEXT,
                catalysts           TEXT,
                summary             TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS combined_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp       TEXT    NOT NULL,
                symbol          TEXT    NOT NULL,
                timeframe       TEXT    NOT NULL,
                math_score      REAL,
                vision_score    REAL,
                sentiment_score REAL,
                final_score     REAL,
                verdict         TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp        TEXT    NOT NULL,
                symbol           TEXT    NOT NULL,
                timeframe        TEXT    NOT NULL,
                -- ציונים
                math_score       REAL,
                vision_score     REAL,
                sentiment_score  REAL,
                final_score      REAL,
                -- החלטה
                action           TEXT    NOT NULL,   -- BUY / SELL / HOLD
                executed         INTEGER NOT NULL,   -- 1 = בוצע, 0 = נחסם/HOLD
                rejection_reason TEXT,
                -- פרטי עסקה (null אם HOLD)
                entry_price      REAL,
                sl_price         REAL,
                tp_price         REAL,
                quantity         REAL,
                notional         REAL,
                risk_amount      REAL,
                -- נימוקים מפורטים
                math_reasoning   TEXT,   -- JSON array
                vision_reasoning TEXT,   -- key_observation + patterns
                sentiment_summary TEXT,
                -- קישור לצילום מסך
                screenshot_file  TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                date        TEXT    PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                wins        INTEGER DEFAULT 0,
                losses      INTEGER DEFAULT 0,
                total_pnl   REAL    DEFAULT 0.0,
                max_drawdown REAL   DEFAULT 0.0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta_suggestions (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at        TEXT    NOT NULL,
                -- הצעת MetaAgent
                category          TEXT    NOT NULL,  -- math / vision / sentiment / execution / risk
                description       TEXT    NOT NULL,  -- תיאור ההצעה בטקסט חופשי
                suggested_change  TEXT    NOT NULL,  -- שינוי ספציפי (JSON)
                -- ניתוח attribution שהוביל להצעה
                attribution_data  TEXT,              -- JSON עם הנתונים הסטטיסטיים
                -- תוצאת backtest validation
                backtest_pf_before REAL,             -- profit factor לפני השינוי
                backtest_pf_after  REAL,             -- profit factor אחרי השינוי
                backtest_wr_before REAL,
                backtest_wr_after  REAL,
                backtest_trades    INTEGER,
                validated          INTEGER DEFAULT 0, -- 1 = עבר בקטסט, 0 = לא נבדק עדיין
                -- אימוץ
                adopted            INTEGER DEFAULT 0, -- 1 = אומץ ע"י המשתמש
                adopted_at         TEXT,
                notes              TEXT               -- הערות ידניות
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_outcomes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id        INTEGER NOT NULL,
                decision_id     INTEGER,
                symbol          TEXT    NOT NULL,
                side            TEXT    NOT NULL,
                outcome         TEXT    NOT NULL,   -- TP_HIT / SL_HIT / EOD / TIMEOUT
                entry_price     REAL,
                close_price     REAL,
                sl_price        REAL,
                tp_price        REAL,
                pnl_usdt        REAL,
                pnl_pct         REAL,
                entry_time      TEXT,
                close_time      TEXT,
                duration_minutes REAL,
                -- signal context at entry
                math_score      REAL,
                vision_score    REAL,
                sentiment_score REAL,
                final_score     REAL,
                fear_greed      INTEGER,
                regime          TEXT
            )
        """)
        conn.commit()

        # Migration: add new columns if they don't exist (safe for existing DBs)
        _migrate(conn)

    logger.debug(f"DB initialized at {DB_PATH.resolve()}")


def _migrate(conn: sqlite3.Connection) -> None:
    """הוספת עמודות חדשות ל-DB קיים — בטוח לריצה חוזרת."""
    new_cols = [
        ("trades", "sl_order_id", "TEXT"),
        ("trades", "tp_order_id", "TEXT"),
        ("trades", "trail_peak",  "REAL"),
    ]
    for table, col, col_type in new_cols:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            conn.commit()
            logger.debug(f"Migration: added column {table}.{col}")
        except Exception:
            pass  # עמודה כבר קיימת


def log_math_analysis(result) -> None:
    """שומר תוצאת MathAgent ל-DB."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO analysis_log
                (timestamp, symbol, timeframe, agent, signal, bias_score, confidence, reasoning, raw_data)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                result.symbol,
                result.timeframe,
                "math_agent",
                result.signal,
                result.bias_score,
                result.confidence,
                json.dumps(result.reasoning),
                json.dumps(result.raw),
            ),
        )
        conn.commit()


def log_sentiment_analysis(result) -> None:
    """שומר תוצאת SentimentAgent ל-DB."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO sentiment_log
                (timestamp, symbol, fear_greed_value, fear_greed_label,
                 market_sentiment, news_score, bias_score, confidence,
                 urgency, catalysts, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                result.symbol,
                result.fear_greed_value,
                result.fear_greed_label,
                result.market_sentiment,
                result.news_sentiment_score,
                result.bias_score,
                result.confidence,
                result.urgency,
                json.dumps(result.catalysts),
                result.summary,
            ),
        )
        conn.commit()


def log_combined(
    symbol: str,
    timeframe: str,
    math_score: float,
    vision_score: float,
    sentiment_score: float,
    final_score: float,
    verdict: str,
) -> None:
    """שומר את הציון המשולב הסופי."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO combined_log
                (timestamp, symbol, timeframe, math_score, vision_score,
                 sentiment_score, final_score, verdict)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                symbol, timeframe,
                math_score, vision_score, sentiment_score,
                final_score, verdict,
            ),
        )
        conn.commit()


def log_trade(
    symbol: str,
    side: str,
    quantity: float,
    entry_price: float,
    sl_price: float,
    tp_price: float,
    order_id: str,
    final_score: float,
    sl_order_id: str = "",
    tp_order_id: str = "",
) -> int:
    """שומר עסקה חדשה ל-DB. מחזיר את ה-id של הרשומה."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO trades
                (timestamp, symbol, side, quantity, price, sl_price, tp_price,
                 order_id, sl_order_id, tp_order_id, trail_peak, status, pnl)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.utcnow().isoformat(),
                symbol, side, quantity, entry_price,
                sl_price, tp_price, order_id,
                sl_order_id, tp_order_id,
                entry_price,  # trail_peak מתחיל במחיר הכניסה
                "open", 0.0,
            ),
        )
        conn.commit()
        return cur.lastrowid


def update_trailing_sl(
    trade_id: int,
    new_sl_price: float,
    new_sl_order_id: str,
    trail_peak: float,
) -> None:
    """מעדכן SL לאחר trailing stop."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            UPDATE trades
            SET sl_price = ?, sl_order_id = ?, trail_peak = ?
            WHERE id = ?
            """,
            (new_sl_price, new_sl_order_id, trail_peak, trade_id),
        )
        conn.commit()
    logger.debug(f"Trailing SL updated: trade={trade_id} new_sl={new_sl_price}")


def get_daily_pnl_pct() -> float:
    """
    מחשב P&L יומי כ-% מהתיק.
    לצורך circuit breaker ב-Risk Guard.
    מחזיר 0.0 אם אין נתונים מהיום.
    """
    today = datetime.utcnow().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT total_pnl FROM daily_summary WHERE date = ?
            """,
            (today,),
        ).fetchone()
    if row is None:
        return 0.0
    # daily_summary.total_pnl הוא ערך מוחלט בדולר
    # נחזיר כ-% שלילי (הפסד) - לוגיקה מלאה תתווסף עם position tracking
    return float(row[0])


def log_decision(
    symbol: str,
    timeframe: str,
    math_score: float,
    vision_score: float,
    sentiment_score: float,
    final_score: float,
    action: str,
    executed: bool,
    rejection_reason: str = "",
    entry_price: float | None = None,
    sl_price: float | None = None,
    tp_price: float | None = None,
    quantity: float | None = None,
    notional: float | None = None,
    risk_amount: float | None = None,
    math_reasoning: list | None = None,
    vision_reasoning: str = "",
    sentiment_summary: str = "",
    screenshot_file: str = "",
) -> int:
    """
    שומר כל החלטה - BUY / SELL / HOLD - עם נימוק מלא.
    מחזיר את ה-id של הרשומה שנשמרה.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO decisions (
                timestamp, symbol, timeframe,
                math_score, vision_score, sentiment_score, final_score,
                action, executed, rejection_reason,
                entry_price, sl_price, tp_price,
                quantity, notional, risk_amount,
                math_reasoning, vision_reasoning, sentiment_summary,
                screenshot_file
            ) VALUES (
                ?,?,?, ?,?,?,?, ?,?,?, ?,?,?, ?,?,?, ?,?,?,?
            )
            """,
            (
                datetime.utcnow().isoformat(),
                symbol, timeframe,
                math_score, vision_score, sentiment_score, final_score,
                action, int(executed), rejection_reason or "",
                entry_price, sl_price, tp_price,
                quantity, notional, risk_amount,
                json.dumps(math_reasoning or []),
                vision_reasoning or "",
                sentiment_summary or "",
                screenshot_file or "",
            ),
        )
        conn.commit()
        row_id = cur.lastrowid
    logger.debug(f"Decision logged: id={row_id} {symbol} {action} executed={executed}")
    return row_id


def record_outcome(
    trade_id: int,
    symbol: str,
    side: str,
    outcome: str,
    entry_price: float,
    close_price: float,
    sl_price: float,
    tp_price: float,
    pnl_usdt: float,
    pnl_pct: float,
    entry_time: str,
    close_time: str,
    duration_minutes: float,
    decision_id: int | None = None,
    math_score: float | None = None,
    vision_score: float | None = None,
    sentiment_score: float | None = None,
    final_score: float | None = None,
    fear_greed: int | None = None,
    regime: str | None = None,
) -> None:
    """Records a closed trade outcome — the core of the feedback loop."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO trade_outcomes (
                trade_id, decision_id, symbol, side, outcome,
                entry_price, close_price, sl_price, tp_price,
                pnl_usdt, pnl_pct, entry_time, close_time, duration_minutes,
                math_score, vision_score, sentiment_score, final_score,
                fear_greed, regime
            ) VALUES (?,?,?,?,?, ?,?,?,?, ?,?,?,?,?, ?,?,?,?,?,?)
            """,
            (
                trade_id, decision_id, symbol, side, outcome,
                entry_price, close_price, sl_price, tp_price,
                round(pnl_usdt, 4), round(pnl_pct, 4),
                entry_time, close_time, round(duration_minutes, 1),
                math_score, vision_score, sentiment_score, final_score,
                fear_greed, regime,
            ),
        )
        conn.commit()
    logger.debug(f"Outcome recorded: {symbol} {outcome} pnl={pnl_pct:+.2f}%")


def get_outcomes(
    symbol: str | None = None,
    days: int = 30,
    limit: int = 500,
) -> list[dict]:
    """Fetches recent closed trade outcomes for analysis."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if symbol:
            rows = conn.execute(
                """
                SELECT * FROM trade_outcomes
                WHERE symbol = ?
                  AND close_time >= datetime('now', ?)
                ORDER BY close_time DESC LIMIT ?
                """,
                (symbol, f"-{days} days", limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM trade_outcomes
                WHERE close_time >= datetime('now', ?)
                ORDER BY close_time DESC LIMIT ?
                """,
                (f"-{days} days", limit),
            ).fetchall()
    return [dict(r) for r in rows]


def get_win_rate_summary(days: int = 30) -> dict:
    """Quick win-rate stats for Telegram reports."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN outcome='TP_HIT' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN outcome='SL_HIT' THEN 1 ELSE 0 END) AS losses,
                AVG(pnl_pct)                                    AS avg_pnl_pct,
                SUM(pnl_usdt)                                   AS total_pnl_usdt,
                AVG(CASE WHEN outcome='TP_HIT' THEN pnl_pct END) AS avg_win_pct,
                AVG(CASE WHEN outcome='SL_HIT' THEN pnl_pct END) AS avg_loss_pct
            FROM trade_outcomes
            WHERE close_time >= datetime('now', ?)
            """,
            (f"-{days} days",),
        ).fetchone()
    if not row or not row[0]:
        return {}
    total = row[0] or 1
    return {
        "total": row[0],
        "wins": row[1] or 0,
        "losses": row[2] or 0,
        "win_rate": round((row[1] or 0) / total, 3),
        "avg_pnl_pct": round(row[3] or 0, 3),
        "total_pnl_usdt": round(row[4] or 0, 2),
        "avg_win_pct": round(row[5] or 0, 3),
        "avg_loss_pct": round(row[6] or 0, 3),
    }


def get_open_symbols_from_db() -> list[str]:
    """פוזיציות פתוחות לפי ה-DB — fallback כשאין Bybit API key."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT symbol FROM trades WHERE status = 'open'"
        ).fetchall()
    return [r[0] for r in rows]


def save_meta_suggestion(
    category: str,
    description: str,
    suggested_change: dict,
    attribution_data: dict | None = None,
    backtest_pf_before: float | None = None,
    backtest_pf_after: float | None = None,
    backtest_wr_before: float | None = None,
    backtest_wr_after: float | None = None,
    backtest_trades: int | None = None,
    validated: bool = False,
) -> int:
    """שומר הצעת MetaAgent עם תוצאות validation."""
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO meta_suggestions (
                created_at, category, description, suggested_change,
                attribution_data,
                backtest_pf_before, backtest_pf_after,
                backtest_wr_before, backtest_wr_after,
                backtest_trades, validated
            ) VALUES (?,?,?,?,?, ?,?,?,?,?,?)
            """,
            (
                datetime.utcnow().isoformat(),
                category, description,
                json.dumps(suggested_change),
                json.dumps(attribution_data or {}),
                backtest_pf_before, backtest_pf_after,
                backtest_wr_before, backtest_wr_after,
                backtest_trades,
                int(validated),
            ),
        )
        conn.commit()
        return cur.lastrowid


def get_meta_suggestions(limit: int = 50) -> list[dict]:
    """שולף הצעות MetaAgent אחרונות."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM meta_suggestions ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_decisions(symbol: str | None = None, limit: int = 50) -> list[dict]:
    """שולף החלטות אחרונות - לצפייה ולניתוח ביצועים."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if symbol:
            rows = conn.execute(
                "SELECT * FROM decisions WHERE symbol=? ORDER BY timestamp DESC LIMIT ?",
                (symbol, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM decisions ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_recent_analyses(symbol: str, limit: int = 10) -> list[dict]:
    """שולף ניתוחים אחרונים לצורך השוואה."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT * FROM analysis_log
            WHERE symbol = ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
    return [dict(r) for r in rows]
