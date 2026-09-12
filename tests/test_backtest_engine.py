"""Tahap 5: engine backtest. Test SPEC: selalu FLAT -> return 0 dan 0 trade; selalu LONG ->
buy-and-hold dikurangi biaya. Plus tanpa lookahead, eksekusi di open, stop lapis 1,
biaya per komponen, stress, dan penutupan di akhir data."""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from tradebot.backtest import run_backtest
from tradebot.config import load_settings
from tradebot.data.ohlcv import frame_from_rows
from tradebot.risk import ExitReason, MinimumNotionalError, RiskManager
from tradebot.strategy.base import Signal, Strategy

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def bars_from(ohlc: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    return frame_from_rows(
        [[T0 + i * HOUR, o, h, lo, c, 10.0] for i, (o, h, lo, c) in enumerate(ohlc)]
    )


def flat_bars(price: float, count: int) -> pd.DataFrame:
    return bars_from([(price, price, price, price)] * count)


class Always(Strategy):
    def __init__(self, signal: Signal, name: str = "always") -> None:
        self._signal = signal
        self.name = name

    @property
    def lookback_bars(self) -> int:
        return 0

    def signal(self, bars: pd.DataFrame) -> Signal:
        return self._signal


class Scripted(Strategy):
    """Sinyal per indeks keputusan: signal(bars) memakai len(bars) sebagai kunci."""

    name = "scripted"

    def __init__(self, script: dict[int, Signal]) -> None:
        self.script = script
        self.seen: list[pd.Timestamp] = []

    @property
    def lookback_bars(self) -> int:
        return 0

    def signal(self, bars: pd.DataFrame) -> Signal:
        self.seen.append(bars["timestamp"].iloc[-1])
        return self.script.get(len(bars), Signal.FLAT)


@pytest.fixture
def settings(config_path):
    return load_settings(config_path, environ={})


def all_in_no_stops(settings):
    """position 100%, stop di 0 dan take profit di 2x: tidak pernah kena pada data test."""
    return dataclasses.replace(
        settings.risk,
        position_fraction=1.0,
        max_position_fraction=1.0,
        stop_loss_fraction=1.0,
        take_profit_fraction=1.0,
    )


def run(bars, strategy, settings, *, risk_config=None, costs=None):
    costs = costs or settings.costs
    risk = RiskManager(risk_config or settings.risk, costs)
    return run_backtest(
        bars,
        strategy,
        risk,
        costs,
        initial_equity=settings.backtest.initial_equity,
        bars_per_year=settings.backtest.bars_per_year,
        symbol="BTC/USDT",
        timeframe="1h",
    )


# --------------------------------------------------------------------------- #
# Test yang diminta SPEC
# --------------------------------------------------------------------------- #


def test_always_flat_returns_zero_and_no_trades(settings):
    bars = bars_from([(100, 110, 90, 105), (105, 120, 100, 95), (95, 100, 80, 90)] * 20)
    result = run(bars, Always(Signal.FLAT), settings)
    assert result.trades == []
    assert result.metrics.trade_count == 0
    assert result.metrics.total_return == 0.0
    assert result.final_equity == settings.backtest.initial_equity
    assert (result.equity_curve == settings.backtest.initial_equity).all()
    assert result.metrics.max_drawdown == 0.0 and result.metrics.sharpe is None
    assert sum(result.cost_totals.values()) == 0.0


def test_always_long_equals_buy_and_hold_minus_costs(settings):
    closes = [100 + (i % 7) * 3 - (i % 5) * 2 for i in range(60)]
    bars = bars_from([(c, c + 4, c - 4, c + 1) for c in closes])
    result = run(bars, Always(Signal.LONG), settings, risk_config=all_in_no_stops(settings))
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason is ExitReason.END_OF_DATA
    assert result.final_equity == pytest.approx(result.benchmark_final_equity)
    assert result.metrics.max_drawdown == pytest.approx(result.benchmark_metrics.max_drawdown)
    # biaya benar-benar terpotong: lebih rendah dari return harga mentah
    gross = settings.backtest.initial_equity * (1 + result.raw_price_return)
    assert result.final_equity < gross
    expected_cost_fraction = 1 - result.final_equity / gross
    assert expected_cost_fraction == pytest.approx(settings.costs.round_trip_rate, rel=0.02)


# --------------------------------------------------------------------------- #
# Tanpa lookahead, eksekusi di open
# --------------------------------------------------------------------------- #


def test_decision_for_bar_n_only_sees_bars_up_to_n_minus_1(settings):
    bars = flat_bars(100.0, 10)
    strategy = Scripted({})
    run(bars, strategy, settings)
    expected = [bars["timestamp"].iloc[i - 1] for i in range(1, 10)]
    assert strategy.seen == expected


def test_orders_fill_at_next_open_with_slippage_and_fees(settings):
    # keputusan setelah bar 3 tutup (len=4) -> LONG dieksekusi di open bar 4; FLAT setelah bar 6
    ohlc = [(100, 100, 100, 100)] * 4 + [(200, 200, 200, 200)] * 3 + [(300, 300, 300, 300)] * 3
    bars = bars_from(ohlc)
    strategy = Scripted({4: Signal.LONG, 5: Signal.LONG, 6: Signal.LONG, 7: Signal.FLAT})
    risk_cfg = all_in_no_stops(settings)
    result = run(bars, strategy, settings, risk_config=risk_cfg)
    c = settings.costs
    assert len(result.trades) == 1
    trade = result.trades[0]
    assert trade.entry_time == bars["timestamp"].iloc[4]
    assert trade.entry_price == pytest.approx(200 * (1 + c.slippage_rate))
    assert trade.exit_time == bars["timestamp"].iloc[7]
    assert trade.exit_price == pytest.approx(300 * (1 - c.slippage_rate))
    assert trade.exit_reason is ExitReason.SIGNAL
    assert trade.bars_held == 3
    notional_in = trade.amount * trade.entry_price
    assert trade.entry_fees == pytest.approx(notional_in * c.total_fee_rate)
    assert trade.pnl == pytest.approx(
        trade.amount * trade.exit_price * (1 - c.total_fee_rate)
        - notional_in * (1 + c.total_fee_rate)
    )
    assert result.final_equity == pytest.approx(settings.backtest.initial_equity + trade.pnl)


def test_position_size_follows_risk_fraction(settings):
    bars = flat_bars(100.0, 6)
    result = run(bars, Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.FLAT}), settings)
    trade = result.trades[0]
    budget = settings.backtest.initial_equity * settings.risk.position_fraction
    assert trade.amount * trade.entry_price * (1 + settings.costs.total_fee_rate) == pytest.approx(
        budget
    )


