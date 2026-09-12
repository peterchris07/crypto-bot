"""Tahap 3, test yang diminta SPEC: unduh 1 tahun BTC/USDT 1h dari data publik Tokocrypto.

Ditandai network. Menulis ke folder sementara, bukan ke data/ project. Yang
dipastikan, dan tidak bisa dipenuhi secara tautologis oleh kode yang diuji:

- setiap gap yang dilaporkan benar-benar tidak ada di sisi exchange: bar di
  rentang gap diminta ulang langsung ke exchange dan harus kosong, jadi bar yang
  dijatuhkan paginasi sendiri akan ketahuan;
- jumlah permintaan tidak lebih sedikit dari jumlah halaman yang dibutuhkan;
- histori mulai dari tanggal yang diminta (BTC/USDT ada sejak 2017);
- laporan gap yang tertanam di parquet sama persis dengan gap yang dihitung ulang
  dari isi parquet, ditambah paling banyak satu gap ujung akhir;
- menjalankan ulang tidak mengunduh apa pun yang sudah ada.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

import pandas as pd
import pytest

from tradebot.config import load_settings
from tradebot.data.cache import OhlcvCache
from tradebot.data.fetch import PAGE_LIMIT, update_cache
from tradebot.data.ohlcv import find_gaps, floor_to_bar, ms_of, timeframe_to_ms
from tradebot.exchange.factory import build_public_adapter

pytestmark = pytest.mark.network

ROOT = Path(__file__).resolve().parent.parent
DAY_MS = 86_400_000
log = logging.getLogger("tradebot.tests.fetch")


def test_one_year_download_has_only_recorded_gaps(tmp_path: Path):
    settings = load_settings(
        ROOT / "config" / "default.yaml", environ={**os.environ, "TRADING_MODE": "paper"}
    )
    symbol, timeframe = settings.exchange.symbol, settings.exchange.timeframe
    timeframe_ms = timeframe_to_ms(timeframe)
    step = pd.Timedelta(timeframe_ms, unit="ms")
    adapter = build_public_adapter(settings)
    adapter.connect()
    now_ms = adapter.fetch_server_time_ms()
    start_ms = now_ms - 366 * DAY_MS
    cache = OhlcvCache(tmp_path)

    report = update_cache(
        adapter,
        cache,
        venue_id=settings.exchange.live.id,
        symbol=symbol,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=now_ms,
        max_gap_bars=settings.data.max_gap_bars,
    )
    expected_bars = floor_to_bar(now_ms, timeframe_ms) - floor_to_bar(start_ms, timeframe_ms)
    expected_bars //= timeframe_ms
    log.info(
        "%s %s: %d bar, %d permintaan, %d gap (%d bar hilang), %d bar di depan",
        symbol,
        timeframe,
        len(report.frame),
        report.requests,
        len(report.gaps),
        report.missing_bars,
        report.leading_missing_bars,
    )
    for gap in report.gaps:
        log.info("gap tercatat: %s", gap.describe())

    assert report.leading_missing_bars == 0, "histori BTC/USDT ada sejak 2017; awal harus penuh"
    assert len(report.frame) + report.missing_bars == expected_bars
    assert report.requests >= math.ceil(expected_bars / PAGE_LIMIT), "halaman terlalu sedikit"

    # Bukti independen: setiap gap harus kosong juga kalau ditanyakan langsung ke exchange.
    trailing_end = report.requested_end - step
    for gap in report.gaps:
        if gap.end == trailing_end:
            continue  # ujung akhir: exchange belum punya bar itu saat unduhan berlangsung
        probe = adapter.fetch_ohlcv(
            symbol, timeframe, since_ms=ms_of(gap.start), limit=gap.bars + 2
        )
        inside = probe[(probe["timestamp"] >= gap.start) & (probe["timestamp"] <= gap.end)]
        assert inside.empty, f"exchange punya bar di dalam gap yang dilaporkan: {gap.describe()}"

    saved = cache.load(report.path)
    assert len(saved) == len(report.frame)
    recorded = cache.load_gaps(report.path)
    assert recorded is not None and recorded["bars"] == len(saved)
    recorded_gaps = [(g["start"], g["end"], g["bars"]) for g in recorded["gaps"]]
    internal = [
        (g.start.isoformat(), g.end.isoformat(), g.bars) for g in find_gaps(saved, timeframe)
    ]
    assert recorded_gaps[: len(internal)] == internal
    extra = recorded_gaps[len(internal) :]
    assert len(extra) <= 1, f"lebih dari satu gap di luar isi parquet: {extra}"
    if extra:
        assert extra[0][1] == trailing_end.isoformat(), "gap ekstra hanya boleh di ujung akhir"
    assert recorded["missing_bars"] == sum(g[2] for g in recorded_gaps) == report.missing_bars

    again = update_cache(
        adapter,
        cache,
        venue_id=settings.exchange.live.id,
        symbol=symbol,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=adapter.fetch_server_time_ms(),
        max_gap_bars=settings.data.max_gap_bars,
    )
    assert again.requests <= 1, "jalan ulang hanya mengambil ulang bar terakhir dan yang baru"
    assert again.new_bars <= 1
