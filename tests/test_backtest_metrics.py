"""Tahap 5: metrik dari kurva equity buatan dengan jawaban yang dihitung tangan."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from tradebot.backtest.metrics import compute_metrics, max_drawdown, sharpe_ratio
from tradebot.risk import ExitReason


def curve(values):
    idx = pd.date_range("2024-01-01", periods=len(values), freq="h", tz="UTC")
    return pd.Series(values, index=idx, dtype="float64")


class T:
    def __init__(self, pnl, bars_held=1):
        self.pnl = pnl
        self.bars_held = bars_held
        self.exit_reason = ExitReason.SIGNAL


def test_max_drawdown_hand_computed():
    assert max_drawdown(curve([100, 120, 90, 130, 65, 70])) == pytest.approx(0.5)
    assert max_drawdown(curve([100, 100, 100])) == 0.0
    assert max_drawdown(curve([100, 110, 121])) == 0.0


def test_sharpe_annualizes_per_bar_returns_and_is_none_without_variance():
    values = [100.0]
    for r in [0.01, -0.005, 0.02, 0.0, 0.01]:
        values.append(values[-1] * (1 + r))
    returns = pd.Series([0.01, -0.005, 0.02, 0.0, 0.01])
    expected = returns.mean() / returns.std(ddof=1) * math.sqrt(8760)
    assert sharpe_ratio(curve(values), 8760) == pytest.approx(expected)
    assert sharpe_ratio(curve([100, 100, 100, 100]), 8760) is None
    assert sharpe_ratio(curve([100, 101]), 8760) is None


def test_compute_metrics_trade_statistics():
    trades = [T(10, 2), T(-5, 4), T(20, 6), T(-15, 8)]
    metrics = compute_metrics(curve([100, 110, 105, 125, 110]), trades, 8760)
    assert metrics.total_return == pytest.approx(0.10)
    assert metrics.trade_count == 4
    assert metrics.win_rate == pytest.approx(0.5)
    assert metrics.profit_factor == pytest.approx(30 / 20)
    assert metrics.avg_bars_held == pytest.approx(5.0)
    assert metrics.max_drawdown == pytest.approx(15 / 125)
    assert metrics.sharpe_basis_bars_per_year == 8760


def test_compute_metrics_without_trades_or_losses():
    empty = compute_metrics(curve([100, 100]), [], 8760)
    assert empty.win_rate is None and empty.profit_factor is None and empty.avg_bars_held is None
    only_wins = compute_metrics(curve([100, 110]), [T(10)], 8760)
    assert only_wins.profit_factor is None and only_wins.win_rate == 1.0
    with pytest.raises(ValueError, match="kosong"):
        compute_metrics(curve([]), [], 8760)
