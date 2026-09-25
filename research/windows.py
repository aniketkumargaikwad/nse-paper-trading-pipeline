"""The frozen research calendar.

DATA_END is the last date complete in every timeframe the run READS. The
LOCKED year is the 365 days ending on it; everything a strategy is built from
comes before it. Windows are closed intervals [start, end] in UTC, matching
what ParquetCandleBackend.read_candles expects.

"Every timeframe the run reads" rather than "every stored timeframe", because
since 25 Sep 2026 the two differ. Sessions after July 2026 were recovered from
Yahoo, whose 5-minute day ends with a candle starting 15:15 - the 15:20 and
15:25 candles are simply not served. A run that reads 5-minute bars (or 60m,
which is built from them) has to stop where the 5-minute data is whole. A
daily-only run reads Yahoo's own daily candle, which is whole, and need not.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from config import IST, SUPPORTED_TIMEFRAMES, UTC

LAST_BAR_START_IST = time(15, 25)     # the final 5-minute candle of an NSE session
LOCKED_DAYS = 365
DATA_END_COVERAGE = 0.9

DAILY_TRAINING_START = date(2010, 1, 1)
STOCK_INTRADAY_TRAINING_START = date(2017, 4, 3)    # where Dhan's intraday archive begins
INDEX_INTRADAY_TRAINING_START = date(2023, 10, 4)   # where Yahoo's 60m index history began

_TICK = timedelta(microseconds=1)


def ist_midnight(day: date) -> datetime:
    """The instant a trading day begins in India, in UTC."""
    return datetime.combine(day, time(0), tzinfo=IST).astimezone(UTC)


def training_start(*, is_index: bool, timeframe: str) -> date:
    if timeframe not in SUPPORTED_TIMEFRAMES:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. "
            f"Allowed: {', '.join(SUPPORTED_TIMEFRAMES)}"
        )
    if timeframe == "day":
        return DAILY_TRAINING_START
    return INDEX_INTRADAY_TRAINING_START if is_index else STOCK_INTRADAY_TRAINING_START


def data_end_from(five_min_index: pd.DatetimeIndex, day_index: pd.DatetimeIndex) -> date:
    """Last date complete in both the 5-minute and the daily candles.

    A day is complete only when it has a 5-minute candle starting EXACTLY at
    15:25 IST - a special evening session (e.g. Muhurat trading) starting
    later does not count. DATA_END is the latest date that is complete in the
    5-minute data AND has a daily candle: the intersection of the two date
    sets, not the minimum of their two maxima.

    Measured from the data rather than a holiday calendar, because the
    holiday file only covers the current year.
    """
    if len(five_min_index) == 0 or len(day_index) == 0:
        raise ValueError(
            "cannot place DATA_END: the reference symbol has no 5-minute or no daily candles"
        )
    local = pd.DatetimeIndex(five_min_index).tz_convert(IST)
    complete = {d for d, t in zip(local.date, local.time) if t == LAST_BAR_START_IST}
    if not complete:
        raise ValueError("no complete 5-minute session: no candle starts at 15:25 IST")
    daily_dates = set(pd.DatetimeIndex(day_index).tz_convert(IST).date)
    both = complete & daily_dates
    if not both:
        raise ValueError("no date is complete in both the 5-minute and the daily candles")
    return max(both)


def daily_data_end_from(day_index: pd.DatetimeIndex) -> date:
    """Last date with a daily candle - DATA_END for a run that reads nothing finer.

    There is no 15:25 test to apply to a daily candle: it is one bar, and it
    either exists or it does not. What it cannot tell is whether the session
    had CLOSED when the candle was written, so that guarantee lives with the
    writer - `scripts/recover_prices.py` lists today as a daily session only
    after 15:30 IST - rather than being guessed at here from one bar.
    """
    if len(day_index) == 0:
        raise ValueError("cannot place DATA_END: the reference symbol has no daily candles")
    return max(pd.DatetimeIndex(day_index).tz_convert(IST).date)


def reads_intraday(timeframes: Sequence[str]) -> bool:
    """True when any of `timeframes` is built from the 5-minute candles.

    Decides which completeness rule places DATA_END: every stock timeframe
    below a day is resampled from the 5-minute base, so it inherits whatever
    that base is missing.
    """
    if isinstance(timeframes, str):
        raise TypeError(f"timeframes must be a sequence, not a bare string {timeframes!r}")
    if not timeframes:
        raise ValueError("a run must read at least one timeframe")
    return any(tf != "day" for tf in timeframes)


def universe_data_end(symbol_ends: Sequence[date], *, coverage: float = DATA_END_COVERAGE) -> date:
    """The latest date at least `coverage` of symbols are complete through.

    A plain minimum would let one stale or delisted symbol drag the whole
    universe back a year; a maximum would admit a month that only a handful of
    symbols actually have. In August 2026, 184 of 200 stocks lost their closing
    candles, so the honest answer for the universe is July.
    """
    if not 0 < coverage <= 1:
        raise ValueError(f"coverage must be above 0 and at most 1, got {coverage}")
    ordered = sorted(symbol_ends)
    if not ordered:
        raise ValueError("no symbol produced a complete session, so DATA_END cannot be placed")
    index = int((1 - coverage) * len(ordered) + 1e-9)
    return ordered[min(index, len(ordered) - 1)]


def trim_to_data_end(frame: pd.DataFrame, data_end: date) -> pd.DataFrame:
    """Drop every candle that starts after DATA_END."""
    return frame[frame.index < ist_midnight(data_end + timedelta(days=1))]


@dataclass(frozen=True)
class ResearchWindows:
    data_end: date

    def __post_init__(self) -> None:
        if type(self.data_end) is not date:
            raise TypeError(
                f"data_end must be a datetime.date, got {type(self.data_end).__name__}"
            )

    @property
    def locked_from(self) -> date:
        return self.data_end - timedelta(days=LOCKED_DAYS - 1)

    @property
    def locked_from_utc(self) -> datetime:
        return ist_midnight(self.locked_from)

    @property
    def end_utc(self) -> datetime:
        return ist_midnight(self.data_end + timedelta(days=1)) - _TICK

    def training(self, *, is_index: bool, timeframe: str) -> tuple[datetime, datetime]:
        start = ist_midnight(training_start(is_index=is_index, timeframe=timeframe))
        return start, self.locked_from_utc - _TICK

    def training_days(
        self, *, is_index: bool, timeframe: str, data_from: date | None = None
    ) -> int:
        """Calendar days of training data. `data_from` is when this combination's candles
        actually begin; a later start shortens the window, an earlier one changes nothing."""
        start = training_start(is_index=is_index, timeframe=timeframe)
        if data_from is not None and data_from > start:
            start = data_from
        return (self.locked_from - start).days

    def full(self, *, is_index: bool, timeframe: str) -> tuple[datetime, datetime]:
        start = ist_midnight(training_start(is_index=is_index, timeframe=timeframe))
        return start, self.end_utc
