"""Tests for session-anchored resampling of the 5-minute base."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from resample import ResampleError, resample_candles  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def five_min_frame(rows: list[tuple], start_ist: datetime | None = None) -> pd.DataFrame:
    """rows = [(open, high, low, close, volume)], 5-minute spacing from 09:15 IST."""
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(len(rows))],
        name="ts",
    )
    arr = np.asarray(rows, dtype=float).reshape(len(rows), 5) if rows else np.empty((0, 5))
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": arr[:, 4]},
        index=index,
    )


def test_three_five_minute_candles_become_one_fifteen_minute_candle() -> None:
    df = five_min_frame([
        (100, 105, 99, 104, 10),    # 09:15
        (104, 108, 103, 107, 20),   # 09:20
        (107, 109, 101, 102, 30),   # 09:25
    ])
    out = resample_candles(df, "15m")
    assert len(out) == 1
    row = out.iloc[0]
    assert row["open"] == 100      # first
    assert row["high"] == 109      # max
    assert row["low"] == 99        # min
    assert row["close"] == 102     # last
    assert row["volume"] == 60     # sum
    # The bucket is stamped with the SESSION-anchored start, 09:15 IST.
    assert out.index[0] == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)


def test_first_bucket_anchors_to_0915_not_to_the_clock() -> None:
    """The Yahoo bug we must not reproduce: a 30m bucket starting 09:00 would
    contain only 09:15-09:30, i.e. 15 minutes of data in a 30-minute bar."""
    df = five_min_frame([(100, 101, 99, 100, 1)] * 12)  # 09:15 -> 10:10
    out = resample_candles(df, "30m")
    first, second = out.index[0], out.index[1]
    assert first == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)
    assert second == datetime(2026, 8, 3, 9, 45, tzinfo=IST).astimezone(UTC)


def test_partial_trailing_bucket_is_kept() -> None:
    # 4 candles into a 15m grouping: one full bucket + one partial.
    df = five_min_frame([(100, 101, 99, 100, 1)] * 4)
    out = resample_candles(df, "15m")
    assert len(out) == 2
    assert out.iloc[-1]["volume"] == 1  # the lone trailing candle


def test_buckets_never_span_two_sessions() -> None:
    day1 = five_min_frame([(100, 101, 99, 100, 1)] * 2,
                          start_ist=datetime(2026, 8, 3, 15, 20, tzinfo=IST))
    day2 = five_min_frame([(200, 201, 199, 200, 1)] * 2,
                          start_ist=datetime(2026, 8, 4, 9, 15, tzinfo=IST))
    out = resample_candles(pd.concat([day1, day2]), "60m")
    assert len(out) == 2                    # one bucket per session, not merged
    assert out.iloc[0]["close"] == 100
    assert out.iloc[1]["open"] == 200


def test_empty_bucket_produces_no_row() -> None:
    # A gap (halt / missing data) must not create a synthesised candle.
    early = five_min_frame([(100, 101, 99, 100, 1)] * 2)
    late = five_min_frame([(120, 121, 119, 120, 1)] * 2,
                          start_ist=datetime(2026, 8, 3, 11, 15, tzinfo=IST))
    out = resample_candles(pd.concat([early, late]), "15m")
    assert len(out) == 2
    assert not out.isna().any().any()


def test_resampling_to_the_base_is_a_passthrough() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    pd.testing.assert_frame_equal(resample_candles(df, "5m"), df)


def test_empty_input_gives_empty_canonical_frame() -> None:
    out = resample_candles(five_min_frame([]), "15m")
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_day_rejected_because_it_is_stored_not_derived() -> None:
    """`day` is 375 min, a whole multiple of 5 - so arithmetic alone would
    wrongly allow it. It is rejected because Dhan's daily feed is stored
    directly (corporate-action adjusted, back to inception)."""
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    with pytest.raises(ResampleError, match="stored separately"):
        resample_candles(df, "day")


def test_naive_index_rejected() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ResampleError, match="tz-aware"):
        resample_candles(df, "15m")


def test_unknown_timeframe_rejected() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3)
    with pytest.raises(ResampleError, match="Unknown timeframe"):
        resample_candles(df, "7m")


def test_twentyfive_minute_bucketing() -> None:
    # 25m = 5 base candles per bucket; 7 candles -> one full + one partial.
    df = five_min_frame([(100, 101, 99, 100, 2)] * 7)
    out = resample_candles(df, "25m")
    assert len(out) == 2
    assert out.iloc[0]["volume"] == 10   # 5 candles x 2
    assert out.iloc[1]["volume"] == 4    # 2 candles x 2
    assert out.index[1] == datetime(2026, 8, 3, 9, 40, tzinfo=IST).astimezone(UTC)


def test_output_is_sorted_even_if_input_is_not() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 6)
    shuffled = df.iloc[::-1]
    out = resample_candles(shuffled, "15m")
    assert out.index.is_monotonic_increasing


def test_pre_open_candles_bucket_backwards_without_merging() -> None:
    """Pre-open auction bars sit BEFORE 09:15, so minutes_from_open is
    negative. Floor division must round toward -inf, giving them their own
    bucket - not merging them into the 09:15 one. An int()/abs() refactor
    would silently break this."""
    df = five_min_frame(
        [(100, 101, 99, 100, 1)] * 3,
        start_ist=datetime(2026, 8, 3, 9, 0, tzinfo=IST),
    )
    out = resample_candles(df, "15m")
    assert len(out) == 1
    assert out.index[0] == datetime(2026, 8, 3, 9, 0, tzinfo=IST).astimezone(UTC)


def test_single_candle_frame() -> None:
    df = five_min_frame([(100, 105, 99, 104, 7)])
    out = resample_candles(df, "60m")
    assert len(out) == 1
    assert out.iloc[0]["volume"] == 7


def test_missing_column_raises_resample_error() -> None:
    df = five_min_frame([(100, 101, 99, 100, 1)] * 3).drop(columns=["volume"])
    with pytest.raises(ResampleError, match="missing required column"):
        resample_candles(df, "15m")
