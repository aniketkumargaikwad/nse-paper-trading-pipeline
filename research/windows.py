"""The frozen research calendar.

DATA_END is the last date complete in every stored timeframe. The LOCKED year
is the 365 days ending on it; everything a strategy is built from comes before
it. Windows are closed intervals [start, end] in UTC, matching what
ParquetCandleBackend.read_candles expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from config import IST, SUPPORTED_TIMEFRAMES, UTC

LAST_BAR_START_IST = time(15, 25)     # the final 5-minute candle of an NSE session
LOCKED_DAYS = 365

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
