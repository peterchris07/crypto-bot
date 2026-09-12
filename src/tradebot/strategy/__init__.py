"""Strategi: dari DataFrame OHLCV ke satu Signal. Tidak tahu saldo, ukuran posisi, atau exchange."""

from tradebot.strategy.base import Signal, Strategy
from tradebot.strategy.registry import build_strategy

__all__ = ["Signal", "Strategy", "build_strategy"]