# --------------------------------------------------------------------------- #
# Stop lapis 1 dari high dan low bar
# --------------------------------------------------------------------------- #


def test_stop_loss_fills_at_stop_price_minus_slippage(settings):
    # masuk di open bar 2 (100); stop 2% = 98; bar 3 low 97 menembus
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 99, 100), (100, 101, 97, 99)]
    ohlc += [(99, 99, 99, 99)] * 3
    strategy = Always(Signal.LONG)
    result = run(bars_from(ohlc), strategy, settings)
    c = settings.costs
    entry = 100 * (1 + c.slippage_rate)
    stop = entry * (1 - settings.risk.stop_loss_fraction)
    first = result.trades[0]
    assert first.exit_reason is ExitReason.STOP_LOSS
    assert first.exit_time == result.equity_curve.index[3]
    assert first.exit_price == pytest.approx(stop * (1 - c.slippage_rate))
    assert first.pnl < 0
    # sinyal masih LONG -> masuk lagi di open bar berikutnya (dan itu memang mahal)
    assert result.trades[1].entry_time == result.equity_curve.index[4]


def test_take_profit_fills_at_target_plus_slippage(settings):
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 99, 100), (100, 106, 99, 105)]
    ohlc += [(105, 105, 105, 105)] * 2
    result = run(
        bars_from(ohlc), Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.LONG}), settings
    )
    c = settings.costs
    entry = 100 * (1 + c.slippage_rate)
    target = entry * (1 + settings.risk.take_profit_fraction)
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.exit_price == pytest.approx(target * (1 - c.slippage_rate))
    assert trade.pnl > 0


def test_stop_and_target_in_same_bar_is_counted_as_stop_loss(settings):
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 110, 90, 100), (100, 100, 100, 100)]
    result = run(bars_from(ohlc), Scripted({2: Signal.LONG, 3: Signal.FLAT}), settings)
    assert result.trades[0].exit_reason is ExitReason.STOP_LOSS
    assert result.trades[0].bars_held == 0


def test_stop_checked_on_entry_bar_itself(settings):
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 100, 90, 95), (95, 95, 95, 95)]
    result = run(bars_from(ohlc), Scripted({2: Signal.LONG}), settings)
    assert result.trades[0].exit_reason is ExitReason.STOP_LOSS
    assert result.trades[0].entry_time == result.trades[0].exit_time


# --------------------------------------------------------------------------- #
# Biaya per komponen, stress, akhir data, batas minimum
# --------------------------------------------------------------------------- #


