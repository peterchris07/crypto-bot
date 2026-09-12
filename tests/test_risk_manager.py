"""Tahap 5: RiskManager bagian sizing dan stop lapis 1 (kill switch di tahap 6)."""

from __future__ import annotations

import dataclasses

import pytest

from tradebot.config import load_settings
from tradebot.exchange import MarketLimits
from tradebot.risk import ExitReason, MinimumNotionalError, RiskError, RiskManager, StopLevels
from tradebot.risk.manager import quantize_down

LIMITS = MarketLimits(
    symbol="BTC/USDT",
    base="BTC",
    quote="USDT",
    min_amount=0.00001,
    amount_step=0.00001,
    min_cost=5.0,
    price_step=0.01,
)


@pytest.fixture
def risk(config_path) -> RiskManager:
    settings = load_settings(config_path, environ={})
    return RiskManager(settings.risk, settings.costs)


def test_sizing_leaves_room_for_fees(risk: RiskManager):
    amount = risk.size_position(1000.0, 50_000.0)
    budget = 1000.0 * 0.10
    assert amount * 50_000.0 * (1 + risk.costs.total_fee_rate) == pytest.approx(budget)
    assert amount * 50_000.0 < budget


def test_sizing_is_capped_by_max_position_fraction(risk: RiskManager):
    greedy = RiskManager(
        dataclasses.replace(risk.config, position_fraction=0.25, max_position_fraction=0.25),
        risk.costs,
    )
    assert greedy.position_fraction == 0.25
    assert greedy.size_position(1000.0, 100.0) == pytest.approx(
        250.0 / (100.0 * (1 + risk.costs.total_fee_rate))
    )


def test_sizing_quantizes_down_to_exchange_step(risk: RiskManager):
    amount = risk.size_position(1000.0, 50_000.0, LIMITS)
    assert amount == quantize_down(amount, 0.00001)
    assert round(amount / 0.00001, 6) == int(round(amount / 0.00001, 6))
    assert quantize_down(0.0019999, 0.001) == 0.001
    assert quantize_down(0.003, 0.001) == pytest.approx(0.003)


def test_below_minimum_notional_stops_with_a_clear_message(risk: RiskManager):
    with pytest.raises(MinimumNotionalError, match="min_cost 5.0 USDT") as excinfo:
        risk.size_position(20.0, 50_000.0, LIMITS)  # budget 2 USDT
    message = str(excinfo.value)
    assert "bot berhenti" in message and "position_fraction" in message
    with pytest.raises(MinimumNotionalError, match="min_amount"):
        risk.size_position(60.0, 1_000_000.0, LIMITS)  # 6 USDT budget -> 0.000006 BTC


def test_sizing_rejects_non_positive_inputs(risk: RiskManager):
    with pytest.raises(RiskError):
        risk.size_position(0.0, 100.0)
    with pytest.raises(RiskError):
        risk.size_position(100.0, 0.0)


def test_stop_levels_from_entry_price(risk: RiskManager):
    levels = risk.stop_levels(100.0)
    assert levels == StopLevels(stop_loss=98.0, take_profit=104.0)


def test_exit_reason_for_bar_prefers_stop_loss_when_both_touch(risk: RiskManager):
    levels = StopLevels(stop_loss=98.0, take_profit=104.0)
    assert risk.exit_reason_for_bar(levels, high=103.0, low=98.5) is None
    assert risk.exit_reason_for_bar(levels, high=103.0, low=98.0) is ExitReason.STOP_LOSS
    assert risk.exit_reason_for_bar(levels, high=104.0, low=99.0) is ExitReason.TAKE_PROFIT
    assert risk.exit_reason_for_bar(levels, high=110.0, low=90.0) is ExitReason.STOP_LOSS


def test_exit_reason_for_price(risk: RiskManager):
    levels = StopLevels(stop_loss=98.0, take_profit=104.0)
    assert risk.exit_reason_for_price(levels, 100.0) is None
    assert risk.exit_reason_for_price(levels, 97.9) is ExitReason.STOP_LOSS
    assert risk.exit_reason_for_price(levels, 104.5) is ExitReason.TAKE_PROFIT
