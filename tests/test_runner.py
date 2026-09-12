"""Tahap 7: runner mode paper di atas klien publik palsu, jam dikendalikan test.

Feed: klien palsu diberi bar per jam; test memajukan jam dan menambah bar. Ticker
disetel = open bar berikutnya supaya fill paper sama dengan aturan backtest, dan
test terakhir membuktikan runner dan run_backtest menghasilkan trade yang sama
untuk data yang sama.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import datetime
from pathlib import Path

import ccxt
import pandas as pd
import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot.backtest import run_backtest
from tradebot.config import load_settings
from tradebot.data.ohlcv import frame_from_rows
from tradebot.exchange.factory import build_public_adapter
from tradebot.exchange.paper import PaperAdapter
from tradebot.ledger import Ledger
from tradebot.live.journal import OrderJournal
from tradebot.live.runner import EXIT_KILL_SWITCH, EXIT_OK, PositionStore, Runner
from tradebot.risk import DailyStateStore, RiskManager
from tradebot.strategy.base import Signal, Strategy
from tradebot.strategy.ema_cross import EmaCross

HOUR = 3_600_000
T0 = BASE_MS - (BASE_MS % HOUR)  # 2023-11-14T22:00Z


class Scripted(Strategy):
    """Sinyal ditentukan oleh timestamp bar tutup terakhir yang dilihat."""

    name = "scripted"

    def __init__(self, script: dict[int, Signal], lookback: int = 2) -> None:
        self.script = script
        self._lookback = lookback
        self.seen: list[pd.Timestamp] = []

    @property
    def lookback_bars(self) -> int:
        return self._lookback

    def signal(self, bars: pd.DataFrame) -> Signal:
        last = bars["timestamp"].iloc[-1]
        self.seen.append(last)
        return self.script.get(int(last.value // 1_000_000), Signal.FLAT)


class Feed:
    """Bar per jam dari T0. bar(i) = harga; forming bar ikut dikirim seperti exchange."""

    def __init__(self, client: FakeTokocryptoClient, prices: list[float]) -> None:
        self.client = client
        self.prices = prices
        self.set_time(0)

    def set_time(self, bar_index: int, minutes: int = 1) -> None:
        """Jam = open bar ke-bar_index + minutes; bar 0..bar_index tersedia (terakhir forming)."""
        self.now_ms = T0 + bar_index * HOUR + minutes * 60_000
        rows = []
        for i, p in enumerate(self.prices[: bar_index + 1]):
            rows.append([T0 + i * HOUR, p, p * 1.001, p * 0.999, p, 10.0])
        self.client.ohlcv_rows = rows
        self.client.server_time_ms = self.now_ms
        price = self.prices[bar_index]
        self.client.ticker = {
            "symbol": "BTC/USDT",
            "last": price,
            "bid": price,
            "ask": price,
            "timestamp": self.now_ms,
        }


@pytest.fixture
def settings(config_path):
    return load_settings(config_path, environ={})


def build(settings, project_dir: Path, prices, strategy, *, risk_overrides=None):
    holder = {}

    def factory(params):
        client = FakeTokocryptoClient(params)
        holder["client"] = client
        return client

    state: dict[str, float] = {"now": T0 / 1000}
    clock = lambda: state["now"]  # noqa: E731
    public = build_public_adapter(settings, client_factory=factory, clock=clock)
    feed = Feed(holder["client"], prices)
    paper = PaperAdapter(
        public,
        settings.costs,
        symbol="BTC/USDT",
        account_path=project_dir / settings.live.paper_account_path,
        initial_quote=settings.backtest.initial_equity,
        clock=clock,
    )
    risk_config = dataclasses.replace(settings.risk, **(risk_overrides or {}))
    risk = RiskManager(
        risk_config,
        settings.costs,
        stop_file=project_dir / "STOP",
        state_store=DailyStateStore(project_dir / settings.live.state_path),
    )
    runner = Runner(
        settings,
        paper,
        strategy,
        risk,
        OrderJournal(project_dir / settings.live.journal_path),
        Ledger(project_dir / settings.live.trades_csv),
        PositionStore(project_dir / settings.live.position_path),
        clock=clock,
        sleep=lambda _: None,
    )

    def advance(bar_index: int, minutes: int = 1) -> None:
        feed.set_time(bar_index, minutes)
        state["now"] = feed.now_ms / 1000

    return runner, holder["client"], advance


ALL_IN = {
    "position_fraction": 1.0,
    "max_position_fraction": 1.0,
    "stop_loss_fraction": 1.0,
    "take_profit_fraction": 1.0,
    "daily_loss_limit_fraction": 1.0,
}


def ts(i: int) -> int:
    return T0 + i * HOUR


# --------------------------------------------------------------------------- #
# Keputusan per bar, warmup, forming bar, stale, lubang
# --------------------------------------------------------------------------- #


def test_no_decision_until_warmup_and_forming_bar_is_excluded(settings, project_dir, caplog):
    strategy = Scripted({}, lookback=3)
    runner, client, advance = build(settings, project_dir, [100.0] * 10, strategy)
    advance(2)  # bar 0,1 tutup; bar 2 forming -> hanya 2 bar tutup < 3
    runner.start()
    with caplog.at_level(logging.INFO, logger="tradebot"):
        runner.iterate()
    assert strategy.seen == [] and "warmup" in caplog.text
    advance(3)  # bar 0..2 tutup
    runner.iterate()
    assert strategy.seen == [pd.Timestamp(ts(2), unit="ms", tz="UTC")], "bar forming tidak dilihat"
    runner.iterate()
    assert len(strategy.seen) == 1, "satu keputusan per bar tutup"
    advance(3, minutes=30)
    runner.iterate()
    assert len(strategy.seen) == 1


def test_missing_bar_waits_within_tolerance_then_is_stale(settings, project_dir, caplog):
    strategy = Scripted({}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 10, strategy)
    advance(3, minutes=1)  # 60 detik setelah bar 2 tutup, masih dalam toleransi 120 detik
    runner.start()
    client.ohlcv_rows = client.ohlcv_rows[:2]  # exchange belum memberi bar 2
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        runner.iterate()
    assert strategy.seen == [] and "stale" not in caplog.text, "menunggu, belum stale"
    advance(3, minutes=3)  # 180 detik: lewat toleransi
    client.ohlcv_rows = client.ohlcv_rows[:2]
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        runner.iterate()
    assert strategy.seen == [] and "stale" in caplog.text


def test_late_start_still_decides_the_last_closed_bar(settings, project_dir):
    """Bot yang mulai jam :10 harus memutuskan bar yang tutup jam :00, seperti backtest."""
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 10, strategy)
    advance(3, minutes=10)
    runner.start()
    runner.iterate()
    assert strategy.seen == [pd.Timestamp(ts(2), unit="ms", tz="UTC")]
    assert runner.position is not None


def test_bar_after_hole_gets_no_decision(settings, project_dir, caplog):
    strategy = Scripted({ts(5): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 10, strategy)
    advance(6)
    runner.start()
    client.ohlcv_rows = [r for r in client.ohlcv_rows if r[0] not in (ts(3), ts(4))]  # lubang
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        runner.iterate()
    assert strategy.seen == [] and "lubang" in caplog.text
    assert runner.position is None
    advance(7)
    runner.iterate()  # bar 6 tutup, berurutan setelah bar 5: keputusan normal lagi
    assert len(strategy.seen) == 1


# --------------------------------------------------------------------------- #
# Order: LONG/FLAT, jurnal sebelum kirim, ledger, posisi dipersist
# --------------------------------------------------------------------------- #


def test_long_then_flat_round_trip_with_journal_ledger_and_position(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.LONG, ts(4): Signal.FLAT}, lookback=2)
    runner, client, advance = build(
        settings, project_dir, [100.0, 100.0, 100.0, 110.0, 112.0, 112.0], strategy
    )
    advance(3)
    runner.start()
    runner.iterate()
    c = settings.costs
    assert runner.position is not None
    assert runner.position.entry_price == pytest.approx(110.0 * (1 + c.slippage_rate))
    assert runner.position.stop_loss == pytest.approx(runner.position.entry_price * 0.98)
    budget = 1000.0 * settings.risk.position_fraction
    spent = runner.position.amount * runner.position.entry_price * (1 + c.total_fee_rate)
    step_value = 0.00001 * runner.position.entry_price * (1 + c.total_fee_rate)
    assert budget - step_value <= spent <= budget, "dibulatkan ke bawah ke step exchange"
    saved = PositionStore(project_dir / settings.live.position_path).load()
    assert saved == runner.position
    journal = OrderJournal(project_dir / settings.live.journal_path).entries()
    assert [e["event"] for e in journal] == ["intent", "result"]
    assert journal[0]["reason"] == "signal" and journal[1]["status"] == "closed"
    rows = Ledger(project_dir / settings.live.trades_csv).rows()
    assert len(rows) == 1 and rows[0]["side"] == "buy" and rows[0]["fee_status"] == "reconciled"

    advance(4)
    runner.iterate()  # masih LONG: tidak ada order
    assert len(OrderJournal(project_dir / settings.live.journal_path).entries()) == 2
    advance(5)
    runner.iterate()  # FLAT
    assert runner.position is None
    assert PositionStore(project_dir / settings.live.position_path).load() is None
    rows = Ledger(project_dir / settings.live.trades_csv).rows()
    assert [r["side"] for r in rows] == ["buy", "sell"]
    assert runner.adapter.fetch_balance().total("BTC") == pytest.approx(0.0)


def test_intent_is_journaled_even_when_exchange_rejects(settings, project_dir, monkeypatch):
    from tradebot.exchange import InvalidOrderError

    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 6, strategy)
    advance(3)
    runner.start()

    def reject(*args, **kwargs):
        raise InvalidOrderError("ditolak (disimulasikan)")

    monkeypatch.setattr(runner.adapter, "create_order", reject)
    with pytest.raises(InvalidOrderError):
        runner.iterate()
    entries = OrderJournal(project_dir / settings.live.journal_path).entries()
    assert [e["event"] for e in entries] == ["intent"], "niat tercatat sebelum request keluar"


def test_lost_response_is_reconciled_never_resent(settings, project_dir, monkeypatch):
    from tradebot.exchange import OrderStateUnknownError

    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 6, strategy)
    advance(3)
    runner.start()
    real_create = runner.adapter.create_order
    calls = []

    def lost(*args, **kwargs):
        calls.append(kwargs.get("client_order_id"))
        real_create(*args, **kwargs)  # order MASUK di exchange, tapi jawabannya hilang
        raise OrderStateUnknownError("timeout (disimulasikan)")

    monkeypatch.setattr(runner.adapter, "create_order", lost)
    runner.iterate()
    assert len(calls) == 1, "tidak dikirim ulang"
    assert runner.position is not None, "order yang ternyata masuk diakui lewat client_order_id"
    events = [e["event"] for e in OrderJournal(project_dir / settings.live.journal_path).entries()]
    assert events == ["intent", "unknown", "reconciled"]
    assert len(Ledger(project_dir / settings.live.trades_csv).rows()) == 1


def test_restart_reconciles_pending_intent_from_journal(settings, project_dir):
    strategy = Scripted({}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 6, strategy)
    advance(3)
    # proses sebelumnya: intent tertulis, order masuk (ada di akun paper), lalu proses mati
    runner.adapter.connect()
    runner.adapter.create_order(
        "BTC/USDT",
        __import__("tradebot.exchange", fromlist=["OrderSide"]).OrderSide.BUY,
        __import__("tradebot.exchange", fromlist=["OrderType"]).OrderType.MARKET,
        0.1,
        client_order_id="tb-lama",
    )
    journal = OrderJournal(project_dir / settings.live.journal_path)
    journal.record_intent(
        "tb-lama",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        amount=0.1,
        reason="signal",
        time=runner.now(),
    )
    journal.record_intent(
        "tb-hilang",
        symbol="BTC/USDT",
        side="buy",
        order_type="market",
        amount=0.1,
        reason="signal",
        time=runner.now(),
    )
    before = client.count("create_order")
    runner.start()
    assert client.count("create_order") == before, "tidak ada yang dikirim ulang"
    assert journal.pending_intents() == []
    outcomes = {
        e["client_order_id"]: e["outcome"] for e in journal.entries() if e["event"] == "reconciled"
    }
    assert outcomes == {"tb-lama": "found", "tb-hilang": "not_found"}
    assert len(Ledger(project_dir / settings.live.trades_csv).rows()) == 1
    # saldo BTC ada tanpa catatan posisi: dianggap posisi dengan harga masuk = harga sekarang
    assert runner.position is not None and runner.position.amount == pytest.approx(0.1)


def test_position_record_without_balance_is_dropped(settings, project_dir, caplog):
    from tradebot.live.runner import PositionState

    strategy = Scripted({}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 6, strategy)
    advance(3)
    PositionStore(project_dir / settings.live.position_path).save(
        PositionState(0.5, 100.0, "2023-11-14T22:00:00+00:00", 98.0, 104.0, "x")
    )
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        runner.start()
    assert runner.position is None and "catatan dihapus" in caplog.text


# --------------------------------------------------------------------------- #
# Stop lapis 1 dari harga, dan kill switch lewat jalur runner
# --------------------------------------------------------------------------- #


def test_stop_loss_from_ticker_sells_between_bars(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    assert runner.position is not None
    client.ticker["last"] = client.ticker["bid"] = client.ticker["ask"] = 97.0  # < stop 98.15
    advance(3, minutes=20)
    client.ticker["last"] = client.ticker["bid"] = client.ticker["ask"] = 97.0
    runner.iterate()
    assert runner.position is None
    reasons = [
        e.get("reason")
        for e in OrderJournal(project_dir / settings.live.journal_path).entries()
        if e["event"] == "intent"
    ]
    assert reasons == ["signal", "stop_loss"]


def test_stop_file_halts_without_flatten(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    assert runner.position is not None
    (project_dir / "STOP").write_text("", encoding="utf-8")
    assert runner.run(max_iterations=5) == EXIT_KILL_SWITCH
    assert runner.position is not None, "file STOP: pengguna yang memutuskan, tidak flatten"


def test_daily_loss_halts_and_flattens(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(
        settings,
        project_dir,
        [100.0] * 8,
        strategy,
        risk_overrides={
            "position_fraction": 1.0,
            "max_position_fraction": 1.0,
            "stop_loss_fraction": 1.0,
        },
    )
    advance(3)
    assert runner.run(max_iterations=1) == EXIT_OK
    assert runner.position is not None
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 90.0  # rugi 10% > 3%
    advance(3, minutes=10)
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 90.0
    code = runner.run(max_iterations=2)
    assert code == EXIT_KILL_SWITCH
    assert runner.position is None, "flatten_on.daily_loss: true"
    reasons = [
        e.get("reason")
        for e in OrderJournal(project_dir / settings.live.journal_path).entries()
        if e["event"] == "intent"
    ]
    assert reasons == ["signal", "kill_switch"]


def test_runaway_orders_halt_without_new_order(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(
        settings, project_dir, [100.0] * 10, strategy, risk_overrides={"max_orders_per_minute": 1}
    )
    advance(3)
    runner.start()
    runner.iterate()  # LONG -> beli: order pertama di menit ini
    assert runner.position is not None
    # jam tidak maju; harga jatuh menembus stop -> runner ingin jual dalam menit yang sama
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 90.0
    code = runner.run(max_iterations=2)
    assert code == EXIT_KILL_SWITCH
    assert len(runner.adapter.account["orders"]) == 1, "order ke-2 dalam satu menit tidak dikirim"
    assert runner.position is not None, "runaway: tidak flatten, menambah order memperparah"


def test_consecutive_connection_failures_halt(settings, project_dir):
    strategy = Scripted({}, lookback=2)
    runner, client, advance = build(
        settings, project_dir, [100.0] * 8, strategy, risk_overrides={"max_consecutive_failures": 2}
    )
    advance(3)
    runner.start()
    # tiap fetch_ticker gagal 3x (retry adapter habis) -> satu kegagalan koneksi per iterasi
    client.failures["fetch_ticker"] = [ccxt.NetworkError("putus") for _ in range(50)]
    assert runner.run(max_iterations=5) == EXIT_KILL_SWITCH
    assert runner.iterations == 2


# --------------------------------------------------------------------------- #
# Paritas dengan backtest: data yang sama -> trade yang sama
# --------------------------------------------------------------------------- #


def test_paper_runner_matches_backtest_on_same_bars(settings, project_dir):
    prices = [100.0 + ((i * 7) % 11) - 5 + (3 if 12 <= i < 22 else 0) for i in range(40)]
    strategy = EmaCross(fast_period=2, slow_period=4, lookback_multiplier=2)  # jendela 8 bar
    runner, client, advance = build(settings, project_dir, prices, strategy, risk_overrides=ALL_IN)
    advance(1)
    runner.start()
    for i in range(1, 40):
        advance(i)  # bar 0..i-1 tutup, bar i forming; ticker = open bar i
        runner.iterate()
    live_rows = Ledger(project_dir / settings.live.trades_csv).rows()

    bars = frame_from_rows(
        [[ts(i), p, p * 1.001, p * 0.999, p, 10.0] for i, p in enumerate(prices)]
    )
    risk = RiskManager(dataclasses.replace(settings.risk, **ALL_IN), settings.costs)
    result = run_backtest(
        bars,
        EmaCross(2, 4, 2),
        risk,
        settings.costs,
        initial_equity=settings.backtest.initial_equity,
        bars_per_year=8760,
        symbol="BTC/USDT",
        timeframe="1h",
    )
    bt_fills = []
    for trade in result.trades:
        bt_fills.append(("buy", trade.entry_time, trade.entry_price))
        if trade.exit_reason.value != "end_of_data":
            bt_fills.append(("sell", trade.exit_time, trade.exit_price))
    live_fills = [
        (
            r["side"],
            pd.Timestamp(datetime.fromisoformat(r["timestamp"])).floor("h"),
            float(r["price"]),
        )
        for r in live_rows
    ]
    assert len(live_fills) == len(bt_fills) >= 4, (live_fills, bt_fills)
    for live, bt in zip(live_fills, bt_fills, strict=True):
        assert live[0] == bt[0]
        assert live[1] == bt[1]
        assert live[2] == pytest.approx(bt[2], rel=1e-9)


# --------------------------------------------------------------------------- #
# Temuan review: jual tidak terisi, restart di bar yang sama, fee dalam base, urutan halt
# --------------------------------------------------------------------------- #


def test_unfilled_or_partial_sell_keeps_position(settings, project_dir, monkeypatch):
    from tradebot.exchange import Order, OrderStatus, OrderType

    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.FLAT}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    held = runner.position.amount
    real_create = runner.adapter.create_order

    def make_sell(filled):
        def fake(symbol, side, order_type, amount, **kwargs):
            if side.value == "buy":
                return real_create(symbol, side, order_type, amount, **kwargs)
            return Order(
                id="x",
                client_order_id=kwargs.get("client_order_id"),
                symbol=symbol,
                side=side,
                type=OrderType.MARKET,
                amount=amount,
                price=None,
                stop_price=None,
                status=OrderStatus.CANCELED if filled == 0 else OrderStatus.CLOSED,
                filled=filled,
                average=100.0 if filled else None,
                cost=filled * 100.0,
                fee=0.0,
                fee_currency="USDT",
                timestamp=runner.now(),
            )

        return fake

    monkeypatch.setattr(runner.adapter, "create_order", make_sell(0.0))
    advance(4)
    runner.iterate()  # FLAT -> jual, tapi tidak terisi
    assert runner.position is not None and runner.position.amount == held
    assert PositionStore(project_dir / settings.live.position_path).load().amount == held

    monkeypatch.setattr(runner.adapter, "create_order", make_sell(held / 2))
    runner.last_decided_bar = None
    runner.iterate()  # terisi sebagian
    assert runner.position is not None
    assert runner.position.amount == pytest.approx(held / 2)


def test_restart_within_same_bar_does_not_redecide_it(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()  # beli
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 90.0
    runner.iterate()  # stop loss
    assert runner.position is None
    orders_before = len(runner.adapter.account["orders"])
    # proses baru di jam yang sama, store yang sama
    strategy2 = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner2, client2, advance2 = build(settings, project_dir, [100.0] * 8, strategy2)
    advance2(3)
    runner2.start()
    runner2.iterate()
    assert strategy2.seen == [], "bar 2 sudah diputuskan sebelum restart"
    assert len(runner2.adapter.account["orders"]) == orders_before
    assert runner2.position is None


def test_fee_charged_in_base_reduces_position_amount(settings, project_dir, monkeypatch):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    real_create = runner.adapter.create_order

    def fee_in_btc(symbol, side, order_type, amount, **kwargs):
        order = real_create(symbol, side, order_type, amount, **kwargs)
        # tiru Binance tanpa BNB: fee dipotong dari BTC yang diterima
        runner.adapter.account["balances"]["BTC"] -= amount * 0.001
        return dataclasses.replace(order, fee=amount * 0.001, fee_currency="BTC")

    monkeypatch.setattr(runner.adapter, "create_order", fee_in_btc)
    runner.iterate()
    assert runner.position is not None
    assert runner.position.amount == pytest.approx(runner.adapter.fetch_balance().free("BTC"))
    assert runner.position.amount < 0.999 * runner.adapter.account["orders"][0]["filled"] + 1e-9


def test_reconstructed_position_uses_ledger_entry_price(settings, project_dir):
    from tradebot.exchange import OrderSide, OrderType

    strategy = Scripted({}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.adapter.connect()
    order = runner.adapter.create_order(
        "BTC/USDT", OrderSide.BUY, OrderType.MARKET, 0.1, client_order_id="tb-x"
    )
    Ledger(project_dir / settings.live.trades_csv).record_fill(order)
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 98.5  # harga sudah turun 1.5% sejak masuk
    runner.start()
    assert runner.position is not None
    assert runner.position.entry_price == pytest.approx(order.average), (
        "dari ledger, bukan harga kini"
    )
    assert runner.position.client_order_id == "tb-x"
    assert runner.position.stop_loss == pytest.approx(order.average * 0.98)


def test_halt_cancels_open_orders_before_flatten(settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(
        settings,
        project_dir,
        [100.0] * 8,
        strategy,
        risk_overrides={
            "position_fraction": 1.0,
            "max_position_fraction": 1.0,
            "stop_loss_fraction": 1.0,
        },
    )
    advance(3)
    runner.start()
    runner.iterate()
    calls: list[str] = []
    real_cancel = runner.adapter.cancel_all_orders
    real_create = runner.adapter.create_order
    runner.adapter.cancel_all_orders = lambda symbol: (calls.append("cancel"), real_cancel(symbol))[
        1
    ]
    runner.adapter.create_order = lambda *a, **k: (calls.append("sell"), real_create(*a, **k))[1]
    for key in ("last", "bid", "ask"):
        client.ticker[key] = 90.0
    assert runner.run(max_iterations=2) == EXIT_KILL_SWITCH
    assert calls == ["cancel", "sell"]
    assert runner.position is None


def test_paper_runner_matches_backtest_with_stops_and_targets(settings, project_dir):
    """Paritas kedua: stop 2% dan target 4% aktif, harga dengan lompatan yang menembus level."""
    prices = [100.0] * 4 + [103.0, 105.0, 101.0, 97.0, 99.0, 104.0, 108.0, 106.0, 100.0, 95.0]
    prices += [96.0] * 6
    lows = [p * 0.985 if i in (7, 13) else p * 0.999 for i, p in enumerate(prices)]
    highs = [p * 1.03 if i in (10,) else p * 1.001 for i, p in enumerate(prices)]
    script = {ts(i): Signal.LONG for i in range(2, len(prices))}
    strategy = Scripted(script, lookback=2)
    overrides = {
        "position_fraction": 1.0,
        "max_position_fraction": 1.0,
        "daily_loss_limit_fraction": 1.0,
    }
    runner, client, advance = build(
        settings, project_dir, prices, strategy, risk_overrides=overrides
    )
    advance(1)
    runner.start()
    for i in range(1, len(prices)):
        advance(i)  # tick di open bar i: keputusan dari bar i-1
        runner.iterate()
        # tick di dalam bar i: low dulu (pesimistis), lalu high
        for price in (lows[i], highs[i]):
            for key in ("last", "bid", "ask"):
                client.ticker[key] = price
            runner.iterate()
    live = OrderJournal(project_dir / settings.live.journal_path).entries()
    live_fills = [(e["side"], e["reason"]) for e in live if e["event"] == "intent"]

    bars = frame_from_rows([[ts(i), p, highs[i], lows[i], p, 10.0] for i, p in enumerate(prices)])
    risk = RiskManager(dataclasses.replace(settings.risk, **overrides), settings.costs)
    result = run_backtest(
        bars,
        Scripted(script, lookback=2),
        risk,
        settings.costs,
        initial_equity=settings.backtest.initial_equity,
        bars_per_year=8760,
        symbol="BTC/USDT",
        timeframe="1h",
        limits=runner.limits,
    )
    bt_fills = []
    for trade in result.trades:
        bt_fills.append(("buy", "signal"))
        if trade.exit_reason.value != "end_of_data":
            bt_fills.append(("sell", trade.exit_reason.value))
    assert live_fills == bt_fills
    assert {"stop_loss", "take_profit"} <= {r for _, r in bt_fills}, "kedua level harus terpicu"
    live_rows = Ledger(project_dir / settings.live.trades_csv).rows()
    bt_amounts = [t.amount for t in result.trades]
    live_amounts = [float(r["amount"]) for r in live_rows if r["side"] == "buy"]
    # Jumlah pertama identik. Setelah stop, runner mengisi di harga tick (low/high) sedangkan
    # backtest di level stop, jadi kas dan jumlah berikutnya boleh berbeda sedikit; arah
    # perbedaannya selalu ke sisi runner yang lebih pesimistis untuk stop loss.
    assert live_amounts[0] == pytest.approx(bt_amounts[0], rel=1e-9)
    assert live_amounts == pytest.approx(bt_amounts, rel=0.02)
