"""Pengunduh OHLCV historis dari data publik venue live ke cache parquet.

Aturan yang ditegakkan di sini, semuanya turunan SPEC.md bagian Gap data:

- Tidak mengarang bar. Bar yang hilang dilaporkan sebagai Gap, di log dan di
  laporan gap di samping file parquet. Gap yang lebih panjang dari
  data.max_gap_bars dianggap data rusak: proses berhenti dengan DataGapError dan
  cache TIDAK ditulis, supaya backtest tidak diam-diam berjalan di atas lubang.
- Bar yang masih berjalan tidak pernah disimpan. Batas akhir dipotong ke waktu
  buka bar saat ini (menurut jam server), jadi hanya bar yang sudah tutup yang
  masuk cache. Ini syarat backtest tanpa lookahead di tahap 5.
- Inkremental. Cache yang ada hanya ditambah di depan (kalau history_start
  dimundurkan) dan di belakang (bar baru). Setiap kali ada bar baru, bar terakhir
  di cache ikut diambil ulang dan versi barunya menimpa yang lama, supaya bar yang
  sempat tersimpan sebelum final tidak bertahan. Tanpa bar baru yang mungkin,
  tidak ada permintaan ke exchange.
- Bar sebelum bar pertama yang dikembalikan exchange BUKAN gap: pair bisa saja
  baru tercatat setelah tanggal yang diminta. Ini dilaporkan terpisah sebagai
  leading_missing_bars, dengan peringatan, bukan error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from tradebot.data.cache import OhlcvCache
from tradebot.data.errors import DataError, DataGapError
from tradebot.data.ohlcv import (
    Gap,
    empty_frame,
    find_gaps,
    floor_to_bar,
    merge_frames,
    ms_of,
    stamp_of,
    timeframe_to_ms,
)
from tradebot.exchange.base import ExchangeAdapter

log = logging.getLogger(__name__)

# Batas maksimum bar per permintaan klines di Binance dan di host data Tokocrypto
# (www.tokocrypto.site memakai API yang sama). Bukan angka perilaku bot, jadi bukan
# di config; venue yang mengembalikan lebih sedikit tetap ditangani karena loop
# maju berdasarkan bar terakhir yang benar-benar diterima.
PAGE_LIMIT = 1000
# Pengaman loop: 1h sejak Agustus 2017 hanya butuh sekitar 80 halaman.
MAX_PAGES = 10_000


def _iso(ms: int) -> str:
    return stamp_of(ms).isoformat()


@dataclass(frozen=True)
class FetchReport:
    """Hasil satu kali update_cache. frame adalah seluruh isi cache setelah digabung."""

    venue: str
    symbol: str
    timeframe: str
    requested_start: pd.Timestamp
    requested_end: pd.Timestamp  # eksklusif: bar yang buka di sini masih berjalan
    frame: pd.DataFrame
    new_bars: int
    requests: int
    gaps: list[Gap]  # di dalam data, plus kekurangan di ujung akhir
    leading_missing_bars: int  # bar antara requested_start dan bar pertama; bukan gap
    path: Path
    gaps_path: Path

    @property
    def missing_bars(self) -> int:
        return sum(gap.bars for gap in self.gaps)

    @property
    def first(self) -> pd.Timestamp:
        return self.frame["timestamp"].iloc[0]

    @property
    def last(self) -> pd.Timestamp:
        return self.frame["timestamp"].iloc[-1]


def download_range(
    adapter: ExchangeAdapter,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    *,
    page_limit: int = PAGE_LIMIT,
) -> tuple[pd.DataFrame, int]:
    """Unduh semua bar dengan start_ms <= waktu buka < end_ms, halaman demi halaman.

    Mengembalikan frame berskema dan jumlah permintaan. Loop maju berdasarkan bar
    terakhir yang diterima, bukan berdasarkan jumlah yang diminta, jadi venue yang
    memberi lebih sedikit dari page_limit tetap benar. Halaman kosong berarti exchange
    tidak punya bar lagi di rentang itu. Halaman yang tidak maju (bar terakhirnya
    lebih tua dari since) dianggap kesalahan, bukan diulang selamanya.
    """
    timeframe_ms = timeframe_to_ms(timeframe)
    if start_ms >= end_ms:
        return empty_frame(), 0
    since = start_ms
    pages: list[pd.DataFrame] = []
    requests = 0
    while since < end_ms:
        if requests >= MAX_PAGES:
            raise DataError(
                f"{symbol} {timeframe}: sudah {MAX_PAGES} permintaan dan belum sampai "
                f"{_iso(end_ms)}; loop dihentikan"
            )
        page = adapter.fetch_ohlcv(symbol, timeframe, since_ms=since, limit=page_limit)
        requests += 1
        if len(page) == 0:
            log.debug("halaman kosong pada since=%s, unduhan selesai", _iso(since))
            break
        first_ms = ms_of(page["timestamp"].iloc[0])
        last_ms = ms_of(page["timestamp"].iloc[-1])
        log.debug(
            "halaman %d: since=%s -> %d bar %s .. %s",
            requests,
            _iso(since),
            len(page),
            _iso(first_ms),
            _iso(last_ms),
        )
        if last_ms < since:
            raise DataError(
                f"{symbol} {timeframe}: exchange mengembalikan bar {_iso(first_ms)} .. "
                f"{_iso(last_ms)} untuk since={_iso(since)}; unduhan tidak maju, dihentikan"
            )
        pages.append(page)
        if last_ms + timeframe_ms >= end_ms:
            break
        since = last_ms + timeframe_ms
    frame = merge_frames(pages)
    if len(frame):
        stamps = frame["timestamp"]
        inside = (stamps >= stamp_of(start_ms)) & (stamps < stamp_of(end_ms))
        frame = frame[inside].reset_index(drop=True)
    return frame, requests


def gap_report(report: FetchReport, max_gap_bars: int) -> dict[str, Any]:
    """Isi file <nama>.gaps.json: cukup untuk dibaca tanpa membuka parquet-nya."""
    return {
        "venue": report.venue,
        "symbol": report.symbol,
        "timeframe": report.timeframe,
        "requested_start": report.requested_start.isoformat(),
        "requested_end": report.requested_end.isoformat(),
        "first": report.first.isoformat(),
        "last": report.last.isoformat(),
        "bars": int(len(report.frame)),
        "max_gap_bars": int(max_gap_bars),
        "leading_missing_bars": int(report.leading_missing_bars),
        "missing_bars": int(report.missing_bars),
        "gaps": [
            {"start": gap.start.isoformat(), "end": gap.end.isoformat(), "bars": int(gap.bars)}
            for gap in report.gaps
        ],
    }


def update_cache(
    adapter: ExchangeAdapter,
    cache: OhlcvCache,
    *,
    venue_id: str,
    symbol: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    max_gap_bars: int,
    page_limit: int = PAGE_LIMIT,
) -> FetchReport:
    """Lengkapi cache supaya memuat semua bar tutup di [start_ms, end_ms).

    end_ms biasanya jam server saat ini; dipotong ke waktu buka bar berjalan, jadi bar
    itu tidak ikut. Gap lebih panjang dari max_gap_bars menghentikan proses sebelum
    apa pun ditulis.
    """
    timeframe_ms = timeframe_to_ms(timeframe)
    start_ms = floor_to_bar(start_ms, timeframe_ms)
    end_ms = floor_to_bar(end_ms, timeframe_ms)
    if start_ms >= end_ms:
        raise DataError(
            f"rentang kosong: awal {_iso(start_ms)} tidak sebelum akhir {_iso(end_ms)} "
            f"(akhir dipotong ke bar {timeframe} yang sedang berjalan)"
        )
    path = cache.path_for(venue_id, symbol, timeframe)
    existing = cache.load(path)

    ranges: list[tuple[int, int]] = []
    if len(existing) == 0:
        ranges.append((start_ms, end_ms))
    else:
        first_cached = ms_of(existing["timestamp"].iloc[0])
        last_cached = ms_of(existing["timestamp"].iloc[-1])
        if start_ms < first_cached:
            ranges.append((start_ms, first_cached))
        if last_cached + timeframe_ms < end_ms:
            # Ada bar baru yang mungkin sudah tutup. Mulai dari bar terakhir di cache, bukan
            # sesudahnya: versi barunya menimpa yang lama. Kalau tidak ada bar baru yang
            # mungkin, tidak ada permintaan sama sekali.
            ranges.append((last_cached, end_ms))
        log.info(
            "cache %s: %d bar %s .. %s",
            path,
            len(existing),
            _iso(first_cached),
            _iso(last_cached),
        )

    frames = [existing]
    requests = 0
    for range_start, range_end in ranges:
        log.info(
            "unduh %s %s dari %s: %s .. %s",
            symbol,
            timeframe,
            venue_id,
            _iso(range_start),
            _iso(range_end),
        )
        frame, count = download_range(
            adapter, symbol, timeframe, range_start, range_end, page_limit=page_limit
        )
        requests += count
        frames.append(frame)
        log.info("diterima %d bar dalam %d permintaan", len(frame), count)
    if not ranges:
        log.info("cache sudah mencakup rentang yang diminta; tidak ada yang diunduh")

    merged = merge_frames(frames)
    if len(merged) == 0:
        raise DataError(
            f"{venue_id} tidak mengembalikan satu bar pun untuk {symbol} {timeframe} "
            f"{_iso(start_ms)} .. {_iso(end_ms)}; cek pair, timeframe, dan tanggal mulai"
        )
    first_ms = ms_of(merged["timestamp"].iloc[0])
    last_ms = ms_of(merged["timestamp"].iloc[-1])

    gaps = find_gaps(merged, timeframe)
    trailing = (end_ms - (last_ms + timeframe_ms)) // timeframe_ms
    if trailing > 0:
        gaps.append(
            Gap(
                start=stamp_of(last_ms + timeframe_ms),
                end=stamp_of(end_ms - timeframe_ms),
                bars=int(trailing),
            )
        )
    leading = max((first_ms - start_ms) // timeframe_ms, 0)
    if leading:
        log.warning(
            "data %s %s dimulai %s, %d bar setelah awal yang diminta %s. Bukan gap kalau pair "
            "memang baru tercatat setelah itu; kalau bukan, exchange tidak memberi histori itu.",
            symbol,
            timeframe,
            _iso(first_ms),
            leading,
            _iso(start_ms),
        )
    for gap in gaps:
        log.warning("gap %s %s: %s", symbol, timeframe, gap.describe())

    too_long = [gap for gap in gaps if gap.bars > max_gap_bars]
    if too_long:
        detail = "; ".join(gap.describe() for gap in too_long)
        raise DataGapError(
            f"{symbol} {timeframe}: {len(too_long)} gap lebih panjang dari "
            f"data.max_gap_bars={max_gap_bars}: {detail}. Cache tidak ditulis. Kalau ini "
            "downtime exchange yang terkonfirmasi, naikkan data.max_gap_bars di config; "
            "kalau bukan, data sumbernya rusak."
        )

    report = FetchReport(
        venue=venue_id,
        symbol=symbol,
        timeframe=timeframe,
        requested_start=stamp_of(start_ms),
        requested_end=stamp_of(end_ms),
        frame=merged,
        new_bars=len(merged) - len(existing),
        requests=requests,
        gaps=gaps,
        leading_missing_bars=int(leading),
        path=path,
        gaps_path=cache.gaps_path_for(path),
    )
    cache.save(path, merged)
    cache.save_gaps(report.gaps_path, gap_report(report, max_gap_bars))
    log.info(
        "cache %s: %d bar %s .. %s (+%d baru, %d permintaan, %d gap, %d bar hilang)",
        path,
        len(merged),
        _iso(first_ms),
        _iso(last_ms),
        report.new_bars,
        requests,
        len(gaps),
        report.missing_bars,
    )
    return report
