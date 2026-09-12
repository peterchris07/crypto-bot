"""Lapisan exchange. Kode lain hanya bicara ke ExchangeAdapter, tidak pernah ke ccxt langsung.

Adapter dibuat lewat tradebot.exchange.factory.build_adapter, bukan langsung.
"""

from tradebot.exchange.base import (
    AssetBalance,
    Balance,
    ExchangeAdapter,
    MarketLimits,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Ticker,
    Trade,
)
from tradebot.exchange.errors import (
    AuthenticationError,
    ExchangeError,
    FatalExchangeError,
    InsufficientFundsError,
    InvalidOrderError,
    MainnetRefusedError,
    OrderNotFoundError,
    OrderStateUnknownError,
    RetryableExchangeError,
    TimeDriftError,
)

__all__ = [
    "AssetBalance",
    "AuthenticationError",
    "Balance",
    "ExchangeAdapter",
    "ExchangeError",
    "FatalExchangeError",
    "InsufficientFundsError",
    "InvalidOrderError",
    "MainnetRefusedError",
    "MarketLimits",
    "Order",
    "OrderNotFoundError",
    "OrderSide",
    "OrderStateUnknownError",
    "OrderStatus",
    "OrderType",
    "RetryableExchangeError",
    "Ticker",
    "TimeDriftError",
    "Trade",
]
