"""Fixture bersama dan aturan pytest untuk seluruh project.

Tiga hal yang diatur di sini:

1. Test bertanda @pytest.mark.testnet atau @pytest.mark.tokocrypto dilewati kalau
   kuncinya tidak ada.
2. Test bertanda @pytest.mark.network ditandai GAGAL DIJALANKAN kalau host
   Tokocrypto tidak terjangkau dari mesin ini (diperiksa sekali di awal sesi
   lewat jalur HTTP yang sama dengan ccxt). Ini bukan skip kunci dan bukan lulus:
   di container tanpa jaringan, suite pernah tampak hijau padahal test jaringan
   yang rusak tidak pernah dijalankan.
3. Apa pun yang tidak dijalankan tidak boleh tersembunyi di balik hasil hijau:
   di akhir run selalu dicetak ringkasan terpisah untuk skip kunci, gagal
   dijalankan karena jaringan, dan test network yang tidak dipilih (-m).
"""

from __future__ import annotations

import logging
import os
import textwrap
from collections import Counter
from pathlib import Path

import pytest
from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parent.parent
TESTNET_VARS = ("BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET")
TESTNET_SKIP_REASON = (
    "kunci testnet tidak ada: isi BINANCE_TESTNET_API_KEY dan BINANCE_TESTNET_API_SECRET di .env"
)
# marker -> (variabel .env yang wajib ada, alasan skip)
KEYED_MARKERS: dict[str, tuple[tuple[str, ...], str]] = {
    "testnet": (TESTNET_VARS, TESTNET_SKIP_REASON),
    "tokocrypto": (
        ("TOKOCRYPTO_API_KEY", "TOKOCRYPTO_API_SECRET"),
        "kunci Tokocrypto tidak ada: isi TOKOCRYPTO_API_KEY dan TOKOCRYPTO_API_SECRET di .env",
    ),
}

MINIMAL_CONFIG = textwrap.dedent(
    """
    exchange:
      symbol: BTC/USDT
      timeframe: 1h
      recv_window_ms: 5000
      max_time_drift_ms: 1000
      time_sync_samples: 3
      time_sync_max_rtt_ms: 300
      time_sync_max_attempts: 6
      rate_limit: true
      retry:
        max_attempts: 3
        base_delay_seconds: 0.01
        max_delay_seconds: 0.05
      testnet:
        id: binance
        market_data_url: ""
      live:
        id: tokocrypto
        market_data_url: "https://data.example/api/v3"
    data:
      cache_dir: data
      history_start: "2025-01-01"
      max_gap_bars: 6
    strategy:
      name: ema_cross
      fast_period: 20
      slow_period: 50
      lookback_multiplier: 5
    risk:
      position_fraction: 0.10
      max_position_fraction: 0.25
      daily_loss_limit_fraction: 0.03
      stop_loss_fraction: 0.02
      take_profit_fraction: 0.04
      exchange_stop_multiplier: 2.0
      exchange_stop_limit_offset_fraction: 0.005
      max_orders_per_minute: 5
      max_consecutive_failures: 5
      stop_file: STOP
      flatten_on:
        daily_loss: true
        runaway_orders: false
        connection_failures: false
        stop_file: false
    costs:
      taker_fee_rate: 0.0015
      tax_rate: 0.0021
      exchange_fee_rate: 0.000444
      slippage_rate: 0.0015
      stress_multiplier: 2.0
    backtest:
      initial_equity: 1000.0
      bars_per_year: 8760
    live:
      loop_interval_seconds: 30
      stale_bar_tolerance_seconds: 120
      journal_path: state/orders.jsonl
      state_path: state/risk_state.json
      position_path: state/position.json
      paper_account_path: state/paper_account.json
      trades_csv: trades/trades.csv
      bias_min_trades: 5
      bias_adverse_share: 0.75
      checklist_min_fills: 4
      enabled: false
      api_key_verified_date: ""
      max_key_age_days: 90
      min_cycles_before_normal: 3
      stage_path: state/live_stage.json
    logging:
      dir: logs
      level: INFO
    """
).lstrip()


NETWORK_PROBE_URLS = (
    "https://www.tokocrypto.com/open/v1/common/time",
    "https://www.tokocrypto.site/api/v3/time",
)
NETWORK_UNREACHABLE_PREFIX = "GAGAL DIJALANKAN karena jaringan: "


