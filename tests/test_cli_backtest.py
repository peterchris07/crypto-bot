"""Tahap 5: perintah backtest membaca cache parquet, tanpa jaringan, dan selalu menampilkan
buy-and-hold. --stress menggandakan fee, bursa, slippage; pajak tetap."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

from tradebot import cli
from tradebot.data.cache import OhlcvCache
from tradebot.data.ohlcv import frame_from_rows

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def write_cache(project_dir: Path, count: int = 600) -> Path:
    rng = np.random.default_rng(7)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, count)))
    rows = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        rows.append([T0 + i * HOUR, o, max(o, c) * 1.002, min(o, c) * 0.998, c, 10.0])
    cache = OhlcvCache(project_dir / "data")
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    cache.save(path, frame_from_rows(rows))
    return path


def test_backtest_without_cache_says_run_fetch_data(project_dir: Path, config_path: Path, capsys):
    code = cli.main(["--config", str(config_path), "backtest"])
    err = capsys.readouterr().err
    assert code == cli.EXIT_DATA_ERROR
    assert "fetch-data" in err


def test_backtest_prints_strategy_and_buy_and_hold(project_dir: Path, config_path: Path, capsys):
    write_cache(project_dir)
    code = cli.main(["--config", str(config_path), "backtest"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK, out
    assert "buy-and-hold" in out
    assert "all-in per putaran 1.1088%" in out
    assert "jendela 250 bar" in out
    assert "sharpe" in out and "basis 8760 bar/tahun" in out
    assert "biaya terbayar" in out
    assert "bukan bukti strategi menguntungkan" in out
    assert re.search(r"strategi (KALAH dari|di atas) buy-and-hold", out)


def test_backtest_stress_reports_doubled_costs(project_dir: Path, config_path: Path, capsys):
    write_cache(project_dir)
    assert cli.main(["--config", str(config_path), "backtest"]) == cli.EXIT_OK
    normal = capsys.readouterr().out
    assert cli.main(["--config", str(config_path), "backtest", "--stress"]) == cli.EXIT_OK
    stressed = capsys.readouterr().out
    assert "(STRESS)" in stressed and "(STRESS)" not in normal
    assert "fee 0.3000% + pajak 0.2100% + bursa 0.0888% + slippage 0.3000%" in stressed
    assert "fee 0.1500% + pajak 0.2100% + bursa 0.0444% + slippage 0.1500%" in normal


def test_backtest_period_slicing_and_too_few_bars(project_dir: Path, config_path: Path, capsys):
    write_cache(project_dir)
    code = cli.main(
        ["--config", str(config_path), "backtest", "--start", "2023-11-15", "--end", "2023-11-20"]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "2023-11-15T00:00:00+00:00 .. 2023-11-19T23:00:00+00:00 (120 bar)" in out
    code = cli.main(["--config", str(config_path), "backtest", "--start", "2030-01-01"])
    assert code == cli.EXIT_DATA_ERROR
    assert "minimal 2" in capsys.readouterr().err
    code = cli.main(["--config", str(config_path), "backtest", "--start", "kemarin"])
    assert code == cli.EXIT_CONFIG_ERROR