def test_cost_components_are_tracked_separately(settings):
    bars = flat_bars(100.0, 6)
    result = run(bars, Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.FLAT}), settings)
    c = settings.costs
    trade = result.trades[0]
    notional_in = trade.amount * trade.entry_price
    notional_out = trade.amount * trade.exit_price
    totals = result.cost_totals
    assert totals["taker_fee"] == pytest.approx((notional_in + notional_out) * c.taker_fee_rate)
    assert totals["tax"] == pytest.approx((notional_in + notional_out) * c.tax_rate)
    assert totals["exchange_fee"] == pytest.approx(
        (notional_in + notional_out) * c.exchange_fee_rate
    )
    assert totals["slippage"] == pytest.approx(2 * trade.amount * 100.0 * c.slippage_rate)
    assert trade.entry_fees + trade.exit_fees == pytest.approx(
        totals["taker_fee"] + totals["tax"] + totals["exchange_fee"]
    )


def test_stress_doubles_fee_exchange_and_slippage_but_not_tax(settings):
    bars = flat_bars(100.0, 6)
    strategy = Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.FLAT})
    normal = run(bars, strategy, settings)
    stressed = run(bars, Scripted(strategy.script), settings, costs=settings.costs.stressed())
    n, s = normal.cost_totals, stressed.cost_totals
    # notional hampir sama (sizing sedikit berbeda karena fee), jadi bandingkan rasionya
    assert s["taker_fee"] / n["taker_fee"] == pytest.approx(2.0, rel=0.01)
    assert s["exchange_fee"] / n["exchange_fee"] == pytest.approx(2.0, rel=0.01)
    assert s["slippage"] / n["slippage"] == pytest.approx(2.0, rel=0.01)
    assert s["tax"] / n["tax"] == pytest.approx(1.0, rel=0.01)
    assert stressed.final_equity < normal.final_equity


def test_open_position_is_closed_at_last_close(settings):
    bars = flat_bars(100.0, 5)
    result = run(bars, Always(Signal.LONG), settings, risk_config=all_in_no_stops(settings))
    trade = result.trades[-1]
    assert trade.exit_reason is ExitReason.END_OF_DATA
    assert trade.exit_time == bars["timestamp"].iloc[-1]
    assert result.final_equity == pytest.approx(result.equity_curve.iloc[-1])


def test_below_minimum_notional_stops_the_backtest(settings):
    from tradebot.exchange import MarketLimits

    limits = MarketLimits("BTC/USDT", "BTC", "USDT", 0.00001, 0.00001, 5.0, 0.01)
    bars = flat_bars(100_000.0, 5)
    risk = RiskManager(settings.risk, settings.costs)
    tiny = dataclasses.replace(settings.backtest, initial_equity=30.0)  # budget 3 USDT
    with pytest.raises(MinimumNotionalError, match="min_cost"):
        run_backtest(
            bars,
            Always(Signal.LONG),
            risk,
            settings.costs,
            initial_equity=tiny.initial_equity,
            bars_per_year=8760,
            symbol="BTC/USDT",
            timeframe="1h",
            limits=limits,
        )


def test_short_is_treated_as_flat_with_one_warning(settings, caplog):
    import logging

    bars = flat_bars(100.0, 6)
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        result = run(bars, Always(Signal.SHORT), settings)
    assert result.trades == []
    assert sum("SHORT" in r.getMessage() for r in caplog.records) == 1


def test_engine_rejects_too_few_bars_and_bad_frames(settings):
    with pytest.raises(ValueError, match="minimal 2 bar"):
        run(flat_bars(100.0, 1), Always(Signal.FLAT), settings)
    with pytest.raises(ValueError, match="kolom OHLCV hilang"):
        run(pd.DataFrame({"close": [1.0, 2.0]}), Always(Signal.FLAT), settings)


def test_equity_curve_marks_open_position_to_close(settings):
    bars = bars_from(
        [(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 100, 101), (101, 102, 101, 102)]
    )
    result = run(bars, Always(Signal.LONG), settings, risk_config=all_in_no_stops(settings))
    curve = result.equity_curve
    assert len(curve) == 4
    assert curve.iloc[0] == settings.backtest.initial_equity
    # masuk di open bar 1 (100): equity bar 1 = kas + amount x 100, lebih rendah dari awal (biaya)
    assert curve.iloc[1] < settings.backtest.initial_equity
    # close naik 100 -> 101 -> 102: equity ikut naik sampai ditutup di close terakhir
    assert curve.iloc[2] > curve.iloc[1]
    assert curve.iloc[3] == pytest.approx(result.final_equity)


# --------------------------------------------------------------------------- #
# Temuan review: gap harga menembus level, warmup adil, bar bolong, hitungan tangan
# --------------------------------------------------------------------------- #


