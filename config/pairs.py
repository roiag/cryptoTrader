"""
רשימת הזוגות למסחר.
Phase A: BTC + ETH בלבד - הכי נזילים, הכי מתועדים בחדשות.
"""

TRADING_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
]

# מידע עזר לכל pair - לשימוש ב-Sentiment Agent בהמשך
PAIR_METADATA = {
    "BTC/USDT": {
        "name": "Bitcoin",
        "search_terms": ["Bitcoin", "BTC", "$BTC"],
        "min_qty": 0.001,
        "price_precision": 1,
        "qty_precision": 3,
    },
    "ETH/USDT": {
        "name": "Ethereum",
        "search_terms": ["Ethereum", "ETH", "$ETH"],
        "min_qty": 0.01,
        "price_precision": 2,
        "qty_precision": 3,
    },
}
