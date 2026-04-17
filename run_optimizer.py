"""
הרצת אופטימייזר — נקודת כניסה.

שימוש:
    python run_optimizer.py
    python run_optimizer.py --symbol ETH/USDT
    python run_optimizer.py --start 2023-01-01 --end 2025-01-01
"""

import argparse
from backtest.optimizer import ParameterOptimizer

parser = argparse.ArgumentParser(description="Parameter Optimizer")
parser.add_argument("--symbol",    default="BTC/USDT")
parser.add_argument("--timeframe", default="15m")
parser.add_argument("--start",     default="2022-01-01")
parser.add_argument("--end",       default="2024-01-01")
parser.add_argument("--metric",    default="profit_factor",
                    choices=["profit_factor", "win_rate", "ev_per_trade"])
args = parser.parse_args()

result = ParameterOptimizer().optimize(
    args.symbol, args.timeframe, args.start, args.end, metric=args.metric
)

print()
print("=" * 50)
print("BEST PARAMS")
print("=" * 50)
print(result.summary())
