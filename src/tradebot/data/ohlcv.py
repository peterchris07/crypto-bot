"""Skema DataFrame OHLCV dan parser timeframe.

Satu bentuk untuk semua bar di dalam bot, dari adapter, cache, backtest,
sampai runner:

  timestamp  datetime64 dengan zona UTC, waktu BUKA bar
  open, high, low, close, volume  float64

terurut naik menurut timestamp dan tanpa duplikat. frame_from_rows membangun
bentuk itu dari baris mentah ccxt, validate_frame memastikannya sebelum bar
dipakai untuk keputusan apa pun. Tidak ada fungsi di sini yang mengisi bar
yang hilang; gap dilaporkan, bukan dikarang.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
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


def floor_to_bar(ms: int, timeframe_ms: int) -> int:
    """Waktu buka bar yang memuat ms. Bar dihitung dari epoch, sama dengan Binance."""
    return ms - (ms % timeframe_ms)


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
    if frame[list(PRICE_COLUMNS)].isna().any().any():
        raise ValueError("ada harga NaN; bar yang tidak lengkap tidak boleh dipakai")
