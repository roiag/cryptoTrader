"""
ParameterOptimizer — מחפש את הפרמטרים האופטימליים ל-MathAgent.

גרסה מהירה לחלוטין (~30 שניות):
  • שלב 1: חשב final_score + ATR + price לכל בר כ-numpy arrays (פעם אחת)
  • שלב 2: לכל שילוב — numpy vectorized simulation (ללא Python loops)

שימוש:
    python run_optimizer.py
    from backtest.optimizer import ParameterOptimizer
    result = ParameterOptimizer().optimize("BTC/USDT", "15m", "2022-01-01", "2024-01-01")
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich import box

from agents.math_agent import MathAgent
from backtest.data_loader import DataLoader
from backtest.engine import fg_to_score
from data.indicators import calculate_all

console = Console()

THRESHOLD_GRID   = [3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0]
ATR_MUL_GRID     = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]
RR_RATIO_GRID    = [1.5, 2.0, 2.5, 3.0]
MIN_TRADES       = 20
LOOKBACK         = 210
MATH_WEIGHT      = 0.80
SENTIMENT_WEIGHT = 0.20
MAX_BARS_HELD    = 96
SLIPPAGE_PCT     = 0.0005


@dataclass
class OptimizeResult:
    symbol:      str
    timeframe:   str
    train_start: str
    train_end:   str
    best_params: dict
    best_pf:     float
    best_wr:     float
    best_trades: int
    search_results: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        p = self.best_params
        return (
            f"Best params for {self.symbol} [{self.timeframe}]:\n"
            f"  threshold={p['threshold']}  ATR_mul={p['ATR_SL_MULTIPLIER']}  RR={p['RR_RATIO']}\n"
            f"  PF={self.best_pf:.2f}  WR={self.best_wr:.1%}  trades={self.best_trades}"
        )


class ParameterOptimizer:

    def __init__(self) -> None:
        self._loader = DataLoader()

    def optimize(
        self,
        symbol:    str,
        timeframe: str,
        start:     str,
        end:       str,
        metric:    str = "profit_factor",
    ) -> OptimizeResult:
        logger.info(
            f"[Optimizer] {symbol} [{timeframe}] {start}→{end} | metric={metric}"
        )

        df    = self._loader.load_ohlcv(symbol, timeframe, start, end)
        fg_df = self._loader.load_fear_greed(start, end)
        df    = calculate_all(df)

        # ── שלב 1: חשב arrays פעם אחת ───────────────────────────────────────
        logger.info(f"[Optimizer] Pre-computing scores for {len(df):,} bars ...")
        finals, atrs, closes, highs, lows = self._precompute_arrays(df, fg_df)
        logger.info(
            f"[Optimizer] Done. Score range: [{finals.min():.2f}, {finals.max():.2f}]. "
            f"Testing {len(THRESHOLD_GRID)*len(ATR_MUL_GRID)*len(RR_RATIO_GRID)} combinations ..."
        )

        # ── שלב 2: Grid search ───────────────────────────────────────────────
        combinations = list(itertools.product(THRESHOLD_GRID, ATR_MUL_GRID, RR_RATIO_GRID))
        results: list[dict] = []
        best_score  = -999.0
        best_params = {"threshold": 4.5, "ATR_SL_MULTIPLIER": 1.5, "RR_RATIO": 2.0}
        best_wr     = 0.0
        best_n      = 0

        for idx, (thr, atr_mul, rr) in enumerate(combinations):
            if idx % 20 == 0:
                logger.info(f"[Optimizer] {idx}/{len(combinations)} combinations ...")

            pnls = self._simulate_vectorized(
                finals, atrs, closes, highs, lows, thr, atr_mul, rr
            )
            if len(pnls) < MIN_TRADES:
                continue

            wins  = pnls[pnls > 0]
            losses = pnls[pnls < 0]
            n     = len(pnls)
            wr    = len(wins) / n
            gw    = wins.sum()
            gl    = abs(losses.sum())
            pf    = gw / gl if gl > 0 else 99.0
            ev    = pnls.mean()

            row = {
                "threshold":         thr,
                "ATR_SL_MULTIPLIER": atr_mul,
                "RR_RATIO":          rr,
                "trades":            n,
                "win_rate":          round(wr, 3),
                "profit_factor":     round(pf, 3),
                "ev_per_trade":      round(ev, 4),
            }
            results.append(row)

            score = {"profit_factor": pf, "win_rate": wr, "ev_per_trade": ev}.get(metric, pf)
            if score > best_score:
                best_score  = score
                best_params = {"threshold": thr, "ATR_SL_MULTIPLIER": atr_mul, "RR_RATIO": rr}
                best_wr     = wr
                best_n      = n

        if not results:
            logger.warning("[Optimizer] No valid combinations found — returning defaults")
            return OptimizeResult(
                symbol=symbol, timeframe=timeframe,
                train_start=start, train_end=end,
                best_params=best_params,
                best_pf=0.0, best_wr=0.0, best_trades=0,
            )

        result = OptimizeResult(
            symbol=symbol, timeframe=timeframe,
            train_start=start, train_end=end,
            best_params=best_params,
            best_pf=round(best_score if metric == "profit_factor" else 0.0, 3),
            best_wr=round(best_wr, 3),
            best_trades=best_n,
            search_results=sorted(results, key=lambda r: r[metric], reverse=True),
        )

        logger.info(f"[Optimizer] Done. {result.summary()}")
        self._print_top(result)
        return result

    # ── Pre-compute arrays (numpy, fast) ──────────────────────────────────────

    def _precompute_arrays(
        self, df: pd.DataFrame, fg_df: pd.DataFrame
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """
        מחזיר 5 arrays באורך len(df):
          finals  — final_score לכל בר (math×0.8 + fg×0.2), nan לפני LOOKBACK
          atrs    — ATR
          closes  — close price
          highs   — high price
          lows    — low price
        """
        agent = object.__new__(MathAgent)
        n     = len(df)

        def col(name):
            if name in df.columns:
                a = df[name].to_numpy(dtype=float).copy()
                a[np.isnan(a)] = 0
                return a
            return np.zeros(n)

        price   = col("close")
        ema_20  = col("ema_20");  ema_50  = col("ema_50");  ema_200 = col("ema_200")
        rsi     = col("rsi")
        macd_l  = col("macd_line"); macd_s = col("macd_signal"); macd_h = col("macd_hist")
        bb_u    = col("bb_upper"); bb_m  = col("bb_mid");   bb_l   = col("bb_lower")
        atr_arr = col("atr")
        obv     = col("obv")
        vwap    = col("vwap")
        fib_h   = col("fib_high"); fib_l = col("fib_low")
        fib_236 = col("fib_0236"); fib_382 = col("fib_0382")
        fib_50  = col("fib_050");  fib_618 = col("fib_0618"); fib_786 = col("fib_0786")

        # FG lookup
        fg_ts  = fg_df.index.astype(np.int64).to_numpy()
        fg_val = fg_df["value"].to_numpy(dtype=int)
        df_ts  = df.index.astype(np.int64).to_numpy()

        finals = np.full(n, np.nan)

        for i in range(LOOKBACK, n):
            def s(a, i=i): v = a[i]; return None if v == 0 else float(v)

            snap = {
                "price": s(price), "ema_20": s(ema_20), "ema_50": s(ema_50), "ema_200": s(ema_200),
                "rsi": s(rsi), "macd_line": s(macd_l), "macd_signal": s(macd_s),
                "macd_hist": s(macd_h), "macd_hist_prev": float(macd_h[i-1]) if macd_h[i-1] != 0 else None,
                "bb_upper": s(bb_u), "bb_mid": s(bb_m), "bb_lower": s(bb_l), "bb_width": None,
                "atr": s(atr_arr), "obv": s(obv),
                "obv_prev_5": float(obv[i-5]) if i >= 5 and obv[i-5] != 0 else None,
                "vwap": float(vwap[i]) if vwap[i] != 0 else None,
                "fib_high": s(fib_h), "fib_low": s(fib_l),
                "fib_0236": s(fib_236), "fib_0382": s(fib_382),
                "fib_050": s(fib_50), "fib_0618": s(fib_618), "fib_0786": s(fib_786),
            }

            scores = {
                "trend":         agent._score_trend(snap),
                "momentum_rsi":  agent._score_rsi(snap),
                "momentum_macd": agent._score_macd(snap),
                "volatility_bb": agent._score_bollinger(snap),
                "volume_obv":    agent._score_obv(snap),
            }
            if snap["fib_high"]: scores["fibonacci"] = agent._score_fibonacci(snap)
            if snap["vwap"]:     scores["vwap"]      = agent._score_vwap(snap)

            math_score = agent._aggregate(scores)
            ig  = np.searchsorted(fg_ts, df_ts[i], side="right") - 1
            fgv = int(fg_val[ig]) if ig >= 0 else 50
            finals[i] = math_score * MATH_WEIGHT + fg_to_score(fgv) * SENTIMENT_WEIGHT

        highs_arr  = df["high"].to_numpy(dtype=float)
        lows_arr   = df["low"].to_numpy(dtype=float)

        return finals, atr_arr, price, highs_arr, lows_arr

    # ── Vectorized simulation ─────────────────────────────────────────────────

    def _simulate_vectorized(
        self,
        finals: np.ndarray,
        atrs:   np.ndarray,
        closes: np.ndarray,
        highs:  np.ndarray,
        lows:   np.ndarray,
        threshold:  float,
        atr_mul:    float,
        rr:         float,
    ) -> np.ndarray:
        """
        מדמה עסקאות — מהיר: Python loop רק על עסקאות בפועל, לא על כל הבארים.
        מחזיר numpy array של pnl_pct לכל עסקה שנסגרה (TP/SL בלבד).
        """
        n       = len(finals)
        pnls    = []
        next_i  = LOOKBACK   # הבר הראשון שניתן להיכנס בו

        # מצא signal bars מראש (מהיר)
        abs_f   = np.abs(finals)
        signal_bars = np.where(
            (abs_f >= threshold) & ~np.isnan(finals) & (np.arange(n) >= LOOKBACK)
        )[0]

        for i in signal_bars:
            if i < next_i:
                continue

            final  = finals[i]
            side   = "BUY" if final > 0 else "SELL"
            slip   = SLIPPAGE_PCT * (1 if side == "BUY" else -1)
            entry  = closes[i] * (1 + slip)
            atr_v  = atrs[i]
            if atr_v == 0 or entry == 0:
                continue

            sl_pct = (atr_v * atr_mul) / entry
            tp_pct = sl_pct * rr

            if side == "BUY":
                sl = entry * (1 - sl_pct)
                tp = entry * (1 + tp_pct)
            else:
                sl = entry * (1 + sl_pct)
                tp = entry * (1 - tp_pct)

            # סימולציה על numpy slices — מהיר
            end = min(i + 1 + MAX_BARS_HELD, n)
            fwd_h = highs[i+1:end]
            fwd_l = lows[i+1:end]

            if side == "BUY":
                sl_hit = np.where(fwd_l <= sl)[0]
                tp_hit = np.where(fwd_h >= tp)[0]
            else:
                sl_hit = np.where(fwd_h >= sl)[0]
                tp_hit = np.where(fwd_l <= tp)[0]

            first_sl = sl_hit[0] if len(sl_hit) else MAX_BARS_HELD + 1
            first_tp = tp_hit[0] if len(tp_hit) else MAX_BARS_HELD + 1

            if first_sl > MAX_BARS_HELD and first_tp > MAX_BARS_HELD:
                # Timeout — דלג על הבארים אבל אל תספור
                next_i = i + MAX_BARS_HELD + 1
                continue

            if first_sl <= first_tp:
                outcome = "SL_HIT"
                exit_p  = sl
            else:
                outcome = "TP_HIT"
                exit_p  = tp

            pnl = (exit_p - entry) / entry if side == "BUY" else (entry - exit_p) / entry
            pnls.append(pnl * 100)
            next_i = i + 1

        return np.array(pnls) if pnls else np.array([])

    def _print_top(self, result: OptimizeResult, top_n: int = 10) -> None:
        top = result.search_results[:top_n]
        table = Table(
            title=f"Top {top_n} — {result.symbol} [{result.timeframe}]",
            box=box.SIMPLE_HEAD,
        )
        table.add_column("Threshold",    justify="right")
        table.add_column("ATR Mul",      justify="right")
        table.add_column("RR",           justify="right")
        table.add_column("Trades",       justify="right")
        table.add_column("Win Rate",     justify="right")
        table.add_column("Prof. Factor", justify="right")
        table.add_column("EV/trade",     justify="right")

        for r in top:
            pf_c = "green" if r["profit_factor"] >= 1.0 else "red"
            wr_c = "green" if r["win_rate"]       >= 0.52 else "red"
            ev_c = "green" if r["ev_per_trade"]   >  0    else "red"
            table.add_row(
                f"±{r['threshold']}",
                str(r["ATR_SL_MULTIPLIER"]),
                str(r["RR_RATIO"]),
                str(r["trades"]),
                f"[{wr_c}]{r['win_rate']:.1%}[/{wr_c}]",
                f"[{pf_c}]{r['profit_factor']:.2f}[/{pf_c}]",
                f"[{ev_c}]{r['ev_per_trade']:+.3f}%[/{ev_c}]",
            )

        console.print(table)
