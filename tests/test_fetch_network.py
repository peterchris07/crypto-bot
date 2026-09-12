"""Tahap 3, test yang diminta SPEC: unduh 1 tahun BTC/USDT 1h dari data publik Tokocrypto.

Ditandai network. Menulis ke folder sementara, bukan ke data/ project. Yang
dipastikan: jumlah bar cocok dengan rentang dikurangi gap yang dilaporkan, setiap
gap tidak lebih panjang dari data.max_gap_bars, laporan gap cocok dengan isi
parquet, dan menjalankan ulang tidak mengunduh apa pun yang sudah ada.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from tradebot.config import load_settings
from tradebot.data.cache import OhlcvCache
from tradebot.data.fetch import update_cache
from tradebot.data.ohlcv import find_gaps, floor_to_bar, timeframe_to_ms
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
    assert all(gap.bars <= settings.data.max_gap_bars for gap in report.gaps)

    saved = cache.load(report.path)
    assert len(saved) == len(report.frame)
    recorded = cache.load_gaps(report.gaps_path)
    assert recorded is not None and recorded["bars"] == len(saved)
    internal = find_gaps(saved, timeframe)
    assert [(g.start.isoformat(), g.end.isoformat(), g.bars) for g in internal] == [
        (g["start"], g["end"], g["bars"]) for g in recorded["gaps"]
    ][: len(internal)]

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
