"""Tahap 3: cache parquet. Penulisan atomik, pembacaan divalidasi, laporan gap di samping."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from tradebot.data.cache import GAPS_SUFFIX, TMP_SUFFIX, OhlcvCache, atomic_write
from tradebot.data.errors import CacheError
from tradebot.data.ohlcv import OHLCV_COLUMNS, frame_from_rows

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)


def bars(count: int, start_ms: int = T0) -> pd.DataFrame:
    return frame_from_rows(
        [[start_ms + i * HOUR, 1.0, 2.0, 0.5, 1.5 + i, 10.0 + i] for i in range(count)]
    )


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
    assert not path.with_name(path.name + TMP_SUFFIX).exists()


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

    def broken_to_parquet(self, target, *args, **kwargs):
        Path(target).write_bytes(b"setengah jadi")
        raise OSError("disk penuh (disimulasikan)")

    monkeypatch.setattr(pd.DataFrame, "to_parquet", broken_to_parquet)
    with pytest.raises(OSError, match="disk penuh"):
        cache.save(path, bars(4))
    assert path.read_bytes() == before, "file lama harus utuh"
    assert not path.with_name(path.name + TMP_SUFFIX).exists(), "file sementara harus dibersihkan"
    monkeypatch.undo()
    pd.testing.assert_frame_equal(cache.load(path), bars(3))


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


def test_load_rejects_unsorted_or_duplicate_rows(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    path = cache.path_for("tokocrypto", "BTC/USDT", "1h")
    path.parent.mkdir(parents=True)
    pd.concat([bars(2), bars(1)], ignore_index=True).to_parquet(path, index=False)
    with pytest.raises(CacheError, match="rusak"):
        cache.load(path)


def test_gap_report_round_trip(tmp_path: Path):
    cache = OhlcvCache(tmp_path)
    gaps_path = cache.gaps_path_for(cache.path_for("tokocrypto", "BTC/USDT", "1h"))
    assert cache.load_gaps(gaps_path) is None
    payload = {"symbol": "BTC/USDT", "gaps": [{"start": "x", "end": "y", "bars": 2}]}
    cache.save_gaps(gaps_path, payload)
    assert cache.load_gaps(gaps_path) == payload
    gaps_path.write_text("[]", encoding="utf-8")
    with pytest.raises(CacheError, match="bukan objek"):
        cache.load_gaps(gaps_path)


def test_atomic_write_creates_parent_and_cleans_tmp(tmp_path: Path):
    target = tmp_path / "a" / "b" / "c.txt"
    atomic_write(target, lambda tmp: tmp.write_text("isi", encoding="utf-8"))
    assert target.read_text(encoding="utf-8") == "isi"
    assert list(target.parent.iterdir()) == [target]
