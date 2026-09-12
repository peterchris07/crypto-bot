"""Fixture bersama dan aturan pytest untuk seluruh project.

Dua hal yang diatur di sini:

1. Test bertanda @pytest.mark.testnet dilewati kalau kunci testnet tidak ada.
2. Test yang dilewati tidak boleh tersembunyi di balik hasil hijau: di akhir
   run selalu dicetak ringkasan berapa yang dilewati dan kenapa.
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
    logging:
      dir: logs
      level: INFO
    """
).lstrip()


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


def pytest_terminal_summary(terminalreporter, exitstatus: int, config: pytest.Config) -> None:
    skipped = terminalreporter.stats.get("skipped", [])
    if not skipped:
        return
    reasons: Counter[str] = Counter()
    for report in skipped:
        longrepr = report.longrepr
        reason = longrepr[2] if isinstance(longrepr, tuple) else str(longrepr)
        reasons[reason.removeprefix("Skipped: ")] += 1
    terminalreporter.section("PERHATIAN: ada test yang dilewati", sep="=", yellow=True, bold=True)
    terminalreporter.write_line(
        f"{len(skipped)} test dilewati. Hasil hijau di atas TIDAK mencakup test ini."
    )
    for reason, count in reasons.most_common():
        terminalreporter.write_line(f"  {count} test: {reason}")


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