def network_unreachable_reason() -> str | None:
    """None kalau semua host bisa dihubungi lewat jalur yang sama dengan ccxt (requests,
    proxy dan CA dari environment). Kalau tidak, satu kalimat penyebabnya."""
    import requests

    for url in NETWORK_PROBE_URLS:
        try:
            requests.get(url, timeout=8)
        except requests.RequestException as exc:
            detail = str(exc).splitlines()[0]
            return f"{url} tidak terjangkau dari mesin ini ({type(exc).__name__}: {detail[:160]})"
    return None


def keys_present(names: tuple[str, ...]) -> bool:
    """Apakah semua variabel ada di .env project atau environment proses. Nilai tidak dicetak."""
    from_file = {}
    env_file = PROJECT_ROOT / ".env"
    if env_file.is_file():
        from_file = {k: v for k, v in dotenv_values(env_file).items() if v}
    merged = {**from_file, **os.environ}
    return all(merged.get(name) for name in names)


def testnet_keys_present() -> bool:
    return keys_present(TESTNET_VARS)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for marker_name, (names, reason) in KEYED_MARKERS.items():
        if keys_present(names):
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if item.get_closest_marker(marker_name) is not None:
                item.add_marker(skip)

    network_items = [item for item in items if item.get_closest_marker("network") is not None]
    if network_items:
        reason = network_unreachable_reason()
        config.stash[NETWORK_REASON_KEY] = reason
        if reason is not None:
            skip = pytest.mark.skip(reason=NETWORK_UNREACHABLE_PREFIX + reason)
            for item in network_items:
                item.add_marker(skip)


NETWORK_REASON_KEY = pytest.StashKey[str | None]()


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    skipped = terminalreporter.stats.get("skipped", [])
    deselected = terminalreporter.stats.get("deselected", [])
    key_reasons: Counter[str] = Counter()
    network_reasons: Counter[str] = Counter()
    for report in skipped:
        longrepr = report.longrepr
        reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
        reason = reason.removeprefix("Skipped: ")
        if reason.startswith(NETWORK_UNREACHABLE_PREFIX):
            network_reasons[reason.removeprefix(NETWORK_UNREACHABLE_PREFIX)] += 1
        else:
            key_reasons[reason] += 1
    network_deselected = sum(
        1
        for item in deselected
        if getattr(item, "get_closest_marker", None)
        and item.get_closest_marker("network") is not None
    )

    if network_reasons:
        total = sum(network_reasons.values())
        terminalreporter.section(
            "PERHATIAN: test GAGAL DIJALANKAN karena jaringan", sep="=", red=True, bold=True
        )
        terminalreporter.write_line(
            f"{total} test network TIDAK dijalankan: host tidak terjangkau dari mesin ini. "
            "Ini bukan lulus dan bukan skip kunci; hasilnya belum diketahui."
        )
        for reason, count in network_reasons.most_common():
            terminalreporter.write_line(f"  {count} test: {reason}")
    if key_reasons:
        total = sum(key_reasons.values())
        terminalreporter.section(
            "PERHATIAN: ada test yang dilewati", sep="=", yellow=True, bold=True
        )
        terminalreporter.write_line(
            f"{total} test dilewati karena kunci. Hasil hijau di atas TIDAK mencakup test ini."
        )
        for reason, count in key_reasons.most_common():
            terminalreporter.write_line(f"  {count} test: {reason}")
    if network_deselected:
        terminalreporter.section("test network tidak dipilih", sep="-", yellow=True)
        terminalreporter.write_line(
            f"{network_deselected} test network tidak dipilih (-m). Jalankan tanpa -m untuk "
            "hasil sungguhan di mesin yang punya jaringan ke Tokocrypto."
        )


@pytest.fixture(autouse=True)
def _fresh_tradebot_logger():
    """setup_logging mematikan propagate pada logger 'tradebot'; kembalikan setelah tiap test.

    Tanpa ini, caplog di test yang berjalan setelah test_logging tidak melihat apa pun.
    """
    yield
    logger = logging.getLogger("tradebot")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Root project sementara dengan config/default.yaml minimal dan tanpa .env."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "default.yaml").write_text(MINIMAL_CONFIG, encoding="utf-8")
    return tmp_path


@pytest.fixture
def config_path(project_dir: Path) -> Path:
    return project_dir / "config" / "default.yaml"