class Lookback(Always):
    def __init__(self, signal: Signal, lookback: int) -> None:
        super().__init__(signal, name="lookback")
        self._lookback = lookback

    @property
    def lookback_bars(self) -> int:
        return self._lookback


def test_gap_down_through_stop_fills_at_open_not_at_stop(settings):
    # masuk di open bar 2 (100), stop 98.15; bar 3 dibuka 90: bot live jual di sekitar open
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 99, 100), (90, 92, 88, 91)]
    ohlc += [(91, 91, 91, 91)] * 2
    result = run(
        bars_from(ohlc), Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.FLAT}), settings
    )
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.exit_price == pytest.approx(90 * (1 - settings.costs.slippage_rate))
    assert trade.exit_price <= 92, "fill tidak boleh di harga yang tidak pernah ada di bar"


def test_gap_up_through_target_fills_at_open_not_at_target(settings):
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 101, 99, 100), (110, 112, 109, 111)]
    ohlc += [(111, 111, 111, 111)] * 2
    result = run(
        bars_from(ohlc), Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.LONG}), settings
    )
    trade = result.trades[0]
    assert trade.exit_reason is ExitReason.TAKE_PROFIT
    assert trade.exit_price == pytest.approx(110 * (1 - settings.costs.slippage_rate))
    assert trade.exit_price >= 109


def test_benchmark_starts_at_first_tradable_bar_after_warmup(settings):
    closes = [100.0 + i for i in range(12)]
    bars = bars_from([(c, c + 1, c - 1, c) for c in closes])
    strategy = Lookback(Signal.LONG, lookback=5)
    result = run(bars, strategy, settings, risk_config=all_in_no_stops(settings))
    assert result.warmup_bars == 5
    assert result.tradable_start == bars["timestamp"].iloc[5]
    assert result.trades[0].entry_time == bars["timestamp"].iloc[5]
    assert result.equity_curve.index[0] == bars["timestamp"].iloc[4]
    assert len(result.equity_curve) == 12 - 4
    # buy-and-hold masuk di bar yang sama, jadi always-LONG tetap identik dengannya
    assert result.final_equity == pytest.approx(result.benchmark_final_equity)
    assert result.raw_price_return == pytest.approx(closes[-1] / closes[5] - 1)
    with pytest.raises(ValueError, match="warmup"):
        run(bars.iloc[:5], strategy, settings)


def test_bar_after_data_hole_gets_no_decision_and_holding_is_time_based(settings):
    rows = [[T0 + i * HOUR, 100.0, 101.0, 99.0, 100.0, 10.0] for i in range(4)]
    rows += [
        [T0 + (i + 9) * HOUR, 100.0, 101.0, 99.0, 100.0, 10.0] for i in range(4)
    ]  # lubang 5 jam
    bars = frame_from_rows(rows)
    strategy = Scripted({2: Signal.LONG, 3: Signal.LONG, 4: Signal.FLAT, 5: Signal.FLAT})
    result = run(bars, strategy, settings, risk_config=all_in_no_stops(settings))
    # keputusan untuk bar indeks 4 (bar pertama setelah lubang) dilewati: FLAT dari skrip
    # len=4 tidak pernah ditanyakan, jadi posisi baru ditutup di open bar indeks 5.
    assert result.bars_after_gap == 1
    assert len(strategy.seen) == 6  # 7 bar keputusan (indeks 1..7) dikurangi satu yang dilewati
    assert bars["timestamp"].iloc[3] not in strategy.seen[3:]  # frame len=4 tidak pernah diminta
    trade = result.trades[0]
    assert trade.entry_time == bars["timestamp"].iloc[2]  # T0+2h
    assert trade.exit_time == bars["timestamp"].iloc[5]  # T0+10h
    assert trade.bars_held == 8, "dari selisih waktu (8 jam), bukan 3 baris"


def test_always_long_final_equity_hand_computed(settings):
    ohlc = [(100, 100, 100, 100), (100, 100, 100, 100), (100, 110, 100, 110), (110, 120, 110, 120)]
    result = run(
        bars_from(ohlc), Always(Signal.LONG), settings, risk_config=all_in_no_stops(settings)
    )
    c = settings.costs
    equity0 = settings.backtest.initial_equity
    fill_in = 100 * (1 + c.slippage_rate)
    amount = equity0 / (fill_in * (1 + c.total_fee_rate))
    cash_after_buy = equity0 - amount * fill_in * (1 + c.total_fee_rate)
    fill_out = 120 * (1 - c.slippage_rate)
    expected = cash_after_buy + amount * fill_out * (1 - c.total_fee_rate)
    assert result.final_equity == pytest.approx(expected)
    assert cash_after_buy == pytest.approx(0.0, abs=1e-9)
