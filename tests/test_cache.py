"""Tahap 3: cache parquet. Penulisan atomik, pembacaan divalidasi, laporan gap tertanam."""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pandas as pd
import pytest

from tradebot.data import cache as cache_module
from tradebot.data.cache import GAPS_SUFFIX, TMP_SUFFIX, OhlcvCache, atomic_write
from tradebot.data.errors import CacheError
from tradebot.data.ohlcv import OHLCV_COLUMNS, frame_from_rows

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def bars(count: int, start_ms: int = T0) -> pd.DataFrame:
    return frame_from_rows(
        [[start_ms + i * HOUR, 1.0, 2.0, 0.5, 1.5 + i, 10.0 + i] for i in range(count)]
    )


def tmp_files(path: Path) -> list[Path]:
    return sorted(path.parent.glob(f"*{TMP_SUFFIX}")) if path.parent.exists() else []


def test_path_scheme_is_per_venue_symbol_timeframe(tmp_path: Path):
    cache = OhlcvCache(tmp_path / "data")
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    assert path == tmp_path / "data" / "tokocrypto" / "BTC-USDT_1h.parquet"
    assert cache.gaps_path_for(path) == tmp_path / "data" / "tokocrypto" / (
        "BTC-USDT_1h" + GAPS_SUFFIX
    )


