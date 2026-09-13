"""`tradebot pipe-test`: satu putaran beli-stop-batal-jual berukuran minimum dengan klien palsu.

Yang dibuktikan: urutan batalkan-stop-sebelum-jual, stop gagal terpasang lalu jual balik,
jual gagal lalu laporan keadaan, dan bahwa tanpa konfirmasi atau dengan preflight gagal
tidak ada satu pun order yang dikirim.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tests.test_live_runner import LIVE_ENV
from tradebot import cli
from tradebot.config import load_settings
from tradebot.exchange import FatalExchangeError, OrderType
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter
from tradebot.ledger import Ledger
from tradebot.live.journal import OrderJournal
from tradebot.live.pipe_test import (
    CONFIRM_PHRASE,
    EXIT_CONFIG_ERROR,
    EXIT_EXCHANGE_ERROR,
    EXIT_OK,
    EXIT_PREFLIGHT_FAILED,
    PipeTest,
)
from tradebot.risk import DailyStateStore, RiskManager

TODAY = datetime.now(tz=UTC).date().isoformat()


@pytest.fixture
def live_settings(project_dir: Path, config_path: Path):
    # seperti live-setup pemilik: tanggal verifikasi hari ini dan pecahan posisi trial 0.25
    # (0.10 x 39,85 USDT = 3,99 USDT < min_cost 5 USDT, preflight menolak)
    text = (
        config_path.read_text()
        .replace('api_key_verified_date: ""', f'api_key_verified_date: "{TODAY}"')
        .replace("position_fraction: 0.10", "position_fraction: 0.25")
    )
    config_path.write_text(text)
    return load_settings(config_path, i_know_what_im_doing=True, environ=LIVE_ENV)


def build_pipe(settings, project_dir: Path, *, answer: str = CONFIRM_PHRASE, hold: int = 60):
    holder = {}
    state = {"now": BASE_MS / 1000}

    def factory(params):
        client = FakeTokocryptoClient(params)
        client.server_time_ms = BASE_MS
        # akun pemilik: 39,85 USDT, tanpa BTC
        client.balance = {
            "free": {"USDT": 39.85, "BTC": 0.0},
            "used": {"USDT": 0.0, "BTC": 0.0},
            "total": {"USDT": 39.85, "BTC": 0.0},
        }
        client.ticker = {
            "symbol": "BTC/USDT",
            "last": 77_000.0,
            "bid": 76_990.0,
            "ask": 77_010.0,
            "timestamp": BASE_MS,
        }
        holder["client"] = client
        return client

    adapter = TokocryptoAdapter(
        settings.exchange,
        settings.exchange.live,
        settings.credentials,
        sandbox=False,
        allow_mainnet_trading=True,
        client_factory=factory,
        sleep=lambda _: None,
        clock=lambda: state["now"],
    )
    risk = RiskManager(
        settings.risk,
        settings.costs,
        stop_file=project_dir / "STOP",
        state_store=DailyStateStore(project_dir / settings.live.state_path),
    )
    outputs: list[str] = []
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        state["now"] += seconds

    pipe = PipeTest(
        settings,
        adapter,
        risk,
        OrderJournal(project_dir / settings.live.journal_path),
        Ledger(project_dir / settings.live.trades_csv),
        clock=lambda: state["now"],
        sleep=sleep,
        ask=lambda prompt: answer,
        out=outputs.append,
        hold_seconds=hold,
        idr_rate=16_500.0,
    )
    return pipe, holder["client"], outputs, sleeps


def call_names(client):
    return [name for name, _, _ in client.calls]


def create_calls(client):
    """(type, side, params) untuk setiap create_order, urut."""
    return [(args[1], args[2], args[5]) for name, args, _ in client.calls if name == "create_order"]


def test_happy_path_cancels_stop_before_selling_and_ends_flat(live_settings, project_dir):
    pipe, client, out, sleeps = build_pipe(live_settings, project_dir)
    code = pipe.run()
    text = "\n".join(out)
    assert code == EXIT_OK, text
    creates = create_calls(client)
    assert [(t, s) for t, s, _ in creates] == [
        ("market", "buy"),
        ("limit", "sell"),  # STOP_LOSS_LIMIT dikirim sebagai limit + stopPrice
        ("market", "sell"),
    ]
    assert "stopPrice" in creates[1][2]
    names = call_names(client)
    cancel_idx = names.index("cancel_order")
    sell_idx = [i for i, n in enumerate(names) if n == "create_order"][2]
    assert cancel_idx < sell_idx, "stop dibatalkan DULU, baru jual"
    # verifikasi lewat fetch_open_orders, sesudah pasang dan sesudah batal
    fetches = [i for i, n in enumerate(names) if n == "fetch_open_orders"]
    stop_create_idx = [i for i, n in enumerate(names) if n == "create_order"][1]
    assert any(stop_create_idx < i < cancel_idx for i in fetches), "stop diverifikasi ada"
    assert any(cancel_idx < i < sell_idx for i in fetches), "stop diverifikasi hilang"
    # ukuran minimum: 5 USDT / 77010 ~ 6.5e-5 -> dibulatkan naik ke step 1e-5 = 7e-5
    assert pipe.state.amount == pytest.approx(7e-05)
    assert 60 in sleeps, "menunggu 60 detik"
    # akun flat, tidak ada order terbuka, ledger lengkap
    assert client.balance["total"]["BTC"] == pytest.approx(0.0, abs=1e-12)
    assert client.open_orders == []
    ledger = Ledger(project_dir / live_settings.live.trades_csv)
    assert ledger.is_complete()
    sides = [r["side"] for r in ledger.rows()]
    assert sides.count("buy") >= 1 and sides.count("sell") >= 1
    events = OrderJournal(project_dir / live_settings.live.journal_path).entries()
    intents = [(e["type"], e["side"], e["reason"]) for e in events if e["event"] == "intent"]
    assert intents == [
        ("market", "buy", "pipe_test"),
        ("stop_loss_limit", "sell", "pipe_test_stop"),
        ("market", "sell", "pipe_test_exit"),
    ]
    assert any(e["event"] == "cancel" and e["outcome"] == "canceled" for e in events)
    assert "VONIS: JALUR ORDER TERBUKTI" in text
    assert "bukti stop lapis 2: order" in text and "terlihat di fetch_open_orders" in text
    assert "untung/rugi bersih" in text and "Rp" in text
    assert "fee terbayar per baris ledger" in text


def test_intent_is_journaled_before_the_order_is_sent(live_settings, project_dir, monkeypatch):
    pipe, client, out, _ = build_pipe(live_settings, project_dir)
    journal_path = project_dir / live_settings.live.journal_path
    seen_before_send = []
    original = client.create_order

    def spy(*args, **kwargs):
        entries = OrderJournal(journal_path).entries()
        seen_before_send.append([e["event"] for e in entries][-1])
        return original(*args, **kwargs)

    monkeypatch.setattr(client, "create_order", spy)
    assert pipe.run() == EXIT_OK
    assert seen_before_send == ["intent", "intent", "intent"], "niat ditulis sebelum tiap kirim"


def test_stop_that_fails_to_place_means_immediate_sell_back(
    live_settings, project_dir, monkeypatch
):
    pipe, client, out, _ = build_pipe(live_settings, project_dir)
    original = pipe.adapter.create_order

    def failing(symbol, side, order_type, amount, **kwargs):
        if order_type is OrderType.STOP_LOSS_LIMIT:
            raise FatalExchangeError("STOP_LOSS_LIMIT ditolak venue")
        return original(symbol, side, order_type, amount, **kwargs)

    monkeypatch.setattr(pipe.adapter, "create_order", failing)
    code = pipe.run()
    text = "\n".join(out)
    assert code == EXIT_EXCHANGE_ERROR
    assert [(t, s) for t, s, _ in create_calls(client)] == [("market", "buy"), ("market", "sell")]
    assert client.balance["total"]["BTC"] == pytest.approx(0.0, abs=1e-12)
    assert client.open_orders == []
    assert "GAGAL di langkah 4" in text and "jual balik" in text
    assert "TIDAK memegang BTC (flat)" in text
    assert "order terbuka tertinggal: tidak ada" in text


def test_stop_not_visible_in_open_orders_is_treated_as_failure(
    live_settings, project_dir, monkeypatch
):
    pipe, client, out, _ = build_pipe(live_settings, project_dir)
    monkeypatch.setattr(client, "fetch_open_orders", lambda *a, **k: [])
    code = pipe.run()
    text = "\n".join(out)
    assert code == EXIT_EXCHANGE_ERROR
    assert "GAGAL di langkah 4" in text and "tidak muncul di fetch_open_orders" in text
    names = call_names(client)
    assert names.count("cancel_order") >= 1, "stop yang tidak terlihat tetap dicoba dibatalkan"
    assert [(t, s) for t, s, _ in create_calls(client)][-1] == ("market", "sell"), "jual balik"
    assert client.balance["total"]["BTC"] == pytest.approx(0.0, abs=1e-12)


def test_sell_failure_reports_state_and_rearms_the_stop(live_settings, project_dir, monkeypatch):
    pipe, client, out, _ = build_pipe(live_settings, project_dir)
    original = pipe.adapter.create_order
    calls = {"sell_market": 0}

    def failing(symbol, side, order_type, amount, **kwargs):
        if order_type is OrderType.MARKET and side.value == "sell":
            calls["sell_market"] += 1
            raise FatalExchangeError("jual ditolak: saldo terkunci")
        return original(symbol, side, order_type, amount, **kwargs)

    monkeypatch.setattr(pipe.adapter, "create_order", failing)
    code = pipe.run()
    text = "\n".join(out)
    assert code == EXIT_EXCHANGE_ERROR
    assert "GAGAL di langkah 6" in text
    assert "Anda SEDANG memegang" in text
    assert client.balance["total"]["BTC"] > 0
    # stop pengaman dipasang lagi supaya posisi tidak telanjang, dan itu dilaporkan
    assert "stop pengaman dipasang lagi" in text
    stops = [o for o in client.open_orders if o.get("stopPrice") is not None]
    assert len(stops) == 1
    assert "order terbuka tertinggal: [('" in text
    assert "yang harus dilakukan manual" in text and "batalkan order terbuka" in text
    assert "jual " in text and "di aplikasi" in text


def test_without_exact_confirmation_nothing_is_sent(live_settings, project_dir):
    for answer in ("uji pipa", "UJI PIPA ya", "", "YA"):
        pipe, client, out, _ = build_pipe(live_settings, project_dir, answer=answer)
        assert pipe.run() == EXIT_CONFIG_ERROR
        assert client.count("create_order") == 0
        assert "dibatalkan: tidak ada order yang dikirim" in "\n".join(out)


def test_confirmation_screen_shows_size_rupiah_and_round_trip_cost(live_settings, project_dir):
    pipe, client, out, _ = build_pipe(live_settings, project_dir, answer="tidak")
    pipe.run()
    text = "\n".join(out)
    assert "BELI 7e-05 BTC market" in text
    assert "Rp" in text and "perkiraan biaya bolak-balik" in text
    assert "0.4044%" in text and "0.15%" in text


def test_failed_preflight_sends_nothing(project_dir: Path, config_path: Path):
    settings = load_settings(config_path, i_know_what_im_doing=True, environ=LIVE_ENV)
    assert settings.live.api_key_verified_date == ""
    pipe, client, out, _ = build_pipe(settings, project_dir)
    assert pipe.run() == EXIT_PREFLIGHT_FAILED
    assert client.count("create_order") == 0
    assert "PREFLIGHT GAGAL" in "\n".join(out)


def test_refuses_when_account_already_holds_base_or_has_open_orders(live_settings, project_dir):
    pipe, client, out, _ = build_pipe(live_settings, project_dir)
    client.balance["total"]["BTC"] = 0.001
    client.balance["free"]["BTC"] = 0.001
    assert pipe.run() == EXIT_CONFIG_ERROR
    assert client.count("create_order") == 0
    assert "harus mulai flat" in "\n".join(out)


def test_cli_pipe_test_refuses_outside_live_mode(project_dir: Path, config_path: Path, capsys):
    code = cli.main(["--config", str(config_path), "pipe-test"])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "hanya di mode live" in capsys.readouterr().err
