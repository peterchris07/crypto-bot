"""Skema DataFrame OHLCV dan parser timeframe.

Satu bentuk untuk semua bar di dalam bot, dari adapter, cache, backtest,
sampai runner:

  timestamp  datetime64 dengan zona UTC, waktu BUKA bar
  open, high, low, close, volume  float64

terurut naik menurut timestamp dan tanpa duplikat. frame_from_rows membangun
bentuk itu dari baris mentah ccxt, validate_frame memastikannya sebelum bar
dipakai untuk keputusan apa pun. Tidak ada fungsi di sini yang mengisi bar
yang hilang; gap dilaporkan, bukan dikarang (lihat find_gaps).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pandas as pd

OHLCV_COLUMNS: tuple[str, ...] = ("timestamp", "open", "high", "low", "close", "volume")
PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")

_UNIT_MS: dict[str, int] = {
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 7 * 86_400_000,
}
# Ketat: angka bulat positif diikuti satu huruf satuan kecil. "1H", "1.5h", "0h",
# dan "1s" ditolak supaya salah ketik di config gagal keras, bukan diam-diam.
_TIMEFRAME = re.compile(r"^([1-9]\d*)([mhdw])$")


def timeframe_to_ms(timeframe: str) -> int:
    """Panjang satu bar dalam milidetik. ValueError kalau formatnya tidak dikenal."""
    match = _TIMEFRAME.match(str(timeframe).strip())
    if match is None:
        raise ValueError(
            f"timeframe {timeframe!r} tidak dikenal; format: angka bulat positif diikuti "
            f"satuan m, h, d, atau w (contoh 15m, 1h, 4h, 1d)"
        )
    return int(match.group(1)) * _UNIT_MS[match.group(2)]


WEEK_MS = 7 * 86_400_000
# Epoch (1970-01-01) jatuh pada Kamis. Bar mingguan Binance dan Tokocrypto buka Senin 00:00
# UTC (bar 1w BTC/USDT pertama: 2017-08-14), jadi grid kelipatan 1w dijangkar ke Senin pertama
# setelah epoch, 1970-01-05. Satuan lain (m, h, d) memang sejajar epoch.
WEEK_ANCHOR_MS = 4 * 86_400_000


def grid_anchor_ms(timeframe_ms: int) -> int:
    """Titik nol grid bar untuk timeframe ini: Senin 1970-01-05 untuk kelipatan minggu."""
    return WEEK_ANCHOR_MS if timeframe_ms % WEEK_MS == 0 else 0


def floor_to_bar(ms: int, timeframe_ms: int) -> int:
    """Waktu buka bar yang memuat ms, pada grid venue (epoch, atau Senin untuk mingguan)."""
    anchor = grid_anchor_ms(timeframe_ms)
    return ms - ((ms - anchor) % timeframe_ms)


def ms_of(stamp: pd.Timestamp) -> int:
    """Milidetik epoch dari Timestamp pandas, apa pun unit internalnya."""
    return int(stamp.value // 1_000_000)


def stamp_of(ms: int) -> pd.Timestamp:
    return pd.Timestamp(int(ms), unit="ms", tz="UTC")


def parse_utc_ms(text: str) -> int:
    """Tanggal atau waktu ISO 8601 ke milidetik epoch. Tanpa zona waktu dianggap UTC."""
    raw = str(text).strip()
    if not raw:
        raise ValueError("tanggal kosong")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"tanggal {text!r} bukan ISO 8601 (contoh 2025-09-01 atau 2025-09-01T06:00:00Z)"
        ) from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def empty_frame() -> pd.DataFrame:
    frame = pd.DataFrame({column: pd.Series(dtype="float64") for column in OHLCV_COLUMNS})
    frame["timestamp"] = pd.Series(dtype="datetime64[ns, UTC]")
    return frame


def frame_from_rows(rows: Sequence[Sequence[Any]]) -> pd.DataFrame:
    """Bangun DataFrame berskema dari baris ccxt [ms, open, high, low, close, volume, ...].

    Kolom tambahan diabaikan. Baris diurutkan naik. Kalau satu timestamp muncul dua
    kali (ccxt kadang mengembalikan bar yang tumpang tindih di batas halaman), baris
    terakhir yang dipakai karena itu yang paling baru dilaporkan exchange.
    """
    if len(rows) == 0:
        return empty_frame()
    width = len(OHLCV_COLUMNS)
    trimmed = []
    for index, row in enumerate(rows):
        if len(row) < width:
            raise ValueError(f"baris OHLCV ke-{index} hanya punya {len(row)} kolom, butuh {width}")
        trimmed.append(list(row[:width]))
    frame = pd.DataFrame(trimmed, columns=list(OHLCV_COLUMNS))
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].astype("int64"), unit="ms", utc=True)
    for column in OHLCV_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column]).astype("float64")
    frame = frame.sort_values("timestamp", kind="stable")
    frame = frame.drop_duplicates(subset="timestamp", keep="last")
    return frame.reset_index(drop=True)


def validate_frame(frame: pd.DataFrame) -> None:
    """Pastikan frame memenuhi skema. ValueError menyebut apa yang melanggar."""
    missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"kolom OHLCV hilang: {missing}")
    dtype = frame["timestamp"].dtype
    if not isinstance(dtype, pd.DatetimeTZDtype) or str(dtype.tz) != "UTC":
        raise ValueError(f"kolom timestamp harus datetime64 dengan zona UTC, dapat {dtype}")
    if len(frame) == 0:
        return
    if not frame["timestamp"].is_monotonic_increasing:
        raise ValueError("timestamp tidak urut naik")
    if frame["timestamp"].duplicated().any():
        raise ValueError("ada timestamp duplikat")
    for column in OHLCV_COLUMNS[1:]:
        if not pd.api.types.is_float_dtype(frame[column]):
            raise ValueError(f"kolom {column} harus float64, dapat {frame[column].dtype}")
    nan_columns = [column for column in OHLCV_COLUMNS[1:] if frame[column].isna().any()]
    if nan_columns:
        raise ValueError(
            f"ada nilai NaN di kolom {nan_columns}; bar yang tidak lengkap tidak boleh dipakai"
        )


def merge_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    """Gabungkan beberapa frame berskema: urut naik, satu baris per timestamp.

    Kalau timestamp yang sama ada di lebih dari satu frame, baris dari frame yang
    datang BELAKANGAN yang dipakai. Pemanggil mengandalkan ini: cache lama dulu,
    unduhan baru kemudian, supaya bar yang diambil ulang menimpa versi lama.
    """
    parts = [frame for frame in frames if len(frame)]
    if not parts:
        return empty_frame()
    merged = pd.concat(parts, ignore_index=True)
    merged = merged.sort_values("timestamp", kind="stable")
    merged = merged.drop_duplicates(subset="timestamp", keep="last").reset_index(drop=True)
    validate_frame(merged)
    return merged


@dataclass(frozen=True)
class Gap:
    """Rentang bar yang seharusnya ada tapi tidak ada. start dan end adalah waktu buka
    bar pertama dan terakhir yang hilang (inklusif), bars jumlah bar yang hilang."""

    start: pd.Timestamp
    end: pd.Timestamp
    bars: int

    def describe(self) -> str:
        return f"{self.bars} bar hilang: {self.start.isoformat()} .. {self.end.isoformat()}"


def find_gaps(frame: pd.DataFrame, timeframe: str) -> list[Gap]:
    """Cari bar yang hilang di antara bar pertama dan terakhir frame.

    Semua bar harus sejajar grid venue untuk timeframe itu (lihat floor_to_bar); bar yang
    bergeser dari grid, atau berjarak bukan kelipatan timeframe, adalah data rusak dan
    ditolak dengan ValueError, bukan disamarkan sebagai gap. Fungsi ini bekerja
    posisional, jadi label index frame tidak berpengaruh.

    Tidak menebak apa pun di luar rentang frame: bar sebelum bar pertama atau
    setelah bar terakhir bukan urusan fungsi ini. Pemanggil yang tahu rentang
    yang diminta memeriksanya sendiri (lihat data.fetch).
    """
    validate_frame(frame)
    if len(frame) == 0:
        return []
    timeframe_ms = timeframe_to_ms(timeframe)
    step = pd.Timedelta(timeframe_ms, unit="ms")
    stamps = frame["timestamp"].reset_index(drop=True)
    anchor = stamp_of(grid_anchor_ms(timeframe_ms))
    off_grid = ((stamps - anchor) % step) != pd.Timedelta(0)
    if off_grid.any():
        first_bad = stamps[off_grid].iloc[0]
        raise ValueError(
            f"bar {first_bad.isoformat()} tidak sejajar grid {timeframe}; data rusak, bukan gap"
        )
    if len(stamps) < 2:
        return []
    # Setelah cek grid, setiap selisih adalah kelipatan positif dari step (duplikat sudah
    # ditolak validate_frame), jadi selisih > step berarti persis (selisih/step - 1) bar hilang.
    diffs = stamps.diff()
    gaps: list[Gap] = []
    for position in diffs[diffs > step].index:
        previous = stamps.iloc[position - 1]
        current = stamps.iloc[position]
        missing = int((current - previous) / step) - 1
        gaps.append(Gap(start=previous + step, end=current - step, bars=missing))
    return gaps
