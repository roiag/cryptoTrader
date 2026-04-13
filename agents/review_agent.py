"""
Weekly Performance Review Agent — analyses closed trade outcomes and
sends a structured Telegram report.

Runs once per week (wired into scheduler.py).
Does NOT auto-adjust weights — it reports findings so the human can decide.

Report sections:
  1. Overall stats (win rate, P&L, profit factor)
  2. Best / worst conditions (F&G level, hour of day)
  3. Agent score correlation (which agent predicted wins best)
  4. Regime breakdown (how each market regime performed)
  5. Recommendation (threshold suggestion based on data)
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

from loguru import logger

from notifications import telegram
from storage.db import DB_PATH, get_win_rate_summary, get_outcomes


class ReviewAgent:

    def run(self, days: int = 7) -> None:
        """Generates and sends the weekly review to Telegram."""
        logger.info(f"[ReviewAgent] Generating {days}-day performance review")

        outcomes = get_outcomes(days=days, limit=1000)
        if not outcomes:
            telegram.send_text(
                f"📊 <b>Weekly Review ({days}d)</b>\n\n"
                "No closed trades in this period yet.\n"
                "Keep running paper trading to build the dataset."
            )
            return

        report = self._build_report(outcomes, days)
        telegram.send_text(report)
        logger.info("[ReviewAgent] Review sent to Telegram")

    # ── Report builder ─────────────────────────────────────────────────────────

    def _build_report(self, outcomes: list[dict], days: int) -> str:
        closed = [o for o in outcomes if o["outcome"] in ("TP_HIT", "SL_HIT")]
        wins   = [o for o in closed if o["outcome"] == "TP_HIT"]
        losses = [o for o in closed if o["outcome"] == "SL_HIT"]

        n = len(closed)
        if n == 0:
            return f"📊 <b>Weekly Review ({days}d)</b>\n\nNo TP/SL outcomes yet."

        wr        = len(wins) / n
        total_pnl = sum(o["pnl_pct"] for o in closed)
        avg_win   = sum(o["pnl_pct"] for o in wins)   / max(len(wins),   1)
        avg_loss  = sum(o["pnl_pct"] for o in losses) / max(len(losses), 1)
        gw        = sum(o["pnl_pct"] for o in wins   if o["pnl_pct"] > 0)
        gl        = abs(sum(o["pnl_pct"] for o in losses if o["pnl_pct"] < 0))
        pf        = round(gw / gl, 2) if gl > 0 else 99.0

        wr_icon  = "✅" if wr >= 0.53 else ("⚠️" if wr >= 0.47 else "❌")
        pnl_icon = "📈" if total_pnl > 0 else "📉"

        lines = [
            f"📊 <b>Weekly Review — last {days} days</b>",
            "",
            f"<b>Overall</b>",
            f"  Trades: {n}  (W:{len(wins)} L:{len(losses)})",
            f"  Win Rate: {wr:.1%} {wr_icon}",
            f"  Profit Factor: {pf}",
            f"  Total P&L: {total_pnl:+.2f}% {pnl_icon}",
            f"  Avg Win: {avg_win:+.2f}%   Avg Loss: {avg_loss:+.2f}%",
            "",
        ]

        # ── Best/worst by Fear & Greed ─────────────────────────────────────────
        fg_buckets = self._bucket_by(closed, self._fg_bucket)
        lines.append("<b>Fear & Greed breakdown</b>")
        for label, group in sorted(fg_buckets.items()):
            wr_b = sum(1 for o in group if o["outcome"] == "TP_HIT") / len(group)
            ev_b = sum(o["pnl_pct"] for o in group) / len(group)
            lines.append(f"  {label}: {len(group)} trades  WR {wr_b:.0%}  EV {ev_b:+.2f}%")
        lines.append("")

        # ── By hour of day ─────────────────────────────────────────────────────
        hour_buckets = self._bucket_by(closed, self._hour_bucket)
        lines.append("<b>Best trading hours (UTC)</b>")
        best_hours = sorted(hour_buckets.items(),
                            key=lambda kv: sum(o["pnl_pct"] for o in kv[1]) / len(kv[1]),
                            reverse=True)[:3]
        for label, group in best_hours:
            wr_b = sum(1 for o in group if o["outcome"] == "TP_HIT") / len(group)
            ev_b = sum(o["pnl_pct"] for o in group) / len(group)
            lines.append(f"  {label}: {len(group)} trades  WR {wr_b:.0%}  EV {ev_b:+.2f}%")
        lines.append("")

        # ── Agent correlation ──────────────────────────────────────────────────
        lines.append("<b>Agent score correlation with wins</b>")
        for agent, col in [("Math", "math_score"), ("Vision", "vision_score"), ("Sentiment", "sentiment_score")]:
            corr = self._direction_accuracy(closed, col)
            if corr is not None:
                lines.append(f"  {agent}: {corr:.0%} of signals pointed correct direction")
        lines.append("")

        # ── Threshold suggestion ───────────────────────────────────────────────
        best_thr, best_ev = self._suggest_threshold(closed)
        lines.append("<b>Threshold recommendation</b>")
        lines.append(f"  Best threshold: ±{best_thr}  →  EV/trade {best_ev:+.2f}%")
        lines.append(f"  (current default: ±4.5)")
        lines.append("")
        lines.append(f"<i>Based on {n} trades over {days} days</i>")

        return "\n".join(lines)

    # ── Analysis helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _fg_bucket(o: dict) -> str:
        v = o.get("fear_greed") or 50
        if v <= 25:   return "Extreme Fear (0-25)"
        if v <= 45:   return "Fear (26-45)"
        if v <= 55:   return "Neutral (46-55)"
        if v <= 75:   return "Greed (56-75)"
        return "Extreme Greed (76+)"

    @staticmethod
    def _hour_bucket(o: dict) -> str:
        ts = o.get("entry_time") or ""
        try:
            hour = datetime.fromisoformat(ts).hour
        except Exception:
            return "Unknown"
        if hour < 6:   return "00-06 UTC"
        if hour < 12:  return "06-12 UTC"
        if hour < 18:  return "12-18 UTC"
        return "18-24 UTC"

    @staticmethod
    def _bucket_by(outcomes: list[dict], key_fn) -> dict[str, list]:
        buckets: dict[str, list] = defaultdict(list)
        for o in outcomes:
            buckets[key_fn(o)].append(o)
        return dict(buckets)

    @staticmethod
    def _direction_accuracy(outcomes: list[dict], score_col: str) -> float | None:
        valid = [o for o in outcomes if o.get(score_col) is not None and o.get("final_score") is not None]
        if not valid:
            return None
        correct = sum(
            1 for o in valid
            if (o[score_col] > 0 and o["outcome"] == "TP_HIT" and o.get("final_score", 0) > 0) or
               (o[score_col] < 0 and o["outcome"] == "TP_HIT" and o.get("final_score", 0) < 0)
        )
        return correct / len(valid)

    @staticmethod
    def _suggest_threshold(outcomes: list[dict]) -> tuple[float, float]:
        best_thr, best_ev = 4.5, -999.0
        for thr in [3.5, 4.0, 4.5, 5.0, 5.5, 6.0]:
            group = [o for o in outcomes if abs(o.get("final_score") or 0) >= thr]
            if len(group) < 5:
                continue
            ev = sum(o["pnl_pct"] for o in group) / len(group)
            if ev > best_ev:
                best_ev, best_thr = ev, thr
        return best_thr, best_ev
