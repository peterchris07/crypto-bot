"""Tahap 4: interface Strategy dan EMA crossover.

Data buatan dengan crossover yang sudah diketahui posisinya. EMA acuan di test
ini ditulis sebagai loop rekursif polos, terpisah dari pandas, supaya yang diuji
adalah definisi EMA-nya, bukan implementasi yang sama dua kali.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pandas as pd
import pytest

from tradebot.config import ConfigError, StrategyConfig, load_settings
from tradebot.data.ohlcv import frame_from_rows, validate_frame
from tradebot.strategy import Signal, Strategy, build_strategy
from tradebot.strategy.ema_cross import EmaCross

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def frame(closes: list[float], start_ms: int = T0) -> pd.DataFrame:
    return frame_from_rows(
        [[start_ms + i * HOUR, c, c + 1, c - 1, c, 10.0] for i, c in enumerate(closes)]
    )


def reference_ema(values: list[float], period: int) -> list[float]:
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def signals_bar_by_bar(strategy: Strategy, bars: pd.DataFrame) -> list[Signal]:
    """Seperti backtest event-driven: keputusan bar ke-i hanya dari bar 0..i."""
    return [strategy.signal(bars.iloc[: i + 1]) for i in range(len(bars))]


@pytest.fixture
def strategy() -> EmaCross:
    return EmaCross(fast_period=5, slow_period=10, lookback_multiplier=3)  # lookback 30 bar


# --------------------------------------------------------------------------- #
# Warmup dan jendela tetap
# --------------------------------------------------------------------------- #


def test_flat_until_lookback_bars_are_available(strategy: EmaCross):
    rising = frame([100.0 + i for i in range(29)])  # tren naik kuat, tapi baru 29 bar
    assert strategy.lookback_bars == 30
    assert all(s is Signal.FLAT for s in signals_bar_by_bar(strategy, rising))
    assert strategy.signal(frame([100.0 + i for i in range(30)])) is Signal.LONG


def test_signal_is_pure_function_of_last_lookback_bars(strategy: EmaCross):
    window = [100.0 + np.sin(i / 3) * 5 for i in range(30)]
    base = frame(window)
    garbage = [1e6, 1.0, 5e5, 2.0] * 20  # 80 bar sampah sebelum jendela
    with_prefix = frame(garbage + window)
    assert strategy.signal(with_prefix) is strategy.signal(base)
    # Dan jendela yang lebih pendek dari lookback tidak pernah dipakai diam-diam.
    assert strategy.signal(frame(window[5:])) is Signal.FLAT


def test_constant_price_is_flat_not_long(strategy: EmaCross):
    assert strategy.signal(frame([100.0] * 60)) is Signal.FLAT


# --------------------------------------------------------------------------- #
# Crossover pada bar yang benar
# --------------------------------------------------------------------------- #


def test_step_up_and_step_down_switch_exactly_at_the_step(strategy: EmaCross):
    closes = [100.0] * 40 + [110.0] * 20 + [90.0] * 20
    signals = signals_bar_by_bar(strategy, frame(closes))
    # 0..29 warmup; 30..39 datar -> FLAT; bar 40 lompat -> EMA cepat langsung di atas.
    assert signals[39] is Signal.FLAT and signals[40] is Signal.LONG
    assert all(s is Signal.LONG for s in signals[40:60])
    assert signals[59] is Signal.LONG and signals[60] is Signal.FLAT
    assert all(s is Signal.FLAT for s in signals[60:])


def test_crossover_bar_matches_reference_ema_recursion(strategy: EmaCross):
    """Bentuk V: turun 25 bar, naik 35 bar. Bar crossover dihitung dengan EMA acuan."""
    closes = [200.0 - 2 * i for i in range(25)] + [150.0 + 3 * i for i in range(35)]
    bars = frame(closes)
    signals = signals_bar_by_bar(strategy, bars)
    expected = []
    for i in range(len(closes)):
        if i + 1 < strategy.lookback_bars:
            expected.append(Signal.FLAT)
            continue
        window = closes[i + 1 - strategy.lookback_bars : i + 1]
        fast = reference_ema(window, 5)[-1]
        slow = reference_ema(window, 10)[-1]
        expected.append(Signal.LONG if fast > slow else Signal.FLAT)
    assert signals == expected
    first_long = signals.index(Signal.LONG)
    assert 25 < first_long < 40, (
        "crossover terjadi beberapa bar setelah titik balik, bukan di titik balik"
    )
    assert all(s is Signal.FLAT for s in signals[:first_long])
    assert all(s is Signal.LONG for s in signals[first_long:])


def test_periods_change_the_crossover_bar():
    closes = [200.0 - 2 * i for i in range(25)] + [150.0 + 3 * i for i in range(35)]
    quick = EmaCross(3, 6, 5)
    slow = EmaCross(5, 10, 3)
    first_quick = signals_bar_by_bar(quick, frame(closes)).index(Signal.LONG)
    first_slow = signals_bar_by_bar(slow, frame(closes)).index(Signal.LONG)
    assert first_quick < first_slow


# --------------------------------------------------------------------------- #
# Kontrak interface
# --------------------------------------------------------------------------- #


def test_signal_is_deterministic_and_does_not_mutate_input(strategy: EmaCross):
    bars = frame([100.0 + np.sin(i / 4) * 10 for i in range(80)])
    before = bars.copy(deep=True)
    first = signals_bar_by_bar(strategy, bars)
    second = signals_bar_by_bar(strategy, bars)
    assert first == second
    pd.testing.assert_frame_equal(bars, before)
    validate_frame(bars)


def test_extra_columns_are_ignored_and_invalid_frames_rejected(strategy: EmaCross):
    bars = frame([100.0 + i for i in range(40)])
    bars["ema_palsu"] = 0.0
    assert strategy.signal(bars) is Signal.LONG
    with pytest.raises(ValueError, match="kolom OHLCV hilang"):
        strategy.signal(pd.DataFrame({"close": [1.0, 2.0]}))
    unsorted = frame([1.0, 2.0, 3.0]).iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="urut"):
        strategy.signal(unsorted)


def test_constructor_rejects_bad_periods():
    with pytest.raises(ValueError):
        EmaCross(10, 10, 5)
    with pytest.raises(ValueError):
        EmaCross(0, 10, 5)
    with pytest.raises(ValueError):
        EmaCross(5, 10, 0)


def test_build_strategy_uses_config_values(config_path):
    settings = load_settings(config_path, environ={})
    strategy = build_strategy(settings.strategy)
    assert isinstance(strategy, EmaCross)
    assert (strategy.fast_period, strategy.slow_period) == (20, 50)
    assert strategy.lookback_bars == 250
    unknown = dataclasses.replace(settings.strategy, name="rsi_ajaib")
    with pytest.raises(ConfigError, match="rsi_ajaib"):
        build_strategy(unknown)


def test_strategy_config_dataclass_shape():
    config = StrategyConfig(name="ema_cross", fast_period=2, slow_period=4, lookback_multiplier=1)
    assert build_strategy(config).lookback_bars == 4