def test_save_and_load_round_trip_preserves_schema(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    frame = bars(5)
    cache.save(path, frame)
    loaded = cache.load(path)
    assert list(loaded.columns) == list(OHLCV_COLUMNS)
    assert str(loaded["timestamp"].dtype) == "datetime64[ns, UTC]"
    pd.testing.assert_frame_equal(loaded, frame)
    assert tmp_files(path) == []
    assert cache.load_gaps(path) is None, "tanpa laporan, metadata kosong"


def test_load_missing_file_is_empty_schema_frame(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    frame = cache.load(tmp_path / "tidak-ada.parquet")
    assert frame.empty
    assert list(frame.columns) == list(OHLCV_COLUMNS)


def test_save_is_atomic_when_writer_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    cache.save(path, bars(3))
    before = path.read_bytes()

    def broken_write_table(table, target, *args, **kwargs):
        Path(target).write_bytes(b"setengah jadi")
        raise OSError("disk penuh (disimulasikan)")

    monkeypatch.setattr(cache_module.pq, "write_table", broken_write_table)
    with pytest.raises(OSError, match="disk penuh"):
        cache.save(path, bars(4))
    assert path.read_bytes() == before, "file lama harus utuh"
    assert tmp_files(path) == [], "file sementara harus dibersihkan"
    monkeypatch.undo()
    pd.testing.assert_frame_equal(cache.load(path), bars(3))


def test_atomic_write_orders_write_fsync_replace_then_dir_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """fsync sebelum replace adalah bagian kontrak: tanpa itu crash listrik bisa meninggalkan
    file akhir yang namanya sudah benar tapi isinya belum sampai ke disk."""
    target = tmp_path / "x.bin"
    events: list[tuple[str, str]] = []
    real_fsync, real_replace = os.fsync, os.replace
    inode_of: dict[int, str] = {}

    def spy_fsync(fd):
        ino = os.fstat(fd).st_ino
        kind = (
            "dir" if os.path.isdir(f"/proc/self/fd/{fd}") or inode_of.get(ino) == "dir" else "file"
        )
        events.append(("fsync", kind))
        return real_fsync(fd)

    def spy_replace(src, dst, *args, **kwargs):
        events.append(("replace", Path(src).name.endswith(TMP_SUFFIX) and Path(dst) == target))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "fsync", spy_fsync)
    monkeypatch.setattr(os, "replace", spy_replace)
    atomic_write(
        target,
        lambda tmp: (
            events.append(("write", tmp.name.endswith(TMP_SUFFIX))),
            tmp.write_bytes(b"isi"),
        ),
    )
    assert [e[0] for e in events] == ["write", "fsync", "replace", "fsync"]
    assert events[0][1] is True and events[2][1] is True
    assert target.read_bytes() == b"isi"


def test_atomic_write_fails_loudly_when_fsync_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    target = tmp_path / "x.bin"
    target.write_bytes(b"lama")
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync gagal")))
    with pytest.raises(OSError, match="fsync gagal"):
        atomic_write(target, lambda tmp: tmp.write_bytes(b"baru"))
    assert target.read_bytes() == b"lama"
    assert tmp_files(target) == []


def test_concurrent_writers_each_produce_a_whole_file(tmp_path: Path):
    """Dua penulis bersamaan tidak boleh saling menimpa file sementara: nama sementara unik."""
    target = tmp_path / "x.bin"
    ready = threading.Barrier(2)
    payloads = {"a": b"A" * 200_000, "b": b"B" * 200_000}
    errors: list[BaseException] = []

    def worker(name: str):
        def writer(tmp: Path):
            ready.wait(timeout=5)  # keduanya punya file sementara sebelum ada yang replace
            tmp.write_bytes(payloads[name])

        try:
            atomic_write(target, writer)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert target.read_bytes() in payloads.values(), "file akhir harus utuh milik salah satu"
    assert tmp_files(target) == []


def test_save_refuses_frame_that_violates_schema(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    bad = bars(3).iloc[::-1].reset_index(drop=True)
    with pytest.raises(ValueError, match="urut"):
        cache.save(path, bad)
    assert not path.exists()


def test_load_rejects_garbage_file_with_path_in_message(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"bukan parquet")
    with pytest.raises(CacheError, match="BTC-USDT_1h.parquet"):
        cache.load(path)
    with pytest.raises(CacheError, match="BTC-USDT_1h.parquet"):
        cache.load_gaps(path)


def test_load_rejects_parquet_without_ohlcv_columns(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    pd.DataFrame({"timestamp": [1, 2], "close": [1.0, 2.0]}).to_parquet(path, index=False)
    with pytest.raises(CacheError, match="kolom OHLCV hilang"):
        cache.load(path)


def test_load_rejects_naive_timestamps(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    frame = bars(3)
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
    frame.to_parquet(path, index=False)
    with pytest.raises(CacheError, match="zona waktu"):
        cache.load(path)


def test_load_rejects_unsorted_duplicate_or_unconvertible_rows(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    pd.concat([bars(2), bars(1)], ignore_index=True).to_parquet(path, index=False)
    with pytest.raises(CacheError, match="rusak"):
        cache.load(path)
    frame = bars(2)
    frame["close"] = ["bukan", "angka"]
    frame.to_parquet(path, index=False)
    with pytest.raises(CacheError, match="rusak"):
        cache.load(path)


def test_gap_report_is_embedded_in_parquet_and_copied_to_json(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    gaps_path = cache.gaps_path_for(path)
    payload = {"symbol": "BTC/USDT", "bars": 5, "gaps": [{"start": "x", "end": "y", "bars": 2}]}
    cache.save(path, bars(5), payload)
    assert cache.load_gaps(path) == payload
    pd.testing.assert_frame_equal(cache.load(path), bars(5))
    assert not gaps_path.exists(), "salinan JSON ditulis terpisah oleh pemanggil"
    cache.save_gaps_copy(gaps_path, payload)
    assert gaps_path.read_text(encoding="utf-8").startswith("{")
    # laporan lama tidak bertahan kalau parquet ditulis ulang tanpa laporan
    cache.save(path, bars(6))
    assert cache.load_gaps(path) is None


def test_atomic_write_creates_parent_and_cleans_tmp(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_write(target, lambda tmp: tmp.write_text("isi", encoding="utf-8"))
    assert target.read_text(encoding="utf-8") == "isi"
    assert list(target.parent.iterdir()) == [target]
