"""Klien ccxt palsu untuk test unit: merekam setiap panggilan dan bisa diprogram untuk gagal."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import ccxt

DEFAULT_MARKET: dict[str, Any] = {
    "symbol": "BTC/USDT",
    "base": "BTC",
    "quote": "USDT",
    "active": True,
    "precision": {"amount": 1e-05, "price": 0.01},
    "limits": {
        "amount": {"min": 1e-05, "max": 9000.0},
        "cost": {"min": 5.0, "max": None},
        "price": {"min": 0.01, "max": 1_000_000.0},
    },
}

BASE_MS = 1_700_000_000_000
HOUR_MS = 3_600_000


class FakeCcxtClient:
    def __init__(self, params: dict[str, Any]) -> None:
        self.params = dict(params)
        self.sandbox: bool | None = None
        self.urls: dict[str, Any] = {
            "api": {
                "public": "https://api.example/api/v3",
                "private": "https://api.example/api/v3",
            }
        }
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.failures: dict[str, list[Exception]] = defaultdict(list)
        self.server_time_ms = BASE_MS
        self.markets = {"BTC/USDT": dict(DEFAULT_MARKET)}
        self.has = {"cancelAllOrders": True, "fetchTime": True, "fetchOrder": True}
        self.ohlcv_rows = [
            [BASE_MS + i * HOUR_MS, 100.0 + i, 101.0 + i, 99.0 + i, 100.5 + i, 10.0]
            for i in range(5)
        ]
        self.ticker = {
            "symbol": "BTC/USDT",
            "last": 50_000.0,
            "bid": 49_999.0,
            "ask": 50_001.0,
            "timestamp": BASE_MS,
        }
        self.balance = {
            "free": {"USDT": 1000.0, "BTC": 0.5},
            "used": {"USDT": 0.0, "BTC": 0.1},
            "total": {"USDT": 1000.0, "BTC": 0.6},
        }
        self.open_orders: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self._next_id = 1

    # -- alat test ---------------------------------------------------------

    def fail_next(self, name: str, *exceptions: Exception) -> None:
        self.failures[name].extend(exceptions)

    def count(self, name: str) -> int:
        return sum(1 for call in self.calls if call[0] == name)

    def last_call(self, name: str) -> tuple[tuple[Any, ...], dict[str, Any]]:
        for called, args, kwargs in reversed(self.calls):
            if called == name:
                return args, kwargs
        raise AssertionError(f"{name} tidak pernah dipanggil")

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))
        queue = self.failures.get(name)
        if queue:
            raise queue.pop(0)

    # -- API ccxt yang dipakai adapter ---------------------------------------

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox = enabled

    def fetch_time(self) -> int:
        self._record("fetch_time")
        return self.server_time_ms

    def load_markets(self, reload: bool = False, params: dict | None = None) -> dict:
        self._record("load_markets")
        return self.markets

    def market(self, symbol: str) -> dict[str, Any]:
        if symbol not in self.markets:
            raise ccxt.BadSymbol(f"fake does not have market symbol {symbol}")
        return self.markets[symbol]

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        self._record("fetch_ohlcv", symbol, timeframe, since, limit)
        rows = self.ohlcv_rows
        if since is not None:
            rows = [row for row in rows if row[0] >= since]
        if limit is not None:
            rows = rows[:limit]
        return [list(row) for row in rows]

    def fetch_ticker(self, symbol, params=None):
        self._record("fetch_ticker", symbol)
        return dict(self.ticker)

    def fetch_balance(self, params=None):
        self._record("fetch_balance")
        return {key: dict(value) for key, value in self.balance.items()}

    def create_order(self, symbol, type, side, amount, price=None, params=None):
        params = dict(params or {})
        self._record("create_order", symbol, type, side, amount, price, params)
        order_id = str(self._next_id)
        self._next_id += 1
        is_stop = "stopLossPrice" in params
        raw_type = "stop_loss_limit" if is_stop else type
        is_market = type == "market"
        status = "closed" if is_market else "open"
        filled = float(amount) if is_market else 0.0
        average = float(self.ticker["last"]) if is_market else None
        cost = filled * average if average else 0.0
        order = {
            "id": order_id,
            "clientOrderId": params.get("clientOrderId"),
            "symbol": symbol,
            "side": side,
            "type": raw_type,
            "amount": float(amount),
            "price": price,
            "stopPrice": params.get("stopLossPrice"),
            "status": status,
            "filled": filled,
            "average": average,
            "cost": cost,
            "fee": {"cost": cost * 0.001, "currency": "USDT"} if is_market else None,
            "timestamp": self.server_time_ms,
        }
        self.orders[order_id] = order
        if status == "open":
            self.open_orders.append(order)
        return dict(order)

    def cancel_order(self, id, symbol=None, params=None):
        self._record("cancel_order", id, symbol)
        order = self.orders.get(str(id))
        if order is None or order["status"] != "open":
            raise ccxt.OrderNotFound(f"fake order {id} not found")
        order["status"] = "canceled"
        self.open_orders = [o for o in self.open_orders if o["id"] != str(id)]
        return dict(order)

    def cancel_all_orders(self, symbol=None, params=None):
        self._record("cancel_all_orders", symbol)
        canceled = []
        for order in list(self.open_orders):
            order["status"] = "canceled"
            canceled.append(dict(order))
        self.open_orders = []
        return canceled

    def fetch_open_orders(self, symbol=None, since=None, limit=None, params=None):
        self._record("fetch_open_orders", symbol)
        return [dict(o) for o in self.open_orders if symbol is None or o["symbol"] == symbol]

    def fetch_order(self, id, symbol=None, params=None):
        params = dict(params or {})
        self._record("fetch_order", id, symbol, params)
        client_order_id = params.get("clientOrderId")
        if client_order_id:
            for order in self.orders.values():
                if order["clientOrderId"] == client_order_id:
                    return dict(order)
            raise ccxt.OrderNotFound(f"fake order with clientOrderId {client_order_id} not found")
        order = self.orders.get(str(id))
        if order is None:
            raise ccxt.OrderNotFound(f"fake order {id} not found")
        return dict(order)
