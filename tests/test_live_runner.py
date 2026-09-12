"""Tahap 8 di atas klien Tokocrypto palsu BERKUNCI (mode live), tanpa satu pun order asli.

Yang dibuktikan: order pertama live dipaksa ke ukuran minimum exchange; lapis 2 dipasang
segera setelah posisi terbentuk dan DIBATALKAN sebelum order keluar; stop yang sudah
tereksekusi saat bot mati atau di antara iterasi membuat posisi, ledger, dan jurnal
konsisten tanpa order jual tambahan; venue tanpa dukungan stop membuat live gagal keras;
ledger dua fase tetap berlaku di live.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tests.test_runner import Feed, Scripted, ts
from tradebot.config import load_settings
from tradebot.exchange import (
    FatalExchangeError,
    InsufficientFundsError,
    OrderSide,
)
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter
from tradebot.ledger import Ledger
from tradebot.live.journal import OrderJournal
from tradebot.live.preflight import minimum_order_amount
from tradebot.live.runner import EXIT_EXCHANGE_ERROR, PositionStore, Runner
from tradebot.live.stage import MINIMUM, NORMAL, LiveStageStore
from tradebot.risk import DailyStateStore, RiskManager
from tradebot.strategy.base import Signal

HOUR = 3_600_000
T0 = BASE_MS - (BASE_MS % HOUR)
LIVE_ENV = {
    "TRADING_MODE": "live",
    "TOKOCRYPTO_API_KEY": "toko-key-0123456789abcdef",
    "TOKOCRYPTO_API_SECRET": "toko-secret-0123456789abcdef",
}


@pytest.fixture
def live_settings(config_path):
    return load_settings(config_path, i_know_what_im_doing=True, environ=LIVE_ENV)


def build_live(settings, project_dir: Path, prices, strategy, *, mutate_market=None):
    holder = {}
    state = {"now": T0 / 1000}
    clock = lambda: state["now"]  # noqa: E731

    def factory(params):
        client = FakeTokocryptoClient(params)
        # akun live yang bersih: hanya quote, belum ada base
        client.balance = {
            "free": {"USDT": 1000.0, "BTC": 0.0},
            "used": {"USDT": 0.0, "BTC": 0.0},
            "total": {"USDT": 1000.0, "BTC": 0.0},
        }
        if mutate_market:
            mutate_market(client)
        holder["client"] = client
        return client

    adapter = TokocryptoAdapter(
        settings.exchange,
        settings.exchange.live,
        settings.credentials,
        sandbox=False,
        allow_mainnet_trading=True,  # jalur test; di src hanya factory mode live yang memberi ini
        client_factory=factory,
        sleep=lambda _: None,
        clock=clock,
    )
    feed = Feed(holder["client"], prices)
    risk = RiskManager(
        settings.risk,
        settings.costs,
        stop_file=project_dir / "STOP",
        state_store=DailyStateStore(project_dir / settings.live.state_path),
    )
    runner = Runner(
        settings,
        adapter,
        strategy,
        risk,
        OrderJournal(project_dir / settings.live.journal_path),
        Ledger(project_dir / settings.live.trades_csv),
        PositionStore(project_dir / settings.live.position_path),
        stage_store=LiveStageStore(project_dir / settings.live.stage_path),
        clock=clock,
        sleep=lambda _: None,
    )

    def advance(bar_index: int, minutes: int = 1) -> None:
        feed.set_time(bar_index, minutes)
        state["now"] = feed.now_ms / 1000

    return runner, holder["client"], advance


def open_stop_orders(client):
    return [o for o in client.open_orders if o.get("stopPrice") is not None]


def call_names(client):
    return [name for name, _, _ in client.calls]


# --------------------------------------------------------------------------- #
# C4: ukuran minimum untuk order pertama, naik hanya secara sadar
# --------------------------------------------------------------------------- #


def test_first_live_order_is_exchange_minimum_not_sizing(live_settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.FLAT}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    assert runner.position is not None
    fill = 100.0 * (1 + live_settings.costs.slippage_rate)
    expected = minimum_order_amount(runner.limits, fill)
    assert runner.position.amount == pytest.approx(expected)
    assert expected * fill == pytest.approx(5.0, abs=0.2), "sekitar min_cost 5 USDT"
    sized = runner.risk.size_position(1000.0, fill, runner.limits)
    assert expected < sized, "jauh di bawah hasil sizing"
    store = LiveStageStore(project_dir / live_settings.live.stage_path)
    assert store.load().cycles_completed == 0
    advance(4)
    runner.iterate()  # FLAT -> jual, siklus 1 selesai
    assert runner.position is None
    assert store.load().cycles_completed == 1


def test_live_size_normal_refused_until_enough_cycles(live_settings, project_dir):
    store = LiveStageStore(project_dir / live_settings.live.stage_path)
    for _ in range(2):
        store.record_cycle()
    with pytest.raises(ValueError, match="baru 2 siklus"):
        store.set_stage(NORMAL, min_cycles=3)
    store.record_cycle()
    assert store.set_stage(NORMAL, min_cycles=3).stage == NORMAL
    assert store.set_stage(MINIMUM, min_cycles=0).stage == MINIMUM


def test_after_normal_stage_sizing_is_used(live_settings, project_dir):
    store = LiveStageStore(project_dir / live_settings.live.stage_path)
    for _ in range(3):
        store.record_cycle()
    store.set_stage(NORMAL, min_cycles=3)
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    fill = 100.0 * (1 + live_settings.costs.slippage_rate)
    assert runner.position.amount == pytest.approx(
        runner.risk.size_position(1000.0, fill, runner.limits), rel=1e-6
    )


# --------------------------------------------------------------------------- #
# C2: lapis 2 dipasang setelah masuk, dibatalkan sebelum keluar
# --------------------------------------------------------------------------- #


def test_exchange_stop_placed_after_entry_wider_than_bot_stop(live_settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    stops = open_stop_orders(client)
    assert len(stops) == 1
    entry = runner.position.entry_price
    cfg = live_settings.risk
    expected_stop = entry * (1 - cfg.stop_loss_fraction * cfg.exchange_stop_multiplier)
    assert stops[0]["stopPrice"] == pytest.approx(expected_stop, abs=0.01)
    assert stops[0]["stopPrice"] < runner.position.stop_loss, "lebih lebar dari lapis 1"
    assert stops[0]["price"] == pytest.approx(
        stops[0]["stopPrice"] * (1 - cfg.exchange_stop_limit_offset_fraction), abs=0.01
    )
    assert runner.position.stop_order_id == stops[0]["id"]
    saved = PositionStore(project_dir / live_settings.live.position_path).load()
    assert saved.stop_order_id == stops[0]["id"] and saved.stop_price == pytest.approx(
        expected_stop, abs=0.01
    )
    intents = [
        (e["type"], e["reason"])
        for e in OrderJournal(project_dir / live_settings.live.journal_path).entries()
        if e["event"] == "intent"
    ]
    assert intents == [("market", "signal"), ("stop_loss_limit", "exchange_stop")]


def test_exchange_stop_is_canceled_before_exit_order(live_settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.FLAT}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    stop_id = runner.position.stop_order_id
    client.calls.clear()
    advance(4)
    runner.iterate()  # FLAT
    names = call_names(client)
    assert "cancel_order" in names and "create_order" in names
    assert names.index("cancel_order") < names.index("create_order"), "batalkan stop DULU"
    assert client.orders[stop_id]["status"] == "canceled"
    assert open_stop_orders(client) == [] and runner.position is None
    events = OrderJournal(project_dir / live_settings.live.journal_path).entries()
    cancels = [e for e in events if e["event"] == "cancel"]
    assert cancels and cancels[0]["order_id"] == stop_id and cancels[0]["outcome"] == "canceled"


def test_exit_without_cancel_is_rejected_by_locked_balance(live_settings, project_dir, monkeypatch):
    """Bukti eksplisit urutan: kalau stop masih terbuka, jual ditolak (saldo terkunci); runner
    tidak pernah masuk ke jalur itu karena membatalkan dulu."""
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.FLAT}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    real_create = client.create_order

    def locked_aware(symbol, type, side, amount, price=None, params=None):
        if side == "sell" and type == "market" and open_stop_orders(client):
            raise __import__("ccxt").InsufficientFunds("Account has insufficient balance")
        return real_create(symbol, type, side, amount, price, params)

    monkeypatch.setattr(client, "create_order", locked_aware)
    # jalur salah: jual tanpa membatalkan stop -> ditolak
    with pytest.raises(InsufficientFundsError):
        runner._submit(OrderSide.SELL, runner.position.amount, "uji-urutan-salah")
    # jalur runner: batalkan dulu, lalu jual -> berhasil
    advance(4)
    runner.iterate()
    assert runner.position is None


# --------------------------------------------------------------------------- #
# C2: stop sudah tereksekusi saat bot mati atau di antara iterasi
# --------------------------------------------------------------------------- #


def execute_stop_on_exchange(client, stop_id: str, price: float) -> None:
    order = client.orders[stop_id]
    order["status"] = "closed"
    order["filled"] = order["amount"]
    order["average"] = price
    order["cost"] = order["amount"] * price
    order["info"]["status"] = 2
    client.open_orders = [o for o in client.open_orders if o["id"] != stop_id]
    client.trades.append(
        {
            "id": f"t{stop_id}",
            "order": stop_id,
            "symbol": "BTC/USDT",
            "side": "sell",
            "amount": order["amount"],
            "price": price,
            "cost": order["amount"] * price,
            "fee": {"cost": order["amount"] * price * 0.0015, "currency": "USDT"},
            "timestamp": client.server_time_ms,
        }
    )


def test_stop_executed_while_bot_was_down_is_absorbed_on_start(live_settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    stop_id = runner.position.stop_order_id
    stop_cid = runner.position.stop_client_order_id
    # bot mati; harga runtuh; stop terisi di exchange
    execute_stop_on_exchange(client, stop_id, 95.5)
    client.balance["free"]["BTC"] = 0.0
    client.balance["total"]["BTC"] = 0.0
    orders_before = client.count("create_order")

    runner2, client2, advance2 = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    # klien palsu baru: salin keadaan akun exchange
    client2.orders, client2.open_orders, client2.trades = (
        client.orders,
        client.open_orders,
        client.trades,
    )
    client2.balance = client.balance
    advance2(3, minutes=30)
    runner2.start()
    assert runner2.position is None, "posisi sudah ditutup oleh stop exchange"
    assert PositionStore(project_dir / live_settings.live.position_path).load() is None
    rows = Ledger(project_dir / live_settings.live.trades_csv).rows()
    sells = [r for r in rows if r["side"] == "sell"]
    assert sells and sells[-1]["client_order_id"] == stop_cid
    assert sells[-1]["fee_status"] == "reconciled", "ledger dua fase: fee diambil dari trades"
    events = OrderJournal(project_dir / live_settings.live.journal_path).entries()
    assert any(
        e["event"] == "reconciled"
        and e["client_order_id"] == stop_cid
        and e["outcome"] == "executed"
        for e in events
    )
    assert client2.count("create_order") == 0 and client.count("create_order") == orders_before
    assert LiveStageStore(project_dir / live_settings.live.stage_path).load().cycles_completed == 1


def test_stop_executed_between_iterations_means_no_sell_order(live_settings, project_dir):
    strategy = Scripted({ts(2): Signal.LONG, ts(3): Signal.FLAT}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    stop_id = runner.position.stop_order_id
    execute_stop_on_exchange(client, stop_id, 95.5)
    before = client.count("create_order")
    advance(4)
    runner.iterate()  # FLAT: cancel -> tidak ditemukan -> ternyata tereksekusi
    assert runner.position is None
    assert client.count("create_order") == before, "tidak ada order jual tambahan"
    rows = Ledger(project_dir / live_settings.live.trades_csv).rows()
    assert [r["side"] for r in rows][-1] == "sell"


# --------------------------------------------------------------------------- #
# C2: venue tanpa dukungan stop -> live gagal keras; paper tidak memasang lapis 2
# --------------------------------------------------------------------------- #


def test_live_refuses_venue_without_stop_support(live_settings, project_dir):
    def no_stops(client):
        client.markets["BTC/USDT"]["info"]["orderTypes"] = ["LIMIT", "MARKET"]

    strategy = Scripted({}, lookback=2)
    runner, client, advance = build_live(
        live_settings, project_dir, [100.0] * 8, strategy, mutate_market=no_stops
    )
    advance(3)
    with pytest.raises(FatalExchangeError, match="STOP_LOSS_LIMIT"):
        runner.start()
    assert runner.run(max_iterations=1) == EXIT_EXCHANGE_ERROR


def test_paper_never_places_exchange_stops(config_path, project_dir):
    from tests.test_runner import build

    settings = load_settings(config_path, environ={})
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build(settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    runner.iterate()
    assert runner.position is not None and runner.position.stop_order_id is None
    assert not runner.adapter.supports_exchange_stops
    intents = [
        e["type"]
        for e in OrderJournal(project_dir / settings.live.journal_path).entries()
        if e["event"] == "intent"
    ]
    assert intents == ["market"]


# --------------------------------------------------------------------------- #
# C5: ledger dua fase di live
# --------------------------------------------------------------------------- #


def test_ledger_stays_pending_until_trades_are_fetched(live_settings, project_dir, monkeypatch):
    strategy = Scripted({ts(2): Signal.LONG}, lookback=2)
    runner, client, advance = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    advance(3)
    runner.start()
    monkeypatch.setattr(client, "fetch_my_trades", lambda *a, **k: [])
    runner.iterate()
    ledger = Ledger(project_dir / live_settings.live.trades_csv)
    assert not ledger.is_complete(), "respons order Tokocrypto tanpa fee: baris pending"
    assert ledger.pending_rows()[0]["side"] == "buy"
    monkeypatch.undo()
    # restart: rekonsiliasi saat start mengisi fee dari fetch_my_trades
    runner2, client2, advance2 = build_live(live_settings, project_dir, [100.0] * 8, strategy)
    client2.orders, client2.open_orders, client2.trades = (
        client.orders,
        client.open_orders,
        client.trades,
    )
    client2.balance = client.balance
    advance2(3, minutes=30)
    runner2.start()
    assert ledger.is_complete()
