"""Tahap 2: skema OHLCV dan parser timeframe."""

from __future__ import annotations

import pandas as pd
import pytest

from tradebot.data.ohlcv import (
    OHLCV_COLUMNS,
    empty_frame,
    frame_from_rows,
    timeframe_to_ms,
    validate_frame,
)


@pytest.mark.parametrize(
    ("timeframe", "expected_ms"),
    [
        ("1m", 60_000),
        ("15m", 900_000),
        ("1h", 3_600_000),
        ("4h", 14_400_000),
        ("1d", 86_400_000),
        ("1w", 604_800_000),
        (" 1h ", 3_600_000),
    ],
)
def test_timeframe_to_ms(timeframe, expected_ms):
    assert timeframe_to_ms(timeframe) == expected_ms


@pytest.mark.parametrize("timeframe", ["1x", "h", "0h", "1s", "", "1H", "1.5h"])
def test_timeframe_to_ms_rejects_unknown(timeframe):
    with pytest.raises(ValueError):
        timeframe_to_ms(timeframe)


def test_frame_from_rows_sorts_and_types():
    rows = [
        [1_700_003_600_000, 2, 3, 1, 2.5, 20],
        [1_700_000_000_000, 1, 2, 0.5, 1.5, 10],
    ]
    frame = frame_from_rows(rows)
    assert list(frame.columns) == list(OHLCV_COLUMNS)
    assert frame["timestamp"].is_monotonic_increasing
    assert str(frame["timestamp"].dtype) == "datetime64[ns, UTC]"
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2023-11-14T22:13:20Z")
    assert frame["close"].dtype == "float64"
    assert frame["close"].tolist() == [1.5, 2.5]
    validate_frame(frame)


def test_frame_from_rows_ignores_extra_columns():
    frame = frame_from_rows([[1_700_000_000_000, 1, 2, 0.5, 1.5, 10, "extra"]])
    assert list(frame.columns) == list(OHLCV_COLUMNS)


def test_empty_rows_give_empty_schema_frame():
    frame = frame_from_rows([])
    assert list(frame.columns) == list(OHLCV_COLUMNS)
    assert len(frame) == 0
    validate_frame(frame)
    validate_frame(empty_frame())


def test_validate_frame_rejects_missing_column():
    with pytest.raises(ValueError, match="kolom OHLCV hilang"):
        validate_frame(pd.DataFrame({"timestamp": [], "close": []}))


def test_validate_frame_rejects_naive_timestamps():
    frame = frame_from_rows([[1_700_000_000_000, 1, 2, 0.5, 1.5, 10]])
    frame["timestamp"] = frame["timestamp"].dt.tz_localize(None)
    with pytest.raises(ValueError, match="UTC"):
        validate_frame(frame)


def test_validate_frame_rejects_unsorted():
    frame = (
        frame_from_rows(
            [[1_700_000_000_000, 1, 2, 0.5, 1.5, 10], [1_700_003_600_000, 1, 2, 0.5, 1.5, 10]]
        )
        .iloc[::-1]
        .reset_index(drop=True)
    )
    with pytest.raises(ValueError, match="urut"):
        validate_frame(frame)
