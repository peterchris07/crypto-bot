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


# --------------------------------------------------------------------------- #
# Tahap 3: waktu bar, penggabungan, dan deteksi gap
# --------------------------------------------------------------------------- #

from tradebot.data.ohlcv import (  # noqa: E402
    Gap,
    find_gaps,
    floor_to_bar,
    merge_frames,
    ms_of,
    parse_utc_ms,
    stamp_of,
)

HOUR = 3_600_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % HOUR)  # sejajar grid 1h


def bars(start_ms: int, count: int, *, skip: set[int] = frozenset(), close_offset: float = 0.0):
    rows = []
    for i in range(count):
        if i in skip:
            continue
        rows.append([start_ms + i * HOUR, 1.0, 2.0, 0.5, 1.5 + i + close_offset, 10.0])
    return frame_from_rows(rows)


def test_floor_to_bar_aligns_to_epoch_grid():
    assert floor_to_bar(T0 + 59 * 60_000, HOUR) == T0
    assert floor_to_bar(T0, HOUR) == T0
    assert floor_to_bar(T0 + HOUR, HOUR) == T0 + HOUR


def test_ms_of_and_stamp_of_round_trip():
    assert ms_of(stamp_of(T0)) == T0
    assert stamp_of(T0).tzinfo is not None
    # unit internal yang berbeda tidak mengubah hasil
    assert ms_of(pd.Timestamp(T0, unit="ms", tz="UTC").as_unit("us")) == T0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2025-09-01", 1_756_684_800_000),
        ("2025-09-01T00:00:00Z", 1_756_684_800_000),
        ("2025-09-01T07:00:00+07:00", 1_756_684_800_000),
        (" 2025-09-01 ", 1_756_684_800_000),
    ],
)
def test_parse_utc_ms(text, expected):
    assert parse_utc_ms(text) == expected


@pytest.mark.parametrize("text", ["", "kemarin", "2025-13-01", "01/09/2025"])
def test_parse_utc_ms_rejects_non_iso(text):
    with pytest.raises(ValueError):
        parse_utc_ms(text)


def test_frame_from_rows_keeps_last_duplicate():
    frame = frame_from_rows(
        [[T0, 1, 2, 0.5, 1.5, 10], [T0 + HOUR, 1, 2, 0.5, 2.5, 10], [T0, 1, 2, 0.5, 9.9, 10]]
    )
    assert len(frame) == 2
    assert frame["close"].tolist() == [9.9, 2.5]


def test_frame_from_rows_rejects_short_row():
    with pytest.raises(ValueError, match="kolom"):
        frame_from_rows([[T0, 1, 2, 0.5]])


def test_validate_frame_rejects_duplicates_and_nan():
    frame = pd.concat([bars(T0, 2), bars(T0, 1)], ignore_index=True)
    with pytest.raises(ValueError, match="urut|duplikat"):
        validate_frame(frame)
    frame = bars(T0, 3)
    frame.loc[1, "close"] = float("nan")
    with pytest.raises(ValueError, match="NaN"):
        validate_frame(frame)


def test_merge_frames_prefers_later_frame_and_sorts():
    old = bars(T0, 3)
    new = bars(T0 + 2 * HOUR, 2, close_offset=100.0)
    merged = merge_frames([old, new])
    assert len(merged) == 4
    assert merged["timestamp"].is_monotonic_increasing
    # bar T0+2h ada di keduanya: versi new yang menang
    assert merged["close"].iloc[2] == pytest.approx(1.5 + 0 + 100.0)
    assert merge_frames([]).empty
    assert list(merge_frames([empty_frame(), empty_frame()]).columns) == list(OHLCV_COLUMNS)


def test_find_gaps_none_when_contiguous():
    assert find_gaps(bars(T0, 10), "1h") == []
    assert find_gaps(bars(T0, 1), "1h") == []
    assert find_gaps(empty_frame(), "1h") == []


def test_find_gaps_reports_each_hole_with_inclusive_bounds():
    frame = bars(T0, 12, skip={3, 4, 8})
    gaps = find_gaps(frame, "1h")
    assert gaps == [
        Gap(start=stamp_of(T0 + 3 * HOUR), end=stamp_of(T0 + 4 * HOUR), bars=2),
        Gap(start=stamp_of(T0 + 8 * HOUR), end=stamp_of(T0 + 8 * HOUR), bars=1),
    ]
    assert "2 bar hilang" in gaps[0].describe()


def test_find_gaps_rejects_bars_off_grid():
    frame = frame_from_rows([[T0, 1, 2, 0.5, 1.5, 10], [T0 + HOUR + 1, 1, 2, 0.5, 1.5, 10]])
    with pytest.raises(ValueError, match="kelipatan"):
        find_gaps(frame, "1h")
