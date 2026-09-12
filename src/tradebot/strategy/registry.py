"""Pemetaan strategy.name di config ke kelas strategi. Satu-satunya tempat strategi dibuat."""

from __future__ import annotations

from collections.abc import Callable

from tradebot.config import ConfigError, StrategyConfig
from tradebot.strategy.base import Strategy
from tradebot.strategy.ema_cross import EmaCross

STRATEGIES: dict[str, Callable[[StrategyConfig], Strategy]] = {
    "ema_cross": lambda c: EmaCross(c.fast_period, c.slow_period, c.lookback_multiplier),
}


def build_strategy(config: StrategyConfig) -> Strategy:
    try:
        factory = STRATEGIES[config.name]
    except KeyError:
        raise ConfigError(
            f"strategy.name {config.name!r} tidak dikenal. Tersedia: {sorted(STRATEGIES)}"
        ) from None
    return factory(config)
