"""Tahap 3: perintah fetch-data dari ujung ke ujung dengan klien palsu, tanpa jaringan."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot import cli
from tradebot.exchange import factory

HOUR = 3_600_000
T0 = BASE_MS - (BASE_MS % HOUR)
START_ISO = "2023-11-14T22:00:00Z"  # T0: 1 700 000 000 000 ms dibulatkan ke bar 1h


def rows(count: int, *, skip: set[int] = frozenset()):
    return [[T0 + i * HOUR, 1.0, 2.0, 0.5, 1.5 + i, 10.0] for i in range(count) if i not in skip]


@pytest.fixture
def fake_public(monkeypatch: pytest.MonkeyPatch):
    """build_public_adapter memakai klien palsu dengan jam server 30 jam 5 menit setelah T0.

    Klien mengembalikan 31 bar: bar ke-31 (buka T0+30h) adalah bar yang sedang berjalan
    menurut jam server itu, seperti exchange sungguhan yang selalu menyertakan bar berjalan.
    """
    state: dict[str, object] = {"rows": rows(31)}
    original = factory.build_public_adapter

    def factory_fn(settings, **kwargs):
        def client_factory(params):
            client = FakeTokocryptoClient(params)
            client.server_time_ms = T0 + 30 * HOUR + 5 * 60_000
            client.ohlcv_rows = [list(r) for r in state["rows"]]
            if state.get("fail"):
                client.fail_next("fetch_ohlcv", *state["fail"])
            state["client"] = client
            return client

        return original(
            settings,
            client_factory=client_factory,
            clock=lambda: (T0 + 30 * HOUR + 5 * 60_000) / 1000,
            **kwargs,
        )

    monkeypatch.setattr(factory, "build_public_adapter", factory_fn)
    return state


def test_fetch_data_writes_cache_and_prints_summary(
    project_dir: Path, config_path: Path, fake_public, capsys: pytest.CaptureFixture[str]
):
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out
    cache_file = project_dir / "data" / "tokocrypto" / "BTC-USDT_1h.parquet"
    assert cache_file.exists()
    assert (project_dir / "data" / "tokocrypto" / "BTC-USDT_1h.gaps.json").exists()
    assert "venue: tokocrypto-mainnet-public" in out
    assert "bar di cache: 30 (" in out and "30 baru" in out
    assert "gap: 0 (0 bar hilang)" in out
    # klien memberi 31 bar; bar ke-31 masih berjalan menurut jam server dan tidak disimpan
    assert "PERHATIAN" not in out
    assert "TRADING_MODE diabaikan" in out


def test_fetch_data_warns_when_history_starts_after_requested_start(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", "2023-11-14"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "PERHATIAN: data dimulai 22 bar setelah awal yang diminta" in out
    assert "gap: 0 (0 bar hilang)" in out, "kekurangan di depan bukan gap"


def test_fetch_data_ignores_trading_mode_and_never_needs_keys(
    project_dir: Path, config_path: Path, fake_public, capsys, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TRADING_MODE", "testnet")
    monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "k" * 20)
    monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "s" * 20)
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "tanpa kunci" in out
    client = fake_public["client"]
    assert "apiKey" not in client.params
    assert "k" * 20 not in out


def test_fetch_data_end_before_server_time_is_respected(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    code = cli.main(
        [
            "--config",
            str(config_path),
            "fetch-data",
            "--start",
            START_ISO,
            "--end",
            "2023-11-15T08:00:00Z",
        ]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "bar di cache: 10 (" in out
    from tradebot.data.cache import OhlcvCache

    report = OhlcvCache(project_dir / "data").load_gaps(
        project_dir / "data" / "tokocrypto" / "BTC-USDT_1h.parquet"
    )
    assert report["requested_end"] == "2023-11-15T08:00:00+00:00"


@pytest.mark.parametrize(
    "env",
    [
        {"TRADING_MODE": "testnet"},  # tanpa kunci testnet
        {"TRADING_MODE": "live"},  # tanpa flag
        {"TRADING_MODE": "live", "TOKOCRYPTO_API_KEY": "k" * 20, "TOKOCRYPTO_API_SECRET": "s" * 20},
    ],
)
def test_fetch_data_runs_whatever_env_says_and_never_carries_keys(
    project_dir: Path, config_path: Path, fake_public, capsys, monkeypatch: pytest.MonkeyPatch, env
):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    captured = capsys.readouterr()
    assert code == cli.EXIT_OK, captured.err
    assert "tanpa kunci" in captured.out
    assert "apiKey" not in fake_public["client"].params


def test_fetch_data_lists_gaps_in_output(project_dir: Path, config_path: Path, fake_public, capsys):
    fake_public["rows"] = rows(30, skip={4, 5})
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "gap: 1 (2 bar hilang)" in out
    assert "2 bar hilang:" in out


def test_fetch_data_exits_5_on_long_gap_and_writes_nothing(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    fake_public["rows"] = rows(30, skip=set(range(4, 12)))
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    captured = capsys.readouterr()
    assert code == cli.EXIT_DATA_ERROR
    assert "DATA ERROR" in captured.err and "max_gap_bars=6" in captured.err
    assert not (project_dir / "data").exists()


def test_fetch_data_end_is_capped_at_server_time(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    code = cli.main(
        ["--config", str(config_path), "fetch-data", "--start", START_ISO, "--end", "2030-01-01"]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "bar di cache: 30 (" in out, "akhir di masa depan tidak boleh melahirkan gap palsu"


def test_fetch_data_rejects_bad_dates_before_touching_exchange(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", "kemarin"])
    captured = capsys.readouterr()
    assert code == cli.EXIT_CONFIG_ERROR
    assert "ISO 8601" in captured.err
    assert "client" not in fake_public


def test_fetch_data_exits_5_on_missing_price_and_writes_nothing(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    data = rows(31)
    data[3][4] = None
    fake_public["rows"] = data
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    captured = capsys.readouterr()
    assert code == cli.EXIT_DATA_ERROR
    assert "DATA ERROR" in captured.err and "NaN" in captured.err
    assert "Traceback" not in captured.err
    assert not (project_dir / "data").exists()


def test_fetch_data_exits_3_when_network_dies_mid_download(
    project_dir: Path, config_path: Path, fake_public, capsys
):
    import ccxt

    fake_public["fail"] = [ccxt.NetworkError("putus") for _ in range(5)]
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    captured = capsys.readouterr()
    assert code == cli.EXIT_EXCHANGE_ERROR
    assert "EXCHANGE ERROR" in captured.err
    assert not (project_dir / "data").exists()


def test_fetch_data_exit_code_on_exchange_failure(
    project_dir: Path, config_path: Path, fake_public, capsys, monkeypatch: pytest.MonkeyPatch
):
    from tradebot.exchange.errors import RetryableExchangeError

    def failing(settings, **kwargs):
        raise RetryableExchangeError("jaringan putus (disimulasikan)")

    monkeypatch.setattr(factory, "build_public_adapter", failing)
    code = cli.main(["--config", str(config_path), "fetch-data", "--start", START_ISO])
    assert code == cli.EXIT_EXCHANGE_ERROR
    assert "EXCHANGE ERROR" in capsys.readouterr().err
