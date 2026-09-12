"""Cache OHLCV di parquet: satu file per venue, pasangan, dan timeframe.

Tata letak di bawah data.cache_dir (relatif terhadap root project):

  <cache_dir>/<venue>/<BASE-QUOTE>_<timeframe>.parquet    bar yang sudah tutup
  <cache_dir>/<venue>/<BASE-QUOTE>_<timeframe>.gaps.json  salinan laporan gap untuk dibaca orang

Laporan gap yang menjadi acuan tertanam di metadata skema parquet (kunci
tradebot.gaps), jadi bar dan laporannya selalu berasal dari satu penulisan
yang sama; load_gaps membacanya dari sana. File .gaps.json hanya salinan.

Penulisan atomik: isi ditulis ke file sementara bernama unik di folder yang
sama, di-fsync, lalu os.replace ke nama akhir, lalu folder di-fsync. Proses yang
mati di tengah penulisan meninggalkan file lama utuh, bukan parquet setengah
jadi; dua proses yang menulis bersamaan masing-masing menghasilkan file utuh dan
yang terakhir menang. Isi cache selalu divalidasi terhadap skema OHLCV saat
dibaca; cache yang rusak menghasilkan CacheError, bukan bar aneh.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from tradebot.data.errors import CacheError
from tradebot.data.ohlcv import OHLCV_COLUMNS, empty_frame, validate_frame

log = logging.getLogger(__name__)

TMP_SUFFIX = ".tmp"
GAPS_SUFFIX = ".gaps.json"
GAPS_METADATA_KEY = b"tradebot.gaps"


def _fsync_dir(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path: Path, writer: Callable[[Path], None]) -> None:
    """Jalankan writer pada file sementara unik, fsync, lalu ganti path secara atomik."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}{TMP_SUFFIX}")
    # O_EXCL: nama ini milik proses ini saja; permission mengikuti umask seperti file biasa.
    os.close(os.open(tmp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o666))
    try:
        writer(tmp)
        with tmp.open("rb") as handle:
            os.fsync(handle.fileno())
        os.replace(tmp, path)
        _fsync_dir(path.parent)
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
        try:
            frame["timestamp"] = stamps.dt.tz_convert("UTC").astype("datetime64[ns, UTC]")
            for column in OHLCV_COLUMNS[1:]:
                frame[column] = frame[column].astype("float64")
            validate_frame(frame)
        except (ValueError, TypeError) as exc:
            raise CacheError(f"cache {path} rusak: {exc}") from None
        return frame

    def save(self, path: Path, frame: pd.DataFrame, report: dict[str, Any] | None = None) -> None:
        """Tulis bar ke parquet; report (laporan gap) ikut tertanam di metadata skema."""
        validate_frame(frame)
        table = pa.Table.from_pandas(frame, preserve_index=False)
        if report is not None:
            metadata = dict(table.schema.metadata or {})
            metadata[GAPS_METADATA_KEY] = json.dumps(report, ensure_ascii=False).encode("utf-8")
            table = table.replace_schema_metadata(metadata)
        atomic_write(path, lambda tmp: pq.write_table(table, tmp))
        log.info("cache ditulis: %s (%d bar)", path, len(frame))

    # ------------------------------------------------------------------ #
    # Laporan gap
    # ------------------------------------------------------------------ #

    def load_gaps(self, path: Path) -> dict[str, Any] | None:
        """Laporan gap dari metadata parquet di path. None kalau file atau laporannya tidak ada."""
        if not path.exists():
            return None
        try:
            metadata = pq.read_schema(path).metadata or {}
        except Exception as exc:
            raise CacheError(f"cache {path} tidak bisa dibaca: {exc}") from exc
        raw = metadata.get(GAPS_METADATA_KEY)
        if raw is None:
            return None
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            raise CacheError(f"laporan gap di {path} rusak: {exc}") from exc
        if not isinstance(data, dict):
            raise CacheError(f"laporan gap di {path} rusak: isi teratas bukan objek")
        return data

    def save_gaps_copy(self, gaps_path: Path, report: dict[str, Any]) -> None:
        """Salinan laporan gap sebagai JSON untuk dibaca orang. Acuannya tetap metadata parquet."""
        text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        atomic_write(gaps_path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
