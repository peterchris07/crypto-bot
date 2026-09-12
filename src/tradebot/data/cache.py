"""Cache OHLCV di parquet: satu file per venue, pasangan, dan timeframe.

Tata letak di bawah data.cache_dir (relatif terhadap root project):

  <cache_dir>/<venue>/<BASE-QUOTE>_<timeframe>.parquet    bar yang sudah tutup
  <cache_dir>/<venue>/<BASE-QUOTE>_<timeframe>.gaps.json  laporan gap unduhan terakhir

Penulisan atomik: isi ditulis ke file sementara di folder yang sama, di-fsync,
lalu os.replace ke nama akhir. Proses yang mati di tengah penulisan meninggalkan
file lama utuh, bukan parquet setengah jadi. Isi cache selalu divalidasi terhadap
skema OHLCV saat dibaca; cache yang rusak menghasilkan CacheError, bukan bar aneh.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from tradebot.data.errors import CacheError
from tradebot.data.ohlcv import OHLCV_COLUMNS, empty_frame, validate_frame

log = logging.getLogger(__name__)

TMP_SUFFIX = ".tmp"
GAPS_SUFFIX = ".gaps.json"


def atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    """Jalankan writer pada file sementara, fsync, lalu ganti path secara atomik."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        writer(tmp)
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


class OhlcvCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def path_for(self, venue_id: str, symbol: str, timeframe: str) -> Path:
        name = f"{symbol.replace('/', '-')}_{timeframe}.parquet"
        return self.root / venue_id / name

    @staticmethod
    def gaps_path_for(path: Path) -> Path:
        return path.with_name(path.name.removesuffix(".parquet") + GAPS_SUFFIX)

    # ------------------------------------------------------------------ #
    # Bar
    # ------------------------------------------------------------------ #

    def load(self, path: Path) -> pd.DataFrame:
        """Baca cache. File yang belum ada berarti frame kosong; file rusak berarti CacheError."""
        if not path.exists():
            return empty_frame()
        try:
            raw = pd.read_parquet(path, engine="pyarrow")
        except Exception as exc:  # pyarrow melempar beberapa tipe berbeda untuk file rusak
            raise CacheError(f"cache {path} tidak bisa dibaca: {exc}") from exc
        missing = [column for column in OHLCV_COLUMNS if column not in raw.columns]
        if missing:
            raise CacheError(f"cache {path} rusak: kolom OHLCV hilang: {missing}")
        frame = raw[list(OHLCV_COLUMNS)].reset_index(drop=True)
        stamps = frame["timestamp"]
        if not isinstance(stamps.dtype, pd.DatetimeTZDtype):
            raise CacheError(
                f"cache {path} rusak: kolom timestamp tanpa zona waktu ({stamps.dtype}); "
                "cache ini tidak ditulis oleh bot, hapus dan unduh ulang"
            )
        frame["timestamp"] = stamps.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
        for column in OHLCV_COLUMNS[1:]:
            frame[column] = frame[column].astype("float64")
        try:
            validate_frame(frame)
        except ValueError as exc:
            raise CacheError(f"cache {path} rusak: {exc}") from None
        return frame

    def save(self, path: Path, frame: pd.DataFrame) -> None:
        validate_frame(frame)
        atomic_write(path, lambda tmp: frame.to_parquet(tmp, engine="pyarrow", index=False))
        log.info("cache ditulis: %s (%d bar)", path, len(frame))

    # ------------------------------------------------------------------ #
    # Laporan gap
    # ------------------------------------------------------------------ #

    def save_gaps(self, path: Path, report: dict[str, Any]) -> None:
        text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        atomic_write(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))

    def load_gaps(self, path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CacheError(f"laporan gap {path} tidak bisa dibaca: {exc}") from exc
        if not isinstance(data, dict):
            raise CacheError(f"laporan gap {path} rusak: isi teratas bukan objek")
        return data
