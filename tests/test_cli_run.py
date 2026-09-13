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


def test_run_refuses_live_mode_until_enabled(
    project_dir: Path, config_path: Path, capsys, monkeypatch
):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k" * 20)
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s" * 20)
    code = cli.main(
        ["--config", str(config_path), "run", "--i-know-what-im-doing", "--iterations", "1"]
    )
    assert code == cli.EXIT_CONFIG_ERROR
    assert "live.enabled masih false" in capsys.readouterr().err


def test_status_in_live_mode_reads_the_live_supervisor_and_needs_the_flag(
    project_dir: Path, config_path: Path, fake_public, capsys, monkeypatch
):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k" * 20)
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s" * 20)
    # tanpa flag: aturan dua kunci berlaku untuk status juga
    code = cli.main(["--config", str(config_path), "status"])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "menolak" in capsys.readouterr().err

    (project_dir / "state").mkdir(exist_ok=True)
    (project_dir / "state" / "paper_supervisor.pid").write_text("999999999\n")
    code = cli.main(["--config", str(config_path), "status", "--i-know-what-im-doing"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out
    assert "mode live" in out
    # pid paper tidak dibaca sebagai proses live
    assert "proses: supervisor tidak berjalan (tidak ada state/live_supervisor.pid)" in out
    (project_dir / "state" / "live_supervisor.pid").write_text("999999999\n")
    code = cli.main(["--config", str(config_path), "status", "--i-know-what-im-doing"])
    out = capsys.readouterr().out
    assert "MATI (pid file basi)" in out


def test_run_live_with_real_default_config_writes_only_under_live_folders(
    tmp_path: Path, capsys, monkeypatch
):
    """Config asli + overlay local.yaml lewat local-set + klien palsu: satu iterasi live
    menulis ke state/live, logs/live, dan tidak menyentuh catatan paper di state/ dan logs/."""
    import time
    from datetime import UTC, datetime

    from tradebot.exchange import factory as factory_module

    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    root = Path(__file__).resolve().parent.parent
    (tmp_path / "config").mkdir()
    config_path = tmp_path / "config" / "default.yaml"
    config_path.write_text((root / "config" / "default.yaml").read_text(encoding="utf-8"))
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k" * 24)
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s" * 24)
    today = datetime.now(tz=UTC).date().isoformat()
    code = cli.main(
        [
            "--config",
            str(config_path),
            "local-set",
            "live.enabled=true",
            f"live.api_key_verified_date={today}",
            "risk.position_fraction=0.25",
            "--i-know-what-im-doing",
        ]
    )
    assert code == cli.EXIT_OK, capsys.readouterr()

    holder = {}
    now_ms = T0 + 5 * HOUR + 60_000
    original = factory_module.build_adapter

    def patched(settings, **kwargs):
        def client_factory(params):
            client = FakeTokocryptoClient(params)
            client.server_time_ms = now_ms
            client.ohlcv_rows = [[T0 + i * HOUR, 100.0, 100.1, 99.9, 100.0, 10.0] for i in range(6)]
            client.balance = {
                "free": {"USDT": 120.0, "BTC": 0.0},
                "used": {"USDT": 0.0, "BTC": 0.0},
                "total": {"USDT": 120.0, "BTC": 0.0},
            }
            holder["client"] = client
            return client

        return original(
            settings, client_factory=client_factory, clock=lambda: now_ms / 1000, **kwargs
        )

    monkeypatch.setattr(factory_module, "build_adapter", patched)
    code = cli.main(
        ["--config", str(config_path), "run", "--i-know-what-im-doing", "--iterations", "2"]
    )
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err + captured.out
    assert holder["client"].count("create_order") == 0  # jendela EMA belum penuh: FLAT

    assert (tmp_path / "logs" / "live" / "tradebot.log").exists()
    # jendela EMA belum penuh, jadi belum ada posisi; yang pasti ditulis: state risk harian
    assert (tmp_path / "state" / "live" / "risk_state.json").exists()
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert all(
        p.startswith(("config/", "logs/live/", "state/live/", "trades/live/")) for p in written
    ), written
    assert not (tmp_path / "logs" / "tradebot.log").exists()
    assert not (tmp_path / "state" / "position.json").exists()
    assert not (tmp_path / "state" / "risk_state.json").exists()
    assert not (tmp_path / "state" / "paper_account.json").exists()
    assert not (tmp_path / "trades" / "trades.csv").exists()

    code = cli.main(["--config", str(config_path), "status", "--i-know-what-im-doing"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out
    assert "mode live" in out and "live.enabled=true" in out
    assert "catatan mode ini di state/live/ dan trades/live/" in out


def test_backtest_ignores_trading_mode_like_fetch_data(
    project_dir: Path, config_path: Path, capsys, monkeypatch
):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.delenv("TOKOCRYPTO_API_KEY", raising=False)
    monkeypatch.delenv("TOKOCRYPTO_API_SECRET", raising=False)
    code = cli.main(["--config", str(config_path), "backtest"])
    err = capsys.readouterr().err
    # sampai ke cek cache (exit 5), bukan ditolak karena mode/flag/kunci (exit 2)
    assert code == cli.EXIT_DATA_ERROR, err
    assert "fetch-data" in err


def test_run_rejects_zero_iterations(project_dir: Path, config_path: Path, fake_public, capsys):
    assert (
        cli.main(["--config", str(config_path), "run", "--iterations", "0"])
        == cli.EXIT_CONFIG_ERROR
    )


def test_compare_paper_and_checklist_commands(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    # tanpa fill: compare-paper menolak dengan pesan jelas
    code = cli.main(["--config", str(config_path), "compare-paper"])
    assert code == cli.EXIT_DATA_ERROR and "belum punya fill" in capsys.readouterr().err
    # checklist kosong: belum selesai, exit 8
    code = cli.main(["--config", str(config_path), "paper-checklist"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_CHECKLIST_INCOMPLETE and out.count("[BELUM]") == 4


def test_compare_paper_reports_signed_bias(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    """Ledger dengan fill yang konsisten lebih buruk dari backtest -> exit 7 dan vonis bias."""
    import json

    from tradebot.data.cache import OhlcvCache
    from tradebot.data.ohlcv import frame_from_rows

    prices = [100.0] * 4 + [110.0] * 6 + [90.0] * 6 + [110.0] * 6 + [90.0] * 6 + [110.0] * 6
    rows = [[T0 + i * HOUR, p, p * 1.001, p * 0.999, p, 10.0] for i, p in enumerate(prices)]
    cache = OhlcvCache(project_dir / "data")
    cache.save(cache.path_for("tokocrypto", "BTC/USDT", "1h"), frame_from_rows(rows))
    # strategi EMA 2/4 jendela 8: ganti config
    text = (
        config_path.read_text()
        .replace("fast_period: 20", "fast_period: 2")
        .replace("slow_period: 50", "slow_period: 4")
        .replace("lookback_multiplier: 5", "lookback_multiplier: 2")
    )
    config_path.write_text(text)
    from tradebot.backtest import run_backtest
    from tradebot.config import load_settings
    from tradebot.risk import RiskManager
    from tradebot.strategy import build_strategy

    settings = load_settings(config_path, environ={})
    result = run_backtest(
        frame_from_rows(rows),
        build_strategy(settings.strategy),
        RiskManager(settings.risk, settings.costs),
        settings.costs,
        initial_equity=1000.0,
        bars_per_year=8760,
        symbol="BTC/USDT",
        timeframe="1h",
    )
    assert len(result.trades) >= 3
    # ledger paper buatan: setiap fill 20 bps lebih buruk dari backtest
    ledger_path = project_dir / "trades" / "trades.csv"
    ledger_path.parent.mkdir()
    journal_path = project_dir / "state" / "orders.jsonl"
    journal_path.parent.mkdir()
    header = (
        "timestamp,pair,side,amount,price,quote_value,fee,fee_currency,order_id,"
        "client_order_id,fee_status,recorded_at"
    )
    lines = [header]
    events = []
    n = 0
    for trade in result.trades:
        fills = [("buy", trade.entry_time, trade.entry_price * 1.002, "signal")]
        if trade.exit_reason.value != "end_of_data":
            fills.append(
                ("sell", trade.exit_time, trade.exit_price * 0.998, trade.exit_reason.value)
            )
        for side, when, price, reason in fills:
            n += 1
            cid = f"c{n}"
            stamp = (when + __import__("pandas").Timedelta(30, unit="s")).isoformat()
            lines.append(
                f"{stamp},BTC/USDT,{side},{trade.amount},{price},{trade.amount * price},"
                f"0,USDT,paper-{n},{cid},reconciled,{stamp}"
            )
            events.append(
                {"event": "intent", "client_order_id": cid, "reason": reason, "time": stamp}
            )
    ledger_path.write_text("\n".join(lines) + "\n")
    journal_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    code = cli.main(["--config", str(config_path), "compare-paper"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_BIAS_DETECTED, out
    assert "BIAS SATU ARAH TERDETEKSI" in out and "LEBIH BURUK" in out
    assert "+20.0 bps" in out and "merugikan 100%" in out
    assert "tanpa pasangan: paper 0, backtest 0" in out


def test_status_combines_everything_in_one_command(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    # keadaan kosong: semua bagian tetap tercetak, tidak ada yang meledak
    code = cli.main(["--config", str(config_path), "status"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "proses: supervisor tidak berjalan" in out
    assert "posisi: FLAT" in out
    assert "trade: 0 beli, 0 jual" in out
    assert "ledger: LENGKAP" in out
    assert "checklist paper run:" in out and out.count("[BELUM]") == 4
    assert "compare-paper: belum ada fill" in out

    # setelah beberapa iterasi run: akun paper, log terakhir, dan pid basi terlihat
    (project_dir / "state").mkdir(exist_ok=True)
    (project_dir / "state" / "paper_supervisor.pid").write_text("999999999\n")
    assert cli.main(["--config", str(config_path), "run", "--iterations", "2"]) == cli.EXIT_OK
    capsys.readouterr()
    (project_dir / "STOP").write_text("")
    code = cli.main(["--config", str(config_path), "status"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "MATI (pid file basi)" in out
    assert "file STOP ada" in out
    # hitungan mulai ulang supervisor terlihat tanpa membaca log
    import json

    (project_dir / "state" / "paper_supervisor.json").write_text(
        json.dumps(
            {
                "started_at": "2026-09-12T10:00:00Z",
                "restarts": 3,
                "max_restarts": 10,
                "last_restart": "2026-09-12T12:34:56Z",
                "last_exit_code": 3,
                "updated_at": "2026-09-12T12:34:56Z",
                "running": True,
            }
        )
    )
    code = cli.main(["--config", str(config_path), "status"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "supervisor: mulai ulang 3 kali (batas 10), terakhir 2026-09-12T12:34:56Z" in out
    assert "exit terakhir 3" in out and "PERHATIAN: mulai ulang" in out
    assert "akun paper: 1000.0000 USDT" in out
    assert "log terakhir:" in out


def test_run_live_enabled_but_preflight_failing_sends_nothing(
    project_dir: Path, config_path: Path, capsys, monkeypatch
):
    """live.enabled true, dua kunci ada, tetapi api_key_verified_date kosong: preflight gagal,
    exit 9, tidak ada order."""
    from tradebot.exchange import factory as factory_module

    text = config_path.read_text().replace("enabled: false", "enabled: true")
    config_path.write_text(text)
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("TOKOCRYPTO_API_KEY", "k" * 24)
    monkeypatch.setenv("TOKOCRYPTO_API_SECRET", "s" * 24)
    holder = {}
    original = factory_module.build_adapter

    def patched(settings, **kwargs):
        def client_factory(params):
            holder["client"] = FakeTokocryptoClient(params)
            return holder["client"]

        return original(
            settings, client_factory=client_factory, clock=lambda: BASE_MS / 1000, **kwargs
        )

    monkeypatch.setattr(factory_module, "build_adapter", patched)
    code = cli.main(
        ["--config", str(config_path), "run", "--i-know-what-im-doing", "--iterations", "1"]
    )
    captured = capsys.readouterr()
    assert code == cli.EXIT_PREFLIGHT_FAILED
    assert "PREFLIGHT GAGAL" in captured.err and "api_key_verified_date kosong" in captured.out
    assert holder["client"].count("create_order") == 0
