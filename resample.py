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
            that is not derivable from the base (e.g. `day`, which is stored
            separately).
    """
    if target_timeframe == BASE_TIMEFRAME:
        # Passthrough returns the CALLER'S frame, not a copy: this is on the
        # hot read path and copying tens of thousands of rows per backtest
        # buys nothing while every consumer (indicators, signals) is
        # read-only. Callers that intend to mutate must .copy() first.
        return df if not df.empty else _empty_frame()

    target_minutes = TIMEFRAME_MINUTES.get(target_timeframe)
    if target_minutes is None:
        raise ResampleError(
            f"Unknown timeframe {target_timeframe!r}. "
            f"Known: {', '.join(TIMEFRAME_MINUTES)}"
        )

    if target_timeframe not in RESAMPLE_TARGETS:
        raise ResampleError(
            f"{target_timeframe} cannot be derived by resampling the "
            f"{BASE_TIMEFRAME} base. Derivable targets: "
            f"{', '.join(sorted(RESAMPLE_TARGETS))}. Daily candles are stored "
            "separately (Dhan's daily feed is corporate-action adjusted and "
            "reaches back to inception), not resampled from intraday data."
        )

    if df.empty:
        return _empty_frame()
    if df.index.tz is None:
        raise ResampleError(
            "resample_candles requires a tz-aware UTC index (got naive timestamps)"
        )

    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ResampleError(
            f"Frame is missing required column(s): {', '.join(missing)}"
        )

    return _bucket(df, target_minutes)


def aggregate_for_reference(df: pd.DataFrame, target_timeframe: str) -> pd.DataFrame:
    """Aggregate ANY frame up to `target_timeframe`, including `day`.

    Separate from `resample_candles` because it answers a different question.
    `resample_candles` serves candles for TRADING, and refuses `day` because
    stored daily candles come from the provider's corporate-action-adjusted
    feed, which intraday bars cannot reproduce.

    This one serves a strategy REFERENCING a higher timeframe from inside a
    lower-timeframe run — a daily trend filter under a 15m entry. Here the
    derived bar is what you want: it is built from the very candles the
    strategy trades, so the filter and the entry can never disagree about
    what the price was. Pulling in the adjusted daily feed instead would put
    two differently-adjusted series in one strategy, and a filter that
    disagrees with the bars it gates is worse than no filter.

    Bars are stamped at their session-anchored START, exactly as
    `resample_candles` stamps them.
    """
    if df.empty:
        return _empty_frame()
    if df.index.tz is None:
        raise ResampleError(
            "aggregate_for_reference requires a tz-aware UTC index "
            "(got naive timestamps)"
        )
    target_minutes = TIMEFRAME_MINUTES.get(target_timeframe)
    if target_minutes is None:
        raise ResampleError(
            f"Unknown timeframe {target_timeframe!r}. "
            f"Known: {', '.join(TIMEFRAME_MINUTES)}"
        )
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    if missing:
        raise ResampleError(
            f"Frame is missing required column(s): {', '.join(missing)}"
        )
    return _bucket(df, target_minutes)


def _bucket(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """Session-anchored OHLCV aggregation into `target_minutes` buckets."""
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
    # from the bucket NUMBER rather than taken from the first candle - so a
    # bucket whose opening candle is missing is still labelled correctly.
    #
    # Vectorised deliberately: building these with pd.Timestamp() per row was
    # ~92% of this function's runtime, and this sits on the hot read path
    # between the database and the backtest engine.
    #
    # tz_localize is unambiguous here because India observes no DST, so IST
    # wall times can never be ambiguous or nonexistent.
    days = pd.to_datetime(out.index.get_level_values(0))
    buckets = out.index.get_level_values(1).to_numpy()
    offsets = SESSION_OPEN_MINUTES + buckets * target_minutes
    out.index = (
        pd.DatetimeIndex(days + pd.to_timedelta(offsets, unit="m"))
        .tz_localize(IST)
        .tz_convert("UTC")
        .rename("ts")
    )
    return out[OHLCV_COLUMNS].sort_index()
