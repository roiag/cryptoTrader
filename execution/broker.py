"""
Broker - ממשק עם Bybit דרך ccxt.

תומך ב:
  • Paper trading (testnet) - מוגדר ב-.env
  • שליחת פקודות market + stop-loss + take-profit
  • שליפת פוזיציות פתוחות
  • שליפת יתרת חשבון

כל פקודה נשלחת עם ה-hedge mode מכובה (one-way mode).
"""

import ccxt
from dataclasses import dataclass
from loguru import logger

from config.settings import settings


@dataclass
class OrderResult:
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float       # מחיר ביצוע בפועל (0 = market, מחיר יאורגן בבדיקת fill)
    status: str        # "open" / "closed" / "canceled"
    order_type: str    # "market" / "limit" / "stop_market"


class Broker:

    def __init__(self) -> None:
        params = {
            "enableRateLimit": True,
            "options": {
                "defaultType": "linear",       # USDT perpetual
                "recvWindow": 10_000,
            },
        }
        if settings.BYBIT_API_KEY:
            params["apiKey"] = settings.BYBIT_API_KEY
            params["secret"] = settings.BYBIT_SECRET

        self.exchange = ccxt.bybit(params)

        if settings.PAPER_TRADING:
            self.exchange.set_sandbox_mode(True)
            logger.info("Broker: Bybit TESTNET (paper trading)")
        else:
            logger.warning("Broker: Bybit LIVE - real money!")

    # ── פקודות ────────────────────────────────────────────────────────────────

    def place_market_order(
        self, symbol: str, side: str, quantity: float
    ) -> OrderResult:
        """פקודת כניסה - Market order. סימולציה אם אין API key."""
        logger.info(f"[Broker] Market {side.upper()} {quantity} {symbol}")

        if not settings.BYBIT_API_KEY:
            price = self.get_current_price(symbol)
            result = self._simulated_order(symbol, side, quantity, price, "market")
            logger.info(f"[Broker] SIMULATED entry: {result.order_id} @ {result.price}")
            return result

        order = self.exchange.create_order(
            symbol=symbol, type="market", side=side, amount=quantity,
            params={"category": "linear"},
        )
        result = self._parse_order(order)
        logger.info(f"[Broker] Entry order placed: {result.order_id} @ {result.price}")
        return result

    def place_stop_loss(
        self, symbol: str, side: str, quantity: float, stop_price: float
    ) -> OrderResult:
        """Stop-Loss. סימולציה אם אין API key."""
        logger.info(f"[Broker] StopLoss {side.upper()} {quantity} {symbol} @ {stop_price}")

        if not settings.BYBIT_API_KEY:
            return self._simulated_order(symbol, side, quantity, stop_price, "stop_market")

        order = self.exchange.create_order(
            symbol=symbol, type="stop_market", side=side,
            amount=quantity, price=stop_price,
            params={
                "category": "linear", "stopPrice": stop_price,
                "triggerBy": "LastPrice", "reduceOnly": True,
            },
        )
        return self._parse_order(order)

    def place_take_profit(
        self, symbol: str, side: str, quantity: float, tp_price: float
    ) -> OrderResult:
        """Take-Profit. סימולציה אם אין API key."""
        logger.info(f"[Broker] TakeProfit {side.upper()} {quantity} {symbol} @ {tp_price}")

        if not settings.BYBIT_API_KEY:
            return self._simulated_order(symbol, side, quantity, tp_price, "limit")

        order = self.exchange.create_order(
            symbol=symbol, type="limit", side=side,
            amount=quantity, price=tp_price,
            params={"category": "linear", "reduceOnly": True},
        )
        return self._parse_order(order)

    def cancel_order(self, symbol: str, order_id: str) -> bool:
        """מבטל פקודה פתוחה."""
        try:
            self.exchange.cancel_order(order_id, symbol, params={"category": "linear"})
            logger.info(f"[Broker] Cancelled order {order_id}")
            return True
        except Exception as e:
            logger.error(f"[Broker] Cancel failed {order_id}: {e}")
            return False

    def fetch_order_fill(self, symbol: str, order_id: str) -> dict | None:
        """
        בודק אם פקודה בוצעה (filled).
        מחזיר dict עם מחיר ממוצע אם בוצעה, None אחרת.
        """
        if not order_id:
            return None
        try:
            order = self.exchange.fetch_order(
                order_id, symbol, params={"category": "linear"}
            )
            status = order.get("status", "")
            filled = float(order.get("filled") or 0)
            if status == "closed" or filled > 0:
                return {
                    "price": float(order.get("average") or order.get("price") or 0),
                    "quantity": filled,
                    "status": status,
                }
            return None
        except Exception as e:
            logger.warning(f"[Broker] fetch_order_fill {order_id}: {e}")
            return None

    def update_stop_loss(
        self,
        symbol: str,
        old_sl_order_id: str,
        side: str,
        quantity: float,
        new_sl_price: float,
    ) -> OrderResult | None:
        """
        מבטל SL ישן ומציב SL חדש במחיר טוב יותר (trailing stop).
        מחזיר את הפקודה החדשה, או None אם נכשל.
        """
        self.cancel_order(symbol, old_sl_order_id)
        try:
            return self.place_stop_loss(symbol, side, quantity, new_sl_price)
        except Exception as e:
            logger.error(f"[Broker] Failed to place new trailing SL: {e}")
            return None

    # ── שאילתות ───────────────────────────────────────────────────────────────

    PAPER_SIMULATION_BALANCE = 10_000.0   # יתרת סימולציה ללא API key

    def get_balance(self) -> float:
        """יתרת USDT פנויה. אם אין API key מחזיר יתרת סימולציה."""
        if not settings.BYBIT_API_KEY:
            logger.debug(
                f"[Broker] No API key — using simulation balance "
                f"${self.PAPER_SIMULATION_BALANCE:,.0f}"
            )
            return self.PAPER_SIMULATION_BALANCE
        try:
            balance = self.exchange.fetch_balance(params={"category": "linear"})
            free = balance.get("USDT", {}).get("free", 0.0)
            logger.debug(f"[Broker] Balance: ${free:.2f} USDT free")
            return float(free)
        except Exception as e:
            logger.error(f"[Broker] Balance fetch failed: {e}")
            return 0.0

    def get_open_positions(self) -> list[dict]:
        """
        מחזיר רשימת פוזיציות פתוחות.
        כל פוזיציה: {"symbol", "side", "size", "entry_price", "unrealized_pnl"}
        """
        try:
            positions = self.exchange.fetch_positions(
                params={"category": "linear"}
            )
            open_pos = [
                {
                    "symbol": p["symbol"],
                    "side": p["side"],
                    "size": float(p["contracts"] or 0),
                    "entry_price": float(p["entryPrice"] or 0),
                    "unrealized_pnl": float(p["unrealizedPnl"] or 0),
                }
                for p in positions
                if float(p.get("contracts") or 0) > 0
            ]
            logger.debug(f"[Broker] Open positions: {len(open_pos)}")
            return open_pos
        except Exception as e:
            logger.error(f"[Broker] Positions fetch failed: {e}")
            return []

    def get_open_position_symbols(self) -> list[str]:
        """
        רשימה של symbols עם פוזיציות פתוחות.
        זורק ValueError אם ה-API נכשל — כדי שה-Risk Guard יחסום (בטוח יותר מ-[]).
        """
        positions = self.exchange.fetch_positions(
            params={"category": "linear"}
        )
        return [
            p["symbol"]
            for p in positions
            if float(p.get("contracts") or 0) > 0
        ]

    def get_current_price(self, symbol: str) -> float:
        """מחיר אחרון."""
        ticker = self.exchange.fetch_ticker(symbol)
        return float(ticker["last"])

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _simulated_order(
        symbol: str, side: str, quantity: float, price: float, order_type: str
    ) -> OrderResult:
        """פקודה מדומה לצורך paper trading ללא API key."""
        import uuid
        return OrderResult(
            order_id=f"SIM-{uuid.uuid4().hex[:8].upper()}",
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            status="closed",
            order_type=order_type,
        )

    @staticmethod
    def _parse_order(order: dict) -> OrderResult:
        return OrderResult(
            order_id=str(order.get("id", "")),
            symbol=order.get("symbol", ""),
            side=order.get("side", ""),
            quantity=float(order.get("amount") or 0),
            price=float(order.get("average") or order.get("price") or 0),
            status=order.get("status", "open"),
            order_type=order.get("type", "market"),
        )
