"""Tahap 8 C3: preflight tanpa order. Butir kunci: tanggal verifikasi izin, baca saldo,
pasangan dan minimum notional, sizing, jam, dukungan stop."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot import cli
from tradebot.config import load_settings
from tradebot.exchange import MarketLimits
from tradebot.exchange.factory import build_adapter
from tradebot.live.preflight import (
    API_MANAGEMENT_CHECKLIST,
    check_key_verified_date,
    format_preflight,
    minimum_order_amount,
    run_preflight,
)
from tradebot.risk import RiskManager

LOCAL_CLOCK_S = BASE_MS / 1000
TODAY = datetime(2026, 9, 12, tzinfo=UTC)


def with_key_date(settings, value: str):
    return dataclasses.replace(
        settings, live=dataclasses.replace(settings.live, api_key_verified_date=value)
    )


def test_key_verified_date_rules(config_path):
    settings = load_settings(config_path, environ={})
    empty = check_key_verified_date(settings, TODAY)
    assert not empty.ok and "kosong" in empty.detail
    assert "withdrawal MATI" in empty.detail and "pembatasan IP" in empty.detail
    assert API_MANAGEMENT_CHECKLIST in empty.detail
    old = check_key_verified_date(with_key_date(settings, "2026-06-01"), TODAY)
    assert not old.ok and "103 hari lalu" in old.detail and "batas 90 hari" in old.detail
    fresh = check_key_verified_date(with_key_date(settings, "2026-09-01"), TODAY)
    assert fresh.ok and "11 hari lalu" in fresh.detail


def test_minimum_order_amount_respects_min_cost_min_amount_and_step():
    limits = MarketLimits("BTC/USDT", "BTC", "USDT", 0.00001, 0.00001, 5.0, 0.01)
    amount = minimum_order_amount(limits, 100.0)
    assert amount * 100.0 >= 5.0 and amount == pytest.approx(0.05, abs=0.00002)
    assert round(amount / 0.00001, 6) == int(round(amount / 0.00001, 6))
    high = MarketLimits("BTC/USDT", "BTC", "USDT", 0.001, 0.001, 5.0, 0.01)
    assert minimum_order_amount(high, 100.0) == pytest.approx(0.05)  # min_cost menang
    assert minimum_order_amount(high, 100_000.0) == pytest.approx(0.001)  # min_amount menang


def test_preflight_in_paper_mode_reports_what_it_can(config_path, project_dir):
    settings = with_key_date(load_settings(config_path, environ={}), "2026-09-01")
    adapter = build_adapter(
        settings, client_factory=FakeTokocryptoClient, clock=lambda: LOCAL_CLOCK_S
    )
    checks = run_preflight(
        settings, adapter, RiskManager(settings.risk, settings.costs), today=TODAY
    )
    by_key = {c.key: c for c in checks}
    assert by_key["key_verified"].ok and by_key["clock"].ok and by_key["market"].ok
    assert by_key["stops"].ok, "venue publik di balik paper mengiklankan STOP_LOSS_LIMIT"
    assert by_key["balance"].ok and not by_key["balance"].applicable
    assert by_key["sizing"].ok and "order pertama live" in by_key["sizing"].detail
    text = format_preflight(checks, settings)
    assert "[n/a  ]" in text and "butir kunci live baru teruji di mode live" in text


def test_preflight_in_live_mode_reads_balance_and_never_orders(config_path, project_dir):
    env = {
        "TRADING_MODE": "live",
        "TOKOCRYPTO_API_KEY": "toko-key-0123456789abcdef",
        "TOKOCRYPTO_API_SECRET": "toko-secret-0123456789abcdef",
    }
    settings = with_key_date(
        load_settings(config_path, i_know_what_im_doing=True, environ=env), "2026-09-01"
    )
    holder = {}

    def factory(params):
        holder["client"] = FakeTokocryptoClient(params)
        return holder["client"]

    adapter = build_adapter(settings, client_factory=factory, clock=lambda: LOCAL_CLOCK_S)
    checks = run_preflight(
        settings, adapter, RiskManager(settings.risk, settings.costs), today=TODAY
    )
    assert all(c.ok for c in checks), format_preflight(checks, settings)
    by_key = {c.key: c for c in checks}
    assert by_key["balance"].applicable and "1000.0000 USDT" in by_key["balance"].detail
    assert "SIAP" in format_preflight(checks, settings)
    client = holder["client"]
    assert client.count("create_order") == 0 and client.count("fetch_balance") == 1
    assert not any(name.lower().startswith("withdraw") for name, _, _ in client.calls)


def test_preflight_fails_when_balance_unreadable(config_path, project_dir):
    import ccxt

    env = {
        "TRADING_MODE": "live",
        "TOKOCRYPTO_API_KEY": "toko-key-0123456789abcdef",
        "TOKOCRYPTO_API_SECRET": "toko-secret-0123456789abcdef",
    }
    settings = with_key_date(
        load_settings(config_path, i_know_what_im_doing=True, environ=env), "2026-09-01"
    )

    def factory(params):
        client = FakeTokocryptoClient(params)
        client.fail_next("fetch_balance", ccxt.AuthenticationError("Invalid API-key"))
        return client

    adapter = build_adapter(settings, client_factory=factory, clock=lambda: LOCAL_CLOCK_S)
    checks = run_preflight(
        settings, adapter, RiskManager(settings.risk, settings.costs), today=TODAY
    )
    by_key = {c.key: c for c in checks}
    assert not by_key["balance"].ok and "BELUM SIAP" in format_preflight(checks, settings)


def test_preflight_command_exit_code(project_dir: Path, config_path: Path, capsys, monkeypatch):
    from tradebot.exchange import factory as factory_module

    original = factory_module.build_public_adapter
    monkeypatch.setattr(
        factory_module,
        "build_public_adapter",
        lambda settings, **kw: original(
            settings, client_factory=FakeTokocryptoClient, clock=lambda: LOCAL_CLOCK_S, **kw
        ),
    )
    code = cli.main(["--config", str(config_path), "preflight"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_PREFLIGHT_FAILED
    assert "[GAGAL]" in out and "api_key_verified_date kosong" in out


def test_live_size_command(project_dir: Path, config_path: Path, capsys):
    from tradebot.live.stage import LiveStageStore

    assert cli.main(["--config", str(config_path), "live-size"]) == cli.EXIT_OK
    assert "ukuran order live: minimum" in capsys.readouterr().out
    assert (
        cli.main(["--config", str(config_path), "live-size", "--normal"]) == cli.EXIT_CONFIG_ERROR
    )
    assert "baru 0 siklus" in capsys.readouterr().err
    store = LiveStageStore(project_dir / "state" / "live_stage.json")
    for _ in range(3):
        store.record_cycle()
    assert cli.main(["--config", str(config_path), "live-size", "--normal"]) == cli.EXIT_OK
    assert "ukuran order live: normal" in capsys.readouterr().out
    assert cli.main(["--config", str(config_path), "live-size", "--minimum"]) == cli.EXIT_OK
    assert (
        cli.main(["--config", str(config_path), "live-size", "--normal", "--minimum"])
        == cli.EXIT_CONFIG_ERROR
    )
