"""
הגדרות גרף TradingView - URLs וסימבולים.
"""

# המרה מפורמט ccxt לפורמט TradingView
SYMBOL_MAP: dict[str, str] = {
    "BTC/USDT": "BYBIT:BTCUSDT.P",   # Bybit perpetual
    "ETH/USDT": "BYBIT:ETHUSDT.P",
    "SOL/USDT": "BYBIT:SOLUSDT.P",
    "BNB/USDT": "BYBIT:BNBUSDT.P",
}

# המרה מפורמט ccxt לפורמט TradingView interval
TIMEFRAME_MAP: dict[str, str] = {
    "1m":  "1",
    "3m":  "3",
    "5m":  "5",
    "15m": "15",
    "30m": "30",
    "1h":  "60",
    "2h":  "120",
    "4h":  "240",
    "1d":  "D",
    "1w":  "W",
}

# תצורת גרף - EMA 20/50/200 + Volume
# study_1=...&study_2=... מוסיפים אינדיקטורים ישירות ל-URL
_STUDIES = (
    "STD;EMA"   # EMA - נוסיף 3 פעמים עם ערכים שונים
)

def build_chart_url(symbol: str, timeframe: str) -> str:
    """
    בונה URL של TradingView לגרף נקי עם EMA20/50/200.
    דוגמה:
      symbol="BTC/USDT", timeframe="15m"
      → https://www.tradingview.com/chart/?symbol=BYBIT:BTCUSDT.P&interval=15&...
    """
    tv_symbol   = SYMBOL_MAP.get(symbol, symbol.replace("/", ""))
    tv_interval = TIMEFRAME_MAP.get(timeframe, "15")

    # theme=dark → רקע כהה, קל יותר לניתוח ויזואלי
    # hide_side_toolbar=1 → פחות רעש בצדדים
    params = "&".join([
        f"symbol={tv_symbol}",
        f"interval={tv_interval}",
        "theme=dark",
        "style=1",           # candlesticks
        "hide_side_toolbar=1",
        "allow_symbol_change=0",
        "save_image=0",
    ])
    return f"https://www.tradingview.com/chart/?{params}"
