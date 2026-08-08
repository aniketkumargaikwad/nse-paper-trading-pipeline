"""Pure, session-anchored resampling of the 5-minute base timeframe.

Higher intraday timeframes are DERIVED rather than stored, which guarantees
they are mutually consistent and lets new timeframes be added without
refetching anything.

Why session-anchored and not clock-anchored
-------------------------------------------
Buckets are measured from the 09:15 IST session open, not from clock
boundaries. This is a correctness requirement learned from a real defect in a
free data feed: its 30-minute bars start at 09:00, so the first bar of each
day holds only 09:15-09:30 - fifteen minutes of data in a bar labelled thirty.
Anchoring to the session makes that class of bug impossible.

A bucket with no underlying candles produces NO row. A synthesised candle is
indistinguishable from real data and could fire a real signal.
"""

from __future__ import annotations

import pandas as pd

from config import BASE_TIMEFRAME, IST, TIMEFRAME_MINUTES

# NSE regular session opens at 09:15 IST; every bucket is measured from there.
SESSION_OPEN_MINUTES = 9 * 60 + 15

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]

# Timeframes this module may DERIVE by resampling the 5-minute base. 'day' is
# deliberately excluded even though 375 is numerically a multiple of 5: it is
# stored separately (see module docstring / config.STORED_TIMEFRAMES).
RESAMPLE_TARGETS: frozenset[str] = frozenset(TIMEFRAME_MINUTES) - {BASE_TIMEFRAME, "day"}


class ResampleError(ValueError):
    """Raised when a frame or target timeframe cannot be resampled."""


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(
        columns=OHLCV_COLUMNS,
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )


def resample_candles(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Aggregate 5-minute candles into `target_timeframe`, session-anchored.

    Args:
        df: canonical 5-minute frame (tz-aware UTC index, OHLCV columns).
        target_timeframe: '5m', '15m', '25m', '30m' or '60m'.

    Returns:
        A canonical frame indexed by each bucket's session-anchored START time.

    Raises:
        ResampleError: for an unknown timeframe, a naive index, or a target
            that is not a whole multiple of the 5-minute base.
    """
    if target_timeframe == BASE_TIMEFRAME:
        return df  # passthrough: nothing to aggregate

    target_minutes = TIMEFRAME_MINUTES.get(target_timeframe)
    if target_minutes is None:
        raise ResampleError(
            f"Unknown timeframe {target_timeframe!r}. "
            f"Known: {', '.join(TIMEFRAME_MINUTES)}"
        )

    base_minutes = TIMEFRAME_MINUTES[BASE_TIMEFRAME]
    # 'day' (375 min) is numerically a multiple of the 5-minute base, but it
    # is NOT derived by resampling: Dhan's daily feed is corporate-action
    # adjusted and reaches back to inception, so it is stored separately
    # (see config.STORED_TIMEFRAMES / source_timeframe_for). Only genuine
    # intraday timeframes may be resampled here.
    if target_minutes % base_minutes != 0 or target_timeframe not in RESAMPLE_TARGETS:
        raise ResampleError(
            f"{target_timeframe} ({target_minutes} min) is not a whole intraday "
            f"multiple of the {BASE_TIMEFRAME} base ({base_minutes} min) that "
            "can be derived by resampling. Daily candles are stored separately."
        )

    if df.empty:
        return _empty_frame()
    if df.index.tz is None:
        raise ResampleError(
            "resample_candles requires a tz-aware UTC index (got naive timestamps)"
        )

    frame = df.sort_index()
    ist_index = frame.index.tz_convert(IST)

    # Bucket key = (session date, whole buckets elapsed since 09:15 IST).
    # Including the session date keeps a bucket from ever spanning two days.
    minutes_from_open = ist_index.hour * 60 + ist_index.minute - SESSION_OPEN_MINUTES
    bucket_number = minutes_from_open // target_minutes
    session_date = ist_index.date

    grouped = frame.groupby([session_date, bucket_number], sort=True)
    out = grouped.agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )

    # Stamp each bucket with its own session-anchored start time, computed
    # from the bucket number rather than taken from the first candle - so a
    # bucket whose opening candle is missing is still labelled correctly.
    starts = []
    for day, bucket in out.index:
        offset = SESSION_OPEN_MINUTES + int(bucket) * target_minutes
        starts.append(
            pd.Timestamp(
                year=day.year, month=day.month, day=day.day,
                hour=offset // 60, minute=offset % 60, tz=IST,
            ).tz_convert("UTC")
        )

    out.index = pd.DatetimeIndex(starts, name="ts")
    return out[OHLCV_COLUMNS].sort_index()
