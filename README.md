# CryptoTrader — Multi-Agent Crypto Trading System

A multi-agent system for automated cryptocurrency day trading, built around technical analysis, visual chart recognition, and historical backtesting.

**Goal:** 2–3 profitable trades per day on BTC/USDT and ETH/USDT futures.

---

## Table of Contents

1. [Architecture Overview](#architecture-overview)
2. [Agents](#agents)
3. [Setup](#setup)
4. [Configuration](#configuration)
5. [Running the Bot](#running-the-bot)
6. [Backtesting — Math Agent](#backtesting--math-agent)
7. [Backtesting — Vision Agent](#backtesting--vision-agent)
8. [Understanding Backtest Results](#understanding-backtest-results)
9. [Go-Live Threshold](#go-live-threshold)
10. [Roadmap](#roadmap)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                      scheduler.py                        │
│         (runs every 15 min + weekly review)             │
└──────────────────────┬──────────────────────────────────┘
                       │
           ┌───────────▼───────────┐
           │       main.py         │
           │  orchestrates agents  │
           └───────────┬───────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
  MathAgent      VisionAgent    SentimentAgent
  (indicators)  (chart image)  (Fear & Greed)
        │              │              │
        └──────────────┼──────────────┘
                       ▼
                Combined Score
                       │
              ┌────────▼────────┐
              │   Risk Guard     │
              │ (max loss, size) │
              └────────┬────────┘
                       ▼
              ExecutionAgent
              (Bybit futures)
                       │
              PositionMonitor
              (TP / SL / timeout)
                       │
                  Database
              (trade_outcomes)
                       │
              ReviewAgent (weekly)
              (performance report)
```

---

## Agents

### MathAgent (`agents/math_agent.py`)
Analyzes technical indicators on 15m and 1H candles and returns a score from **−10** (strongly bearish) to **+10** (strongly bullish).

Indicators used:
| Indicator | What it measures |
|---|---|
| EMA 20/50/200 | Trend direction |
| RSI 14 | Momentum / overbought-oversold |
| MACD | Momentum crossover |
| Bollinger Bands | Volatility / mean reversion |
| OBV | Volume confirmation |

Multi-timeframe: scores 1H and 15m separately, weighs 1H more heavily.

---

### VisionAgent (`agents/vision_agent.py`)
Sends a chart screenshot to **Claude Sonnet** via the Anthropic API and receives structured JSON analysis:
- Trend direction and strength
- Chart patterns (head & shoulders, flags, wedges, etc.)
- Support / resistance levels
- Candlestick signals
- EMA positioning
- Volume analysis
- `bias_score` (−10 to +10)
- `confidence` (0 to 1)

---

### LocalVisionAgent (`agents/local_vision_agent.py`)
Identical interface to `VisionAgent` but runs **locally via Ollama** (zero API cost). Used for backtesting all historical trades without Claude API charges.

Supported models:
| Model | VRAM | Quality |
|---|---|---|
| `llama3.2-vision:11b` | ~8 GB | Good |
| `qwen2-vl:7b` | ~6 GB | Good |
| `llava:13b` | ~10 GB | Medium+ |
| `moondream` | ~2 GB | Basic |

---

### SentimentAgent (`agents/sentiment_agent.py`)
Fetches the **Fear & Greed Index** (alternative.me) and converts it to a contrarian score.
- Extreme Fear (0–25) → bullish bias
- Extreme Greed (76–100) → bearish bias

---

### ReviewAgent (`agents/review_agent.py`)
Runs every **Monday 08:00 UTC**. Reads the last 7 days of trade outcomes from the database and generates a performance report with recommendations.

---

### ExecutionAgent (`agents/execution_agent.py`)
Places orders on **Bybit Futures** via CCXT. Supports paper trading mode (no real money).

---

## Setup

### Requirements
- Python 3.11+
- A Bybit account (futures enabled)
- An Anthropic API key (for live VisionAgent)
- Ollama installed (for vision backtesting — free)
- Telegram bot (optional, for notifications)

### Install

```bash
git clone https://github.com/roiag/cryptoTrader.git
cd cryptoTrader

python -m venv .venv
# Windows:
.venv\Scripts\activate
# Mac/Linux:
source .venv/bin/activate

pip install -r requirements.txt
```

### Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
# Bybit
BYBIT_API_KEY=your_key_here
BYBIT_SECRET=your_secret_here
PAPER_TRADING=true          # set to false for live trading

# Claude (for live VisionAgent)
ANTHROPIC_API_KEY=your_key_here

# Telegram (optional)
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id

# Risk
MAX_RISK_PER_TRADE=0.01     # 1% of account per trade
MAX_DAILY_LOSS=0.05         # stop trading after 5% daily loss
MAX_OPEN_POSITIONS=3
```

---

## Configuration

Main settings in `config/settings.py` (override via `.env`):

| Setting | Default | Description |
|---|---|---|
| `TIMEFRAME` | `15m` | Candle timeframe |
| `MAX_RISK_PER_TRADE` | `0.01` | 1% account risk per trade |
| `MAX_DAILY_LOSS` | `0.05` | Daily loss limit (5%) |
| `MAX_OPEN_POSITIONS` | `3` | Max simultaneous positions |
| `PAPER_TRADING` | `true` | Simulated trading (no real money) |

Trading pairs are defined in `config/pairs.py`.

---

## Running the Bot

### Single run (one analysis cycle)

```bash
python main.py
```

### Scheduled (runs every 15 minutes automatically)

```bash
python scheduler.py
```

The scheduler runs:
- **Every 15 minutes** — market analysis cycle
- **Monday 08:00 UTC** — weekly performance review

---

## Backtesting — Math Agent

Tests the math agent on **3 years of historical data** (2022–2025) without any lookahead bias.

### Quick start

```bash
# BTC/USDT, default settings
python run_backtest.py

# ETH/USDT
python run_backtest.py --symbol ETH/USDT

# Both BTC + ETH, export results
python run_backtest.py --both --export results.csv

# Custom threshold and date range
python run_backtest.py --threshold 6.0 --start 2023-01-01 --end 2025-01-01
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--symbol` | `BTC/USDT` | Trading pair |
| `--timeframe` | `15m` | Candle timeframe |
| `--start` | `2022-01-01` | Start date |
| `--end` | `2025-01-01` | End date |
| `--threshold` | `4.5` | Signal threshold (±) — only enter trades above this score |
| `--export` | _(none)_ | Save results to CSV |
| `--both` | _(flag)_ | Run BTC and ETH together |

### Key findings (3-year BTC + ETH run)

| Threshold | Trades/day | Win Rate | EV/trade | Profitable? |
|---|---|---|---|---|
| ±4.5 | ~4.5 | 30.0% | −0.02% | No |
| ±5.5 | ~1.8 | 31.4% | +0.00% | Breakeven |
| **±6.0** | **~0.6** | **30.4%** | **+0.02%** | **Yes** |

**Best conditions:** Extreme Fear (F&G 0–25) → 47% win rate on BTC.

> **Note:** Math agent alone at ±6.0 only generates ~0.6 trades/day.
> Adding vision agent as a filter allows lowering threshold to ±4.5–5.0
> while maintaining positive EV — enabling 2–3 trades/day.

### Data caching

OHLCV data and Fear & Greed history are automatically cached in `backtest/cache/` as Parquet files. Subsequent runs use the cache instantly — no re-downloading.

---

## Backtesting — Vision Agent

Tests whether the vision agent **adds predictive value** on top of math signals.

For each historical trade, the engine:
1. Slices the OHLCV data up to the entry timestamp (zero lookahead bias)
2. Renders a candlestick chart with EMA20, EMA50, and volume
3. Sends the chart image to a local Ollama vision model
4. Records whether vision agrees or disagrees with the math signal
5. Compares win rates: **agree vs. disagree vs. all**

### Setup — Ollama

```bash
# Install Ollama from https://ollama.com

# Pull a vision model (choose one)
ollama pull llama3.2-vision      # recommended, 8 GB VRAM
ollama pull qwen2-vl:7b          # good alternative, 6 GB VRAM
ollama pull moondream            # lightweight, 2 GB VRAM

# Start the server
ollama serve
```

### Run vision backtest

```bash
# Quick test — 100 trades (balanced: 50 wins + 50 losses)
python run_vision_backtest.py --sample 100

# Full run on all trades
python run_vision_backtest.py --input combined_results.csv

# Different model
python run_vision_backtest.py --model qwen2-vl:7b --sample 200

# Custom output file
python run_vision_backtest.py --sample 200 --output my_vision_results.csv
```

### Parameters

| Parameter | Default | Description |
|---|---|---|
| `--input` | `combined_results.csv` | CSV from math backtest |
| `--model` | `llama3.2-vision` | Ollama vision model |
| `--timeframe` | `15m` | Must match the cached OHLCV timeframe |
| `--sample` | _(all)_ | Analyze only N trades (balanced sampling) |
| `--output` | `<input>_vision.csv` | Output CSV path |
| `--delay` | `0.3` | Seconds between Ollama calls |

### Output columns (added to CSV)

| Column | Description |
|---|---|
| `vision_score` | Vision bias score (−10 to +10) |
| `vision_trend` | `uptrend` / `downtrend` / `sideways` |
| `vision_confidence` | Confidence 0.0–1.0 |
| `agreement` | `AGREE` / `DISAGREE` / `NEUTRAL` / `ERROR` |

### Interpreting results

The report shows:

```
קבוצה                          עסקאות   Win Rate   EV/trade
כל העסקאות                       200      30.0%     -0.02%
Math + Vision מסכימים  ✓          120      ???%      ???%
Math + Vision חלוקים   ✗           60      ???%      ???%
Vision ניטרלי                      20      ???%      ???%

Vision adds +X% WR when it agrees vs. disagrees.
✓ Vision IS a useful filter.   (or ✗ if not)
```

**If vision agrees → higher win rate:** use vision as a mandatory filter before entering trades.

**If vision disagrees → same or lower win rate:** vision model may not be suitable; try a different model or skip vision.

---

## Understanding Backtest Results

### Metrics explained

| Metric | Formula | Target |
|---|---|---|
| **Win Rate** | Wins / Total trades | > 40% |
| **Profit Factor** | Gross profit / Gross loss | > 1.3 |
| **EV/trade** | Average P&L per trade | > +0.05% |
| **Max Drawdown** | Worst peak-to-trough drop | < 20% |

### Why 30% win rate can still be profitable

It depends on the Risk:Reward ratio. The bot uses ATR-based SL/TP:
- SL = ATR × 1.5 (stop loss)
- TP = ATR × 3.0 (take profit, 2:1 R:R)

With 2:1 R:R, you need **only 34% win rate to break even**.
At 40% win rate with 2:1 R:R → profitable.

---

## Go-Live Threshold

The system graduates through three stages before risking real money:

### Stage 1 — Backtesting ✓ (current stage)
Validate on 3 years of historical data.

**Pass criteria:**
- Profit Factor > 1.3
- Win Rate > 40%
- EV/trade > +0.05%
- Max Drawdown < 20%
- Sample size > 300 trades

### Stage 2 — Paper Trading
Run the full system with real market data but virtual money (set `PAPER_TRADING=true`). Run for at least 1–2 months.

**Pass criteria:** Same metrics as Stage 1, but on live paper trading data.

### Stage 3 — Live Trading
Start with a small amount ($500–$1,000). Scale up gradually as performance is confirmed.

---

## Project Structure

```
cryptoTrader/
├── agents/
│   ├── math_agent.py          # Technical indicator scoring
│   ├── vision_agent.py        # Claude vision analysis (live)
│   ├── local_vision_agent.py  # Ollama vision analysis (backtesting)
│   ├── sentiment_agent.py     # Fear & Greed index
│   ├── execution_agent.py     # Order placement
│   └── review_agent.py        # Weekly performance review
├── backtest/
│   ├── data_loader.py         # Historical OHLCV + F&G downloader
│   ├── engine.py              # Math backtest engine
│   ├── vision_engine.py       # Vision backtest engine
│   ├── chart_renderer.py      # Candlestick chart generator
│   └── report.py              # Backtest report generator
├── config/
│   ├── settings.py            # All configuration (via .env)
│   └── pairs.py               # Trading pairs list
├── data/
│   ├── exchange.py            # Market data fetcher (OHLCV, funding rate, OI)
│   ├── indicators.py          # Technical indicator calculations
│   ├── news.py                # News fetcher
│   └── regime.py              # Market regime detector (TRENDING/RANGING/HIGH_VOL)
├── execution/
│   ├── broker.py              # Bybit order execution
│   └── position_monitor.py    # TP/SL monitoring + outcome recording
├── risk/
│   ├── guard.py               # Daily loss limit, position limits
│   └── position_sizer.py      # ATR-based position sizing
├── storage/
│   └── db.py                  # SQLite: trades, outcomes, win rate summary
├── notifications/
│   └── telegram.py            # Telegram alerts
├── capture/
│   └── screenshot.py          # Chart screenshot for live vision analysis
├── main.py                    # Main orchestration loop
├── scheduler.py               # APScheduler (15min cycles + weekly review)
├── run_backtest.py            # CLI: math backtest
├── run_vision_backtest.py     # CLI: vision backtest
├── requirements.txt
└── .env.example
```

---

## Roadmap

- [x] Math Agent with multi-timeframe confluence (1H + 15m)
- [x] Vision Agent (Claude Sonnet)
- [x] Local Vision Agent (Ollama — free backtesting)
- [x] Math Backtest Engine (3 years, 100k+ candles)
- [x] Vision Backtest Engine (chart renderer + Ollama)
- [x] Market Regime Detector (TRENDING / RANGING / HIGH_VOL)
- [x] Weekly Review Agent
- [x] Trade outcome database
- [ ] Combined Math + Vision backtest analysis
- [ ] Parameter optimization (threshold, SL/TP multipliers)
- [ ] Manager Agent (auto-tunes parameters based on performance)
- [ ] Paper trading validation
- [ ] Live trading with small capital

---

## Notes

- **Never trade with money you cannot afford to lose.** Crypto markets are volatile and no strategy is guaranteed.
- Always validate on paper trading before going live.
- The backtest uses `PAPER_TRADING=true` by default — no real orders are placed.
- API keys in `.env` are never committed to git (`.gitignore` excludes `.env`).
