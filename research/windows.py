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

from config import IST, UTC

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
    if timeframe == "day":
        return DAILY_TRAINING_START
    return INDEX_INTRADAY_TRAINING_START if is_index else STOCK_INTRADAY_TRAINING_START


def data_end_from(five_min_index: pd.DatetimeIndex, day_index: pd.DatetimeIndex) -> date:
    """Last date with a full 5-minute session AND a daily candle.

    Measured from the data rather than a holiday calendar, because the holiday
    file only covers the current year.
    """
    if len(five_min_index) == 0 or len(day_index) == 0:
        raise ValueError(
            "cannot place DATA_END: the reference symbol has no 5-minute or no daily candles"
        )
    local = pd.DatetimeIndex(five_min_index).tz_convert(IST)
    complete = [d for d, t in zip(local.date, local.time) if t >= LAST_BAR_START_IST]
    if not complete:
        raise ValueError("no complete 5-minute session: no candle starts at 15:25 IST")
    last_daily = pd.DatetimeIndex(day_index).tz_convert(IST).max().date()
    return min(max(complete), last_daily)


def trim_to_data_end(frame: pd.DataFrame, data_end: date) -> pd.DataFrame:
    """Drop every candle that starts after DATA_END."""
    return frame[frame.index < ist_midnight(data_end + timedelta(days=1))]


@dataclass(frozen=True)
class ResearchWindows:
    data_end: date

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

    def training_days(self, *, is_index: bool, timeframe: str) -> int:
        return (self.locked_from - training_start(is_index=is_index, timeframe=timeframe)).days

    def full(self, *, is_index: bool, timeframe: str) -> tuple[datetime, datetime]:
        start = ist_midnight(training_start(is_index=is_index, timeframe=timeframe))
        return start, self.end_utc
