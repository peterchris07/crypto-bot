"""Tahap 3: pengunduh historis di atas TokocryptoAdapter dengan klien palsu.

Tidak menyentuh jaringan. Bar disusun sintetis dengan lubang yang diketahui
posisinya; yang diuji adalah paginasi, batas bar berjalan, kebijakan gap,
dan sifat inkremental cache.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from pathlib import Path

import ccxt
import pandas as pd
import pytest

from tests.fakes import BASE_MS, FakeTokocryptoClient
from tradebot.config import ExchangeConfig, RetryConfig, VenueConfig
from tradebot.data.cache import OhlcvCache
from tradebot.data.errors import DataError, DataGapError
from tradebot.data.fetch import download_range, update_cache
from tradebot.data.ohlcv import Gap, find_gaps, ms_of, stamp_of
from tradebot.exchange import RetryableExchangeError
from tradebot.exchange.tokocrypto_adapter import TokocryptoAdapter

HOUR = 3_600_000
T0 = BASE_MS - (BASE_MS % HOUR)  # sejajar grid 1h, sama dengan jam server palsu
LOCAL_CLOCK_S = BASE_MS / 1000
VENUE = "tokocrypto"
SYMBOL = "BTC/USDT"


def rows(start_ms: int, count: int, *, skip: set[int] = frozenset(), close_base: float = 100.0):
    return [
        [
            start_ms + i * HOUR,
            close_base + i,
            close_base + i + 1,
            close_base + i - 1,
            close_base + i + 0.5,
            10.0,
        ]
        for i in range(count)
        if i not in skip
    ]


@pytest.fixture
def exchange_config() -> ExchangeConfig:
    return ExchangeConfig(
        symbol=SYMBOL,
        timeframe="1h",
        recv_window_ms=5000,
        max_time_drift_ms=1000,
        time_sync_samples=3,
        rate_limit=True,
        retry=RetryConfig(max_attempts=2, base_delay_seconds=0.01, max_delay_seconds=0.02),
        testnet=VenueConfig(id="binance", market_data_url=""),
        live=VenueConfig(id=VENUE, market_data_url="https://data.example/api/v3"),
    )


def build(
    exchange_config: ExchangeConfig, ohlcv_rows: list
) -> tuple[TokocryptoAdapter, FakeTokocryptoClient]:
    holder: dict[str, FakeTokocryptoClient] = {}

    def factory(params):
        client = FakeTokocryptoClient(params)
        client.ohlcv_rows = [list(r) for r in ohlcv_rows]
        holder["client"] = client
        return client

    adapter = TokocryptoAdapter(
        exchange_config,
        exchange_config.live,
        None,
        client_factory=factory,
        sleep=lambda _: None,
        clock=lambda: LOCAL_CLOCK_S,
    )
    adapter.connect()
    return adapter, holder["client"]


def since_calls(client: FakeTokocryptoClient) -> list[int]:
    return [args[2] for name, args, _ in client.calls if name == "fetch_ohlcv"]


def run(adapter, cache, *, start_ms, end_ms, max_gap_bars=6, page_limit=1000):
    return update_cache(
        adapter,
        cache,
        venue_id=VENUE,
        symbol=SYMBOL,
        timeframe="1h",
        start_ms=start_ms,
        end_ms=end_ms,
        max_gap_bars=max_gap_bars,
        page_limit=page_limit,
    )


# --------------------------------------------------------------------------- #
# download_range: paginasi
# --------------------------------------------------------------------------- #


def test_download_range_paginates_by_last_bar_without_duplicates(exchange_config):
    adapter, client = build(exchange_config, rows(T0, 25))
    frame, requests = download_range(adapter, SYMBOL, "1h", T0, T0 + 25 * HOUR, page_limit=10)
    assert len(frame) == 25
    assert requests == 3
    assert since_calls(client) == [T0, T0 + 10 * HOUR, T0 + 20 * HOUR]
    assert not frame["timestamp"].duplicated().any()
    assert find_gaps(frame, "1h") == []


def test_download_range_respects_half_open_bounds(exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 25))
    frame, _ = download_range(adapter, SYMBOL, "1h", T0 + 5 * HOUR, T0 + 8 * HOUR)
    assert [ms_of(t) for t in frame["timestamp"]] == [T0 + 5 * HOUR, T0 + 6 * HOUR, T0 + 7 * HOUR]
    empty, requests = download_range(adapter, SYMBOL, "1h", T0 + 8 * HOUR, T0 + 8 * HOUR)
    assert empty.empty and requests == 0


def test_download_range_stops_on_empty_page_when_exchange_runs_out(exchange_config):
    adapter, client = build(exchange_config, rows(T0, 25))
    frame, requests = download_range(adapter, SYMBOL, "1h", T0, T0 + 100 * HOUR, page_limit=10)
    assert len(frame) == 25
    # 10 + 10 + 5, lalu satu halaman kosong yang menghentikan loop
    assert requests == 4
    assert since_calls(client)[-1] == T0 + 25 * HOUR


def test_download_range_handles_venue_returning_fewer_than_requested(exchange_config):
    adapter, client = build(exchange_config, rows(T0, 12))
    original = client.fetch_ohlcv

    def capped(symbol, timeframe, since=None, limit=None, params=None):
        return original(symbol, timeframe, since, min(limit or 5, 5), params)

    client.fetch_ohlcv = capped
    frame, requests = download_range(adapter, SYMBOL, "1h", T0, T0 + 12 * HOUR, page_limit=1000)
    assert len(frame) == 12
    assert requests == 3


def test_download_range_refuses_to_loop_when_exchange_does_not_advance(exchange_config):
    adapter, client = build(exchange_config, rows(T0, 5))
    client.fetch_ohlcv = lambda *args, **kwargs: [list(r) for r in rows(T0 - 10 * HOUR, 3)]
    with pytest.raises(DataError, match="tidak maju"):
        download_range(adapter, SYMBOL, "1h", T0, T0 + 5 * HOUR)


# --------------------------------------------------------------------------- #
# update_cache: bar berjalan, gap, laporan
# --------------------------------------------------------------------------- #


def test_first_run_writes_parquet_and_gap_report(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 30))
    cache = OhlcvCache(tmp_path)
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 24 * HOUR)
    assert report.path == tmp_path / VENUE / "BTC-USDT_1h.parquet"
    assert report.path.exists() and report.gaps_path.exists()
    assert len(report.frame) == 24 and report.new_bars == 24
    assert report.gaps == [] and report.leading_missing_bars == 0
    assert report.first == stamp_of(T0) and report.last == stamp_of(T0 + 23 * HOUR)
    saved = cache.load(report.path)
    assert len(saved) == 24
    payload = cache.load_gaps(report.path)
    assert payload["bars"] == 24 and payload["gaps"] == [] and payload["max_gap_bars"] == 6
    assert json.loads(report.gaps_path.read_text(encoding="utf-8")) == payload
    assert payload["first"] == stamp_of(T0).isoformat()
    assert payload["requested_end"] == stamp_of(T0 + 24 * HOUR).isoformat()


def test_forming_bar_is_never_stored(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 30))
    cache = OhlcvCache(tmp_path)
    # jam server 10:37 -> bar 10:00 masih berjalan, bar terakhir yang disimpan 09:00
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 10 * HOUR + 37 * 60_000)
    assert report.last == stamp_of(T0 + 9 * HOUR)
    assert report.requested_end == stamp_of(T0 + 10 * HOUR)
    assert report.gaps == []


def test_unaligned_start_is_floored_to_bar(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 30))
    cache = OhlcvCache(tmp_path)
    report = run(adapter, cache, start_ms=T0 + 2 * HOUR + 1, end_ms=T0 + 6 * HOUR)
    assert report.requested_start == stamp_of(T0 + 2 * HOUR)
    assert since_calls(client)[0] == T0 + 2 * HOUR
    assert report.first == stamp_of(T0 + 2 * HOUR)


def test_empty_range_is_an_error(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 30))
    with pytest.raises(DataError, match="rentang kosong"):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0 + 5 * HOUR, end_ms=T0 + 5 * HOUR + 10)


def test_short_gap_is_reported_and_cache_still_written(
    tmp_path: Path, exchange_config, caplog: pytest.LogCaptureFixture
):
    adapter, _ = build(exchange_config, rows(T0, 30, skip={7, 8, 9}))
    cache = OhlcvCache(tmp_path)
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        report = run(adapter, cache, start_ms=T0, end_ms=T0 + 24 * HOUR, max_gap_bars=6)
    assert report.gaps == [Gap(start=stamp_of(T0 + 7 * HOUR), end=stamp_of(T0 + 9 * HOUR), bars=3)]
    assert report.missing_bars == 3 and len(report.frame) == 21
    assert any("3 bar hilang" in record.getMessage() for record in caplog.records)
    payload = cache.load_gaps(report.path)
    assert payload["gaps"] == [
        {
            "start": stamp_of(T0 + 7 * HOUR).isoformat(),
            "end": stamp_of(T0 + 9 * HOUR).isoformat(),
            "bars": 3,
        }
    ]
    assert payload["missing_bars"] == 3


def test_long_gap_aborts_without_writing_anything(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 30, skip={5, 6, 7, 8, 9, 10, 11}))
    cache = OhlcvCache(tmp_path)
    with pytest.raises(DataGapError, match="7 bar hilang") as excinfo:
        run(adapter, cache, start_ms=T0, end_ms=T0 + 24 * HOUR, max_gap_bars=6)
    assert "max_gap_bars=6" in str(excinfo.value)
    assert not (tmp_path / VENUE).exists(), "tidak boleh ada file apa pun setelah gap panjang"


def test_long_gap_leaves_existing_cache_untouched(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 12))
    cache = OhlcvCache(tmp_path)
    first = run(adapter, cache, start_ms=T0, end_ms=T0 + 12 * HOUR)
    before = first.path.read_bytes()
    client.ohlcv_rows = [list(r) for r in rows(T0, 40, skip=set(range(12, 20)))]
    with pytest.raises(DataGapError):
        run(adapter, cache, start_ms=T0, end_ms=T0 + 40 * HOUR)
    assert first.path.read_bytes() == before
    assert cache.load_gaps(first.path)["bars"] == 12


def test_gap_exactly_at_threshold_is_allowed(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 30, skip=set(range(5, 11))))
    report = run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 24 * HOUR, max_gap_bars=6)
    assert report.missing_bars == 6


def test_trailing_shortfall_counts_as_gap(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, rows(T0, 20))
    cache = OhlcvCache(tmp_path)
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 23 * HOUR)
    assert report.gaps == [
        Gap(start=stamp_of(T0 + 20 * HOUR), end=stamp_of(T0 + 22 * HOUR), bars=3)
    ]
    with pytest.raises(DataGapError, match="max_gap_bars=2"):
        run(
            adapter,
            OhlcvCache(tmp_path / "lain"),
            start_ms=T0,
            end_ms=T0 + 23 * HOUR,
            max_gap_bars=2,
        )


def test_leading_shortfall_is_reported_but_not_a_gap(
    tmp_path: Path, exchange_config, caplog: pytest.LogCaptureFixture
):
    adapter, _ = build(exchange_config, rows(T0 + 4 * HOUR, 20))
    with caplog.at_level(logging.WARNING, logger="tradebot"):
        report = run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 24 * HOUR)
    assert report.leading_missing_bars == 4
    assert report.gaps == []
    assert report.first == stamp_of(T0 + 4 * HOUR)
    assert any("4 bar setelah awal" in record.getMessage() for record in caplog.records)


def test_no_bars_at_all_is_an_error(tmp_path: Path, exchange_config):
    adapter, _ = build(exchange_config, [])
    with pytest.raises(DataError, match="satu bar pun"):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 24 * HOUR)


# --------------------------------------------------------------------------- #
# update_cache: inkremental
# --------------------------------------------------------------------------- #


def test_second_run_refetches_only_from_last_cached_bar(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 12))
    cache = OhlcvCache(tmp_path)
    run(adapter, cache, start_ms=T0, end_ms=T0 + 12 * HOUR)
    client.calls.clear()
    client.ohlcv_rows = [list(r) for r in rows(T0, 20)]
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 20 * HOUR)
    assert since_calls(client) == [T0 + 11 * HOUR], "mulai dari bar terakhir, bukan dari awal"
    assert report.new_bars == 8 and len(report.frame) == 20
    assert report.requests == 1


def test_second_run_with_nothing_new_makes_no_request(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 12))
    cache = OhlcvCache(tmp_path)
    first = run(adapter, cache, start_ms=T0, end_ms=T0 + 12 * HOUR)
    before_mtime = first.path.stat().st_mtime_ns
    client.calls.clear()
    report = run(adapter, cache, start_ms=T0 + 2 * HOUR, end_ms=T0 + 12 * HOUR)
    assert since_calls(client) == []
    assert report.new_bars == 0 and report.requests == 0
    assert len(report.frame) == 12, "cache tidak dipotong walau awal yang diminta lebih lambat"
    assert report.path.stat().st_mtime_ns == before_mtime, "tidak ditulis ulang tanpa perubahan"


def test_refetched_last_bar_is_overwritten_by_newer_values(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 12))
    cache = OhlcvCache(tmp_path)
    run(adapter, cache, start_ms=T0, end_ms=T0 + 12 * HOUR)
    client.ohlcv_rows = [list(r) for r in rows(T0, 13, close_base=500.0)]
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 13 * HOUR)
    closes = report.frame["close"].tolist()
    assert closes[10] == pytest.approx(100.0 + 10 + 0.5), "bar lama yang tidak diambil ulang tetap"
    assert closes[11] == pytest.approx(500.0 + 11 + 0.5), "bar terakhir diganti versi baru"
    assert closes[12] == pytest.approx(500.0 + 12 + 0.5)


def test_earlier_start_backfills_in_front_of_cache(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 30))
    cache = OhlcvCache(tmp_path)
    run(adapter, cache, start_ms=T0 + 10 * HOUR, end_ms=T0 + 20 * HOUR)
    client.calls.clear()
    report = run(adapter, cache, start_ms=T0, end_ms=T0 + 20 * HOUR)
    # hanya bagian depan yang diunduh; di belakang tidak ada bar baru yang mungkin
    assert since_calls(client) == [T0]
    assert report.first == stamp_of(T0) and len(report.frame) == 20
    assert report.new_bars == 10 and report.requests == 1
    assert find_gaps(cache.load(report.path), "1h") == []


def test_gap_in_existing_cache_is_rechecked_against_current_threshold(
    tmp_path: Path, exchange_config
):
    adapter, _ = build(exchange_config, rows(T0, 30, skip={3, 4, 5}))
    cache = OhlcvCache(tmp_path)
    run(adapter, cache, start_ms=T0, end_ms=T0 + 24 * HOUR, max_gap_bars=6)
    with pytest.raises(DataGapError):
        run(adapter, cache, start_ms=T0, end_ms=T0 + 24 * HOUR, max_gap_bars=2)


# --------------------------------------------------------------------------- #
# Temuan review: grid mingguan, data rusak dari venue, gangguan jaringan
# --------------------------------------------------------------------------- #

WEEK = 7 * 86_400_000
MONDAY = int(pd.Timestamp("2023-12-11", tz="UTC").value // 1_000_000)  # Senin 00:00 UTC


def test_weekly_forming_bar_is_excluded_on_a_saturday(tmp_path: Path, exchange_config):
    """Bar 1w venue buka Senin; grid epoch jatuh di Kamis. Sabtu, bar Senin masih berjalan."""
    weekly = [[MONDAY + i * WEEK, 1.0, 2.0, 0.5, 1.5 + i, 10.0] for i in range(4)]
    config = dataclasses.replace(exchange_config, timeframe="1w")
    adapter, _ = build(config, weekly)
    saturday = MONDAY + 3 * WEEK + 5 * 86_400_000 + 12 * HOUR  # 2024-01-06T12:00Z
    report = update_cache(
        adapter,
        OhlcvCache(tmp_path),
        venue_id=VENUE,
        symbol=SYMBOL,
        timeframe="1w",
        start_ms=MONDAY,
        end_ms=saturday,
        max_gap_bars=1,
    )
    assert report.requested_end == stamp_of(MONDAY + 3 * WEEK), "dipotong ke Senin, bukan Kamis"
    assert report.last == stamp_of(MONDAY + 2 * WEEK), "bar Senin 2024-01-01 masih berjalan"
    assert len(report.frame) == 3 and report.gaps == []


def test_rows_shifted_off_grid_are_refused_and_nothing_written(tmp_path: Path, exchange_config):
    shifted = [[ts + 1000, *rest] for ts, *rest in rows(T0, 24)]
    adapter, _ = build(exchange_config, shifted)
    with pytest.raises(DataError, match="tidak sejajar grid"):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 24 * HOUR)
    assert not (tmp_path / VENUE).exists()


def test_sub_timeframe_spacing_is_refused(tmp_path: Path, exchange_config):
    data = rows(T0, 6)
    data.insert(3, [T0 + 2 * HOUR + 30 * 60_000, 1.0, 2.0, 0.5, 1.5, 10.0])
    adapter, _ = build(exchange_config, data)
    with pytest.raises(DataError, match="tidak sejajar grid"):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 6 * HOUR)
    assert not (tmp_path / VENUE).exists()


def test_missing_price_from_venue_is_a_data_error(tmp_path: Path, exchange_config):
    data = rows(T0, 6)
    data[2][4] = None  # close kosong, seperti safe_number ccxt untuk nilai yang hilang
    adapter, _ = build(exchange_config, data)
    with pytest.raises(DataError, match="NaN"):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 6 * HOUR)
    assert not (tmp_path / VENUE).exists()


def test_transient_network_failure_mid_download_is_retried(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 25))
    client.fail_next("fetch_ohlcv", ccxt.NetworkError("putus (disimulasikan)"))
    frame, requests = download_range(adapter, SYMBOL, "1h", T0, T0 + 25 * HOUR, page_limit=10)
    assert len(frame) == 25
    # percobaan yang gagal tidak dihitung sebagai halaman; adapter mengulanginya sendiri
    assert requests == 3
    assert since_calls(client) == [T0, T0, T0 + 10 * HOUR, T0 + 20 * HOUR]


def test_persistent_network_failure_propagates_and_writes_nothing(tmp_path: Path, exchange_config):
    adapter, client = build(exchange_config, rows(T0, 25))
    client.fail_next("fetch_ohlcv", *[ccxt.NetworkError("putus") for _ in range(5)])
    with pytest.raises(RetryableExchangeError):
        run(adapter, OhlcvCache(tmp_path), start_ms=T0, end_ms=T0 + 24 * HOUR, page_limit=10)
    assert not (tmp_path / VENUE).exists()
