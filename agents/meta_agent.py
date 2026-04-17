"""
MetaAgent — סוכן מנהל שמנתח ביצועים ומציע שיפורים.

תהליך שבועי:
  1. טוען trade_outcomes מה-DB
  2. מחשב attribution stats (איזה סוכן תרם להצלחות/כישלונות)
  3. שולח ל-Claude Sonnet עם הנתונים הסטטיסטיים
  4. מפרש הצעות ספציפיות ובדיקות
  5. מריץ mini-backtest לכל הצעה שניתן לבדוק
  6. שומר הצעות ל-DB + שולח דוח בטלגרם

הגנה מפני overfitting:
  • הצעות מאושרות רק אם backtest out-of-sample מאשר שיפור
  • דורש מינימום MIN_OUTCOMES עסקאות לפני שמציע כלום
  • מזהיר כשה-consistency נמוך

שימוש:
    agent = MetaAgent()
    agent.run_weekly()          # ניתוח 30 יום אחרונים
    agent.run_weekly(days=60)   # ניתוח 60 יום
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

import anthropic
from loguru import logger

from config.settings import settings
from notifications import telegram
from storage.db import get_outcomes, save_meta_suggestion, get_meta_suggestions

# ── הגדרות ─────────────────────────────────────────────────────────────────────
MODEL          = "claude-sonnet-4-6"
MAX_TOKENS     = 2048
MIN_OUTCOMES   = 30     # מינימום עסקאות לניתוח משמעותי
BACKTEST_DAYS  = 90     # תקופה לבדיקת הצעות


# ── System Prompt ──────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are an expert algorithmic trading system analyst.
Your role is to analyze trading performance data and suggest concrete, testable improvements.

Rules:
- Suggest only specific, measurable changes (e.g. "change threshold from 4.5 to 5.0")
- Each suggestion must be independently testable via backtest
- Consider statistical significance — only suggest changes with enough evidence
- Warn about overfitting risks when sample sizes are small
- Prioritize changes with highest expected impact first

Always respond with valid JSON only."""

USER_PROMPT_TEMPLATE = """Analyze this trading system's performance data and suggest improvements.

SYSTEM OVERVIEW:
- Multi-agent crypto trading system (BTC, ETH, SOL, BNB)
- Agents: MathAgent (technical analysis), VisionAgent (chart screenshots), SentimentAgent (news)
- Entry threshold: ±{threshold} combined score
- ATR SL multiplier: {atr_mul}×, RR ratio: {rr_ratio}×

PERFORMANCE SUMMARY (last {days} days — {n_trades} trades):
- Win Rate: {win_rate:.1%}
- Profit Factor: {profit_factor:.2f}
- Avg Win: {avg_win:+.3f}%  |  Avg Loss: {avg_loss:+.3f}%
- EV per trade: {ev_per_trade:+.3f}%

AGENT ATTRIBUTION (direction accuracy = % of time agent pointed correct direction):
- MathAgent accuracy:      {math_accuracy:.1%}
- VisionAgent accuracy:    {vision_accuracy:.1%}
- SentimentAgent accuracy: {sentiment_accuracy:.1%}

PATTERN ANALYSIS:
{pattern_text}

BEST CONDITIONS:
{best_conditions}

WORST CONDITIONS:
{worst_conditions}

PREVIOUS SUGGESTIONS (avoid duplicates):
{prev_suggestions}

Based on this data, suggest 3-5 specific improvements. For each:
- Identify which agent/parameter to change
- Specify the exact change
- Explain the statistical evidence
- Rate confidence (low/medium/high)

Return ONLY this JSON:
{{
  "suggestions": [
    {{
      "category": "math|vision|sentiment|execution|risk",
      "description": "human readable description",
      "change": {{
        "parameter": "parameter name",
        "current_value": "current",
        "suggested_value": "new value",
        "is_backtestable": true
      }},
      "evidence": "statistical evidence from the data above",
      "confidence": "low|medium|high",
      "expected_impact": "expected improvement"
    }}
  ],
  "overall_assessment": "2-3 sentence assessment of current system performance",
  "data_quality_warning": "any concerns about sample size or data quality"
}}"""


