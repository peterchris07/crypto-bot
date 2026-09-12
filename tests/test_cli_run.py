"""Tahap 7: perintah run di mode paper dengan klien palsu; mode live ditolak sampai tahap 8."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot import cli
from tradebot.exchange import factory

HOUR = 3_600_000
T0 = BASE_MS - (BASE_MS % HOUR)


@pytest.fixture
def fake_public(monkeypatch):
    import time

    monkeypatch.setattr(time, "sleep", lambda seconds: None)  # jangan tidur 30 detik antar iterasi
    original = factory.build_public_adapter

    def patched(settings, **kwargs):
        def client_factory(params):
            client = FakeTokocryptoClient(params)
            client.server_time_ms = T0 + 5 * HOUR + 60_000
            client.ohlcv_rows = [[T0 + i * HOUR, 100.0, 100.1, 99.9, 100.0, 10.0] for i in range(6)]
            return client

        return original(
            settings,
            client_factory=client_factory,
            clock=lambda: (T0 + 5 * HOUR + 60_000) / 1000,
            **kwargs,
        )

    monkeypatch.setattr(factory, "build_public_adapter", patched)


def test_run_paper_for_a_few_iterations_creates_state_files(
    project_dir: Path, config_path: Path, fake_public
):
    code = cli.main(["--config", str(config_path), "run", "--iterations", "2"])
    assert code == cli.EXIT_OK
    assert (project_dir / "state" / "paper_account.json").exists()
    assert (project_dir / "logs" / "tradebot.log").exists()


def test_run_stops_with_exit_6_on_stop_file(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    (project_dir / "STOP").write_text("", encoding="utf-8")
    code = cli.main(["--config", str(config_path), "run", "--iterations", "3"])
    assert code == cli.EXIT_KILL_SWITCH
    assert "kill switch" in capsys.readouterr().err


def test_run_refuses_live_mode_until_stage_8(
    project_dir: Path, config_path: Path, capsys, monkeypatch
):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k" * 20)
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s" * 20)
    code = cli.main(
        ["--config", str(config_path), "run", "--i-know-what-im-doing", "--iterations", "1"]
    )
    assert code == cli.EXIT_CONFIG_ERROR
    assert "tahap 8" in capsys.readouterr().err


def test_run_rejects_zero_iterations(project_dir: Path, config_path: Path, fake_public, capsys):
    assert (
        cli.main(["--config", str(config_path), "run", "--iterations", "0"])
        == cli.EXIT_CONFIG_ERROR
    )