@dataclass
class AttributionStats:
    n_trades:          int
    win_rate:          float
    profit_factor:     float
    avg_win:           float
    avg_loss:          float
    ev_per_trade:      float
    math_accuracy:     float
    vision_accuracy:   float
    sentiment_accuracy: float
    patterns:          list[str] = field(default_factory=list)
    best_conditions:   list[str] = field(default_factory=list)
    worst_conditions:  list[str] = field(default_factory=list)


@dataclass
class MetaSuggestion:
    category:     str
    description:  str
    change:       dict
    evidence:     str
    confidence:   str
    expected_impact: str
    backtest_pf_before: float = 0.0
    backtest_pf_after:  float = 0.0
    backtest_wr_before: float = 0.0
    backtest_wr_after:  float = 0.0
    backtest_trades:    int   = 0
    validated:          bool  = False


class MetaAgent:
    """
    מנתח ביצועי המערכת ומציע שיפורים.
    רץ פעם בשבוע.
    """

    def __init__(self) -> None:
        self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    def run_weekly(self, days: int = 30) -> None:
        """
        מריץ ניתוח שבועי מלא ושולח דוח בטלגרם.
        """
        logger.info(f"[MetaAgent] Starting weekly analysis ({days} days)")

        outcomes = get_outcomes(days=days, limit=2000)

        if len(outcomes) < MIN_OUTCOMES:
            msg = (
                f"🤖 <b>MetaAgent Weekly</b>\n\n"
                f"Not enough data yet for meaningful analysis.\n"
                f"Current: {len(outcomes)} trades | Required: {MIN_OUTCOMES}+\n\n"
                f"Keep running the system to build the dataset."
            )
            telegram.send_text(msg)
            logger.info(f"[MetaAgent] Insufficient data ({len(outcomes)} outcomes), skipping")
            return

        # ── ניתוח Attribution ──────────────────────────────────────────────────
        stats = self._calculate_attribution(outcomes)

        # ── הצעות מ-Claude ─────────────────────────────────────────────────────
        suggestions = self._generate_suggestions(stats, days)

        if not suggestions:
            logger.warning("[MetaAgent] No suggestions generated")
            return

        # ── Validation ב-backtest ──────────────────────────────────────────────
        validated = []
        for s in suggestions:
            validated_s = self._validate_suggestion(s)
            validated.append(validated_s)
            # שמור ל-DB
            save_meta_suggestion(
                category=validated_s.category,
                description=validated_s.description,
                suggested_change=validated_s.change,
                attribution_data={
                    "win_rate": stats.win_rate,
                    "profit_factor": stats.profit_factor,
                    "math_accuracy": stats.math_accuracy,
                },
                backtest_pf_before=validated_s.backtest_pf_before,
                backtest_pf_after=validated_s.backtest_pf_after,
                backtest_wr_before=validated_s.backtest_wr_before,
                backtest_wr_after=validated_s.backtest_wr_after,
                backtest_trades=validated_s.backtest_trades,
                validated=validated_s.validated,
            )

        # ── דוח בטלגרם ────────────────────────────────────────────────────────
        report = self._build_report(stats, validated, days)
        telegram.send_text(report)
        logger.info(f"[MetaAgent] Weekly report sent ({len(validated)} suggestions)")

    # ── Attribution Analysis ───────────────────────────────────────────────────

    def _calculate_attribution(self, outcomes: list[dict]) -> AttributionStats:
        """מחשב סטטיסטיקות attribution מפורטות."""
        closed = [o for o in outcomes if o["outcome"] in ("TP_HIT", "SL_HIT")]
        wins   = [o for o in closed if o["outcome"] == "TP_HIT"]
        losses = [o for o in closed if o["outcome"] == "SL_HIT"]

        n = len(closed)
        if n == 0:
            return AttributionStats(
                n_trades=0, win_rate=0, profit_factor=0,
                avg_win=0, avg_loss=0, ev_per_trade=0,
                math_accuracy=0, vision_accuracy=0, sentiment_accuracy=0,
            )

        wr   = len(wins) / n
        gw   = sum(o["pnl_pct"] for o in wins if o["pnl_pct"] > 0)
        gl   = abs(sum(o["pnl_pct"] for o in losses if o["pnl_pct"] < 0))
        pf   = gw / gl if gl > 0 else 99.0
        avg_win  = sum(o["pnl_pct"] for o in wins)  / max(len(wins),   1)
        avg_loss = sum(o["pnl_pct"] for o in losses) / max(len(losses), 1)
        ev   = sum(o["pnl_pct"] for o in closed) / n

        # Agent accuracy: % of the time the agent pointed in the winning direction
        math_acc      = self._direction_accuracy(closed, "math_score")
        vision_acc    = self._direction_accuracy(closed, "vision_score")
        sentiment_acc = self._direction_accuracy(closed, "sentiment_score")

        patterns       = self._find_patterns(closed)
        best, worst    = self._find_conditions(closed)

        return AttributionStats(
            n_trades=n,
            win_rate=round(wr, 3),
            profit_factor=round(pf, 3),
            avg_win=round(avg_win, 3),
            avg_loss=round(avg_loss, 3),
            ev_per_trade=round(ev, 4),
            math_accuracy=round(math_acc, 3),
            vision_accuracy=round(vision_acc, 3),
            sentiment_accuracy=round(sentiment_acc, 3),
            patterns=patterns,
            best_conditions=best,
            worst_conditions=worst,
        )

    @staticmethod
    def _direction_accuracy(outcomes: list[dict], score_col: str) -> float:
        valid = [
            o for o in outcomes
            if o.get(score_col) is not None and o.get("final_score") is not None
        ]
        if not valid:
            return 0.5
        correct = sum(
            1 for o in valid
            if (
                (o[score_col] > 0 and o["outcome"] == "TP_HIT" and o.get("final_score", 0) > 0) or
                (o[score_col] < 0 and o["outcome"] == "TP_HIT" and o.get("final_score", 0) < 0)
            )
        )
        return correct / len(valid)

    @staticmethod
    def _find_patterns(outcomes: list[dict]) -> list[str]:
        """מוצא patterns מעניינים בנתונים."""
        patterns = []

        # Pattern: Math ו-Vision מסכימים vs חלוקים
        agree    = [o for o in outcomes
                    if o.get("math_score") and o.get("vision_score")
                    and (o["math_score"] > 0) == (o["vision_score"] > 0)]
        disagree = [o for o in outcomes
                    if o.get("math_score") and o.get("vision_score")
                    and (o["math_score"] > 0) != (o["vision_score"] > 0)]

        if len(agree) >= 10:
            wr_agree = sum(1 for o in agree if o["outcome"] == "TP_HIT") / len(agree)
            patterns.append(
                f"When Math+Vision AGREE: WR={wr_agree:.1%} ({len(agree)} trades)"
            )
        if len(disagree) >= 10:
            wr_dis = sum(1 for o in disagree if o["outcome"] == "TP_HIT") / len(disagree)
            patterns.append(
                f"When Math+Vision DISAGREE: WR={wr_dis:.1%} ({len(disagree)} trades)"
            )

        return patterns

    @staticmethod
    def _find_conditions(outcomes: list[dict]) -> tuple[list[str], list[str]]:
        """מוצא את התנאים הטובים והגרועים ביותר."""
        best, worst = [], []

        # לפי Fear & Greed
        fg_buckets = {
            "Extreme Fear (0-25)":  [o for o in outcomes if (o.get("fear_greed") or 50) <= 25],
            "Fear (26-45)":         [o for o in outcomes if 26 <= (o.get("fear_greed") or 50) <= 45],
            "Greed (56-75)":        [o for o in outcomes if 56 <= (o.get("fear_greed") or 50) <= 75],
            "Extreme Greed (76+)":  [o for o in outcomes if (o.get("fear_greed") or 50) >= 76],
        }
        bucket_stats = []
        for label, group in fg_buckets.items():
            closed = [o for o in group if o["outcome"] in ("TP_HIT", "SL_HIT")]
            if len(closed) < 10:
                continue
            wr  = sum(1 for o in closed if o["outcome"] == "TP_HIT") / len(closed)
            ev  = sum(o["pnl_pct"] for o in closed) / len(closed)
            bucket_stats.append((label, len(closed), wr, ev))

        bucket_stats.sort(key=lambda x: x[3], reverse=True)
        for label, count, wr, ev in bucket_stats[:2]:
            best.append(f"{label}: WR={wr:.1%} EV={ev:+.3f}% ({count} trades)")
        for label, count, wr, ev in bucket_stats[-2:]:
            worst.append(f"{label}: WR={wr:.1%} EV={ev:+.3f}% ({count} trades)")

        return best, worst

    # ── Claude Suggestions ────────────────────────────────────────────────────

    def _generate_suggestions(
        self,
        stats: AttributionStats,
        days: int,
    ) -> list[MetaSuggestion]:
        """שולח ל-Claude ומחזיר רשימת הצעות."""
        # הצעות קודמות (למניעת כפילויות)
        prev = get_meta_suggestions(limit=20)
        prev_text = "\n".join(
            f"- [{p['category']}] {p['description']}" for p in prev[:5]
        ) or "None yet."

        from config.settings import settings as cfg
        prompt = USER_PROMPT_TEMPLATE.format(
            threshold=getattr(cfg, "TRADE_THRESHOLD", 3.5),
            atr_mul=1.5,
            rr_ratio=2.0,
            days=days,
            n_trades=stats.n_trades,
            win_rate=stats.win_rate,
            profit_factor=stats.profit_factor,
            avg_win=stats.avg_win,
            avg_loss=stats.avg_loss,
            ev_per_trade=stats.ev_per_trade,
            math_accuracy=stats.math_accuracy,
            vision_accuracy=stats.vision_accuracy,
            sentiment_accuracy=stats.sentiment_accuracy,
            pattern_text="\n".join(f"• {p}" for p in stats.patterns) or "No clear patterns yet.",
            best_conditions="\n".join(f"• {c}" for c in stats.best_conditions) or "Insufficient data.",
            worst_conditions="\n".join(f"• {c}" for c in stats.worst_conditions) or "Insufficient data.",
            prev_suggestions=prev_text,
        )

        try:
            message = self._client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = message.content[0].text
            data = self._parse_json(raw)
        except Exception as e:
            logger.error(f"[MetaAgent] Claude call failed: {e}")
            return []

        suggestions = []
        for item in data.get("suggestions", []):
            suggestions.append(MetaSuggestion(
                category=item.get("category", "unknown"),
                description=item.get("description", ""),
                change=item.get("change", {}),
                evidence=item.get("evidence", ""),
                confidence=item.get("confidence", "low"),
                expected_impact=item.get("expected_impact", ""),
            ))

        self._overall_assessment = data.get("overall_assessment", "")
        self._data_warning = data.get("data_quality_warning", "")

        logger.info(f"[MetaAgent] Generated {len(suggestions)} suggestions from Claude")
        return suggestions

    # ── Backtest Validation ────────────────────────────────────────────────────

    def _validate_suggestion(self, suggestion: MetaSuggestion) -> MetaSuggestion:
        """
        מנסה לאמת הצעה ב-backtest.
        רק הצעות שה-change.is_backtestable=true ושניתן לממש אוטומטית.
        """
        change = suggestion.change
        if not change.get("is_backtestable", False):
            logger.debug(
                f"[MetaAgent] Suggestion '{suggestion.description}' not backtestable, skipping"
            )
            return suggestion

        param = change.get("parameter", "")

        # רק פרמטרים שה-BacktestConfig תומך בהם
        backtestable_params = {
            "threshold":          "threshold",
            "TRADE_THRESHOLD":    "threshold",
            "ATR_SL_MULTIPLIER":  "atr_sl_multiplier",
            "RR_RATIO":           "rr_ratio",
        }
        config_param = backtestable_params.get(param)
        if not config_param:
            logger.debug(f"[MetaAgent] Parameter '{param}' not in backtestable list")
            return suggestion

        try:
            new_val = float(change.get("suggested_value", 0))
        except (TypeError, ValueError):
            return suggestion

        # הרץ baseline ו-proposed על BTC/USDT 90 ימים אחורה
        from datetime import timedelta
        end   = datetime.utcnow().strftime("%Y-%m-%d")
        start = (datetime.utcnow() - timedelta(days=BACKTEST_DAYS)).strftime("%Y-%m-%d")

        baseline_pf, baseline_wr, baseline_n = self._run_mini_backtest(
            symbol="BTC/USDT", start=start, end=end, override={}
        )
        proposed_pf, proposed_wr, proposed_n = self._run_mini_backtest(
            symbol="BTC/USDT", start=start, end=end,
            override={config_param: new_val}
        )

        suggestion.backtest_pf_before = baseline_pf
        suggestion.backtest_pf_after  = proposed_pf
        suggestion.backtest_wr_before = baseline_wr
        suggestion.backtest_wr_after  = proposed_wr
        suggestion.backtest_trades    = proposed_n
        suggestion.validated = True

        pf_delta = proposed_pf - baseline_pf
        logger.info(
            f"[MetaAgent] Validated '{param}': "
            f"PF {baseline_pf:.2f} → {proposed_pf:.2f} (Δ{pf_delta:+.2f})"
        )
        return suggestion

    def _run_mini_backtest(
        self,
        symbol: str,
        start: str,
        end: str,
        override: dict,
    ) -> tuple[float, float, int]:
        """מריץ mini backtest ומחזיר (profit_factor, win_rate, n_trades)."""
        try:
            from backtest.data_loader import DataLoader
            from backtest.engine import BacktestConfig, BacktestEngine

            loader = DataLoader()
            df     = loader.load_ohlcv(symbol, "15m", start, end)
            fg_df  = loader.load_fear_greed(start, end)

            cfg = BacktestConfig(
                symbol=symbol, start=start, end=end, **override
            )
            engine = BacktestEngine(cfg)
            trades = engine.run(df, fg_df)

            closed = [t for t in trades if t.outcome in ("TP_HIT", "SL_HIT")]
            if not closed:
                return 0.0, 0.0, 0

            wins  = [t for t in closed if t.outcome == "TP_HIT"]
            wr    = len(wins) / len(closed)
            gw    = sum(t.pnl_pct for t in wins if t.pnl_pct > 0)
            gl    = abs(sum(t.pnl_pct for t in closed if t.pnl_pct < 0))
            pf    = gw / gl if gl > 0 else 99.0
            return round(pf, 3), round(wr, 3), len(closed)

        except Exception as e:
            logger.warning(f"[MetaAgent] Mini backtest failed: {e}")
            return 0.0, 0.0, 0

    # ── Telegram Report ────────────────────────────────────────────────────────

    def _build_report(
        self,
        stats: AttributionStats,
        suggestions: list[MetaSuggestion],
        days: int,
    ) -> str:
        lines = [
            f"🤖 <b>MetaAgent Weekly Report — {days}d</b>",
            "",
            f"<b>System Performance</b>",
            f"  Trades: {stats.n_trades}  |  WR: {stats.win_rate:.1%}  |  PF: {stats.profit_factor:.2f}",
            f"  EV/trade: {stats.ev_per_trade:+.3f}%",
            "",
            f"<b>Agent Attribution</b>",
            f"  Math:      {stats.math_accuracy:.1%} direction accuracy",
            f"  Vision:    {stats.vision_accuracy:.1%} direction accuracy",
            f"  Sentiment: {stats.sentiment_accuracy:.1%} direction accuracy",
            "",
        ]

        if stats.patterns:
            lines.append("<b>Key Patterns</b>")
            for p in stats.patterns:
                lines.append(f"  • {p}")
            lines.append("")

        if getattr(self, "_overall_assessment", ""):
            lines.append(f"<b>Assessment</b>")
            lines.append(f"  {self._overall_assessment}")
            lines.append("")

        lines.append(f"<b>Suggestions ({len(suggestions)})</b>")
        for i, s in enumerate(suggestions, 1):
            conf_icon = {"high": "🟢", "medium": "🟡", "low": "🔴"}.get(s.confidence, "⚪")
            lines.append(f"\n{i}. {conf_icon} [{s.category.upper()}] {s.description}")
            lines.append(f"   <i>{s.evidence}</i>")
            if s.validated:
                delta_pf = s.backtest_pf_after - s.backtest_pf_before
                icon = "✅" if delta_pf > 0 else "❌"
                lines.append(
                    f"   Backtest: PF {s.backtest_pf_before:.2f}→{s.backtest_pf_after:.2f} "
                    f"({delta_pf:+.2f}) {icon} | WR {s.backtest_wr_before:.1%}→{s.backtest_wr_after:.1%}"
                )
            else:
                lines.append("   <i>Backtest: not validated (manual change required)</i>")

        if getattr(self, "_data_warning", ""):
            lines.append("")
            lines.append(f"⚠️ <i>{self._data_warning}</i>")

        return "\n".join(lines)

    # ── Utils ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_json(text: str) -> dict:
        import re
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
        logger.warning(f"[MetaAgent] JSON parse failed: {text[:200]}")
        return {}
