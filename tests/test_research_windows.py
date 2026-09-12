"""The frozen calendar: where prices end, where training ends, what is locked."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.windows import (  # noqa: E402
    ResearchWindows,
    data_end_from,
    ist_midnight,
    training_start,
    trim_to_data_end,
    universe_data_end,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
TICK = timedelta(microseconds=1)


def five_min(day: date, last: time) -> pd.DatetimeIndex:
    t = datetime.combine(day, time(9, 15), tzinfo=IST)
    stamps = []
    while t.time() <= last:
        stamps.append(t.astimezone(UTC))
        t += timedelta(minutes=5)
    return pd.DatetimeIndex(stamps)


def at(day: date, moment: time) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([datetime.combine(day, moment, tzinfo=IST).astimezone(UTC)])


def daily(*days: date) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([ist_midnight(d) for d in days])


def test_india_midnight_is_1830_utc_the_day_before():
    assert ist_midnight(date(2026, 8, 27)) == datetime(2026, 8, 26, 18, 30, tzinfo=UTC)


def test_a_day_that_stops_before_the_last_candle_is_not_data_end():
    idx = five_min(date(2026, 8, 27), time(15, 25)).append(five_min(date(2026, 8, 28), time(15, 10)))
    assert data_end_from(idx, daily(date(2026, 8, 27), date(2026, 8, 28))) == date(2026, 8, 27)


def test_data_end_is_limited_by_the_daily_candles():
    idx = five_min(date(2026, 8, 27), time(15, 25)).append(five_min(date(2026, 8, 28), time(15, 25)))
    assert data_end_from(idx, daily(date(2026, 8, 27))) == date(2026, 8, 27)


def test_a_special_evening_session_does_not_count_as_complete():
    # 28 Aug stops at 15:10 (normal session) then has a Muhurat-style candle
    # at 18:55 - that late candle must NOT make the day complete.
    idx = (
        five_min(date(2026, 8, 27), time(15, 25))
        .append(five_min(date(2026, 8, 28), time(15, 10)))
        .append(at(date(2026, 8, 28), time(18, 55)))
    )
    assert data_end_from(idx, daily(date(2026, 8, 27), date(2026, 8, 28))) == date(2026, 8, 27)


def test_a_complete_session_with_no_matching_daily_candle_raises():
    # 27 Aug is only partial in the 5-minute data; 28 Aug is complete, but the
    # daily candles stop at 27 Aug - no date is complete in both.
    idx = five_min(date(2026, 8, 27), time(15, 10)).append(five_min(date(2026, 8, 28), time(15, 25)))
    with pytest.raises(ValueError, match="no date is complete in both"):
        data_end_from(idx, daily(date(2026, 8, 27)))


def test_empty_five_minute_history_is_refused():
    with pytest.raises(ValueError, match="cannot place DATA_END"):
        data_end_from(pd.DatetimeIndex([], tz="UTC"), daily(date(2026, 8, 27)))


def test_empty_daily_history_is_refused():
    with pytest.raises(ValueError, match="cannot place DATA_END"):
        data_end_from(five_min(date(2026, 8, 27), time(15, 25)), pd.DatetimeIndex([], tz="UTC"))


def test_no_candle_at_1525_is_refused():
    idx = five_min(date(2026, 8, 27), time(15, 10))
    with pytest.raises(ValueError, match="no candle starts at 15:25 IST"):
        data_end_from(idx, daily(date(2026, 8, 27)))


def test_the_locked_year_is_365_days_ending_on_data_end():
    w = ResearchWindows(date(2026, 8, 27))
    assert w.locked_from == date(2025, 8, 28)
    assert w.locked_from_utc == ist_midnight(date(2025, 8, 28))


def test_training_ends_one_tick_before_the_locked_year():
    w = ResearchWindows(date(2026, 8, 27))
    start, end = w.training(is_index=False, timeframe="15m")
    assert start == ist_midnight(date(2017, 4, 3))
    assert end + TICK == w.locked_from_utc


def test_the_full_window_ends_at_the_last_moment_of_data_end():
    w = ResearchWindows(date(2026, 8, 27))
    _, end = w.full(is_index=True, timeframe="60m")
    assert end + TICK == ist_midnight(date(2026, 8, 28))


def test_a_non_date_data_end_is_rejected():
    with pytest.raises(TypeError):
        ResearchWindows(datetime(2026, 8, 27))
    with pytest.raises(TypeError):
        ResearchWindows(pd.Timestamp("2026-08-27"))


def test_training_start_depends_on_kind_and_timeframe():
    assert training_start(is_index=False, timeframe="day") == date(2010, 1, 1)
    assert training_start(is_index=True, timeframe="day") == date(2010, 1, 1)
    assert training_start(is_index=True, timeframe="60m") == date(2023, 10, 4)
    assert training_start(is_index=False, timeframe="5m") == date(2017, 4, 3)
    assert training_start(is_index=True, timeframe="5m") == date(2023, 10, 4)
    assert training_start(is_index=False, timeframe="15m") == date(2017, 4, 3)


def test_training_start_rejects_an_unknown_timeframe():
    with pytest.raises(ValueError):
        training_start(is_index=False, timeframe="1d")


def test_training_days_counts_calendar_days():
    w = ResearchWindows(date(2026, 8, 27))
    assert w.training_days(is_index=True, timeframe="60m") == (date(2025, 8, 28) - date(2023, 10, 4)).days


def test_training_days_is_shortened_by_a_later_data_start():
    w = ResearchWindows(date(2026, 8, 27))
    assert w.training_days(
        is_index=False, timeframe="15m", data_from=date(2024, 10, 1)
    ) == (date(2025, 8, 28) - date(2024, 10, 1)).days


def test_training_days_ignores_a_data_start_earlier_than_training_start():
    w = ResearchWindows(date(2026, 8, 27))
    without = w.training_days(is_index=False, timeframe="15m")
    earlier = w.training_days(is_index=False, timeframe="15m", data_from=date(2000, 1, 1))
    assert earlier == without


def test_trim_keeps_data_end_and_drops_later_candles():
    idx = pd.DatetimeIndex([
        datetime(2026, 8, 27, 15, 25, tzinfo=IST).astimezone(UTC),
        datetime(2026, 8, 28, 9, 15, tzinfo=IST).astimezone(UTC),
    ])
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    assert list(trim_to_data_end(frame, date(2026, 8, 27))["close"]) == [1.0]


def test_universe_data_end_ignores_a_few_stragglers():
    ends = [date(2026, 7, 31)] * 161 + [date(2026, 8, 6)] * 23 + [date(2026, 8, 27)] * 4 + [date(2026, 8, 28)] * 12
    assert universe_data_end(ends) == date(2026, 7, 31)


def test_one_stale_symbol_does_not_drag_the_whole_universe_back():
    ends = [date(2025, 1, 2)] + [date(2026, 7, 31)] * 199
    assert universe_data_end(ends) == date(2026, 7, 31)


def test_coverage_is_a_floor_not_an_average():
    """90% coverage means at least 90% of symbols are complete through the date."""
    ends = [date(2026, 1, 1)] * 30 + [date(2026, 7, 31)] * 70
    assert universe_data_end(ends) == date(2026, 1, 1)


def test_a_stricter_coverage_picks_an_earlier_date():
    ends = [date(2026, 1, 1)] * 5 + [date(2026, 7, 31)] * 95
    assert universe_data_end(ends, coverage=1.0) == date(2026, 1, 1)
    assert universe_data_end(ends, coverage=0.9) == date(2026, 7, 31)


def test_a_single_symbol_is_its_own_data_end():
    assert universe_data_end([date(2026, 7, 31)]) == date(2026, 7, 31)


def test_no_symbol_ends_is_refused():
    with pytest.raises(ValueError, match="no symbol"):
        universe_data_end([])


def test_coverage_outside_zero_to_one_is_refused():
    with pytest.raises(ValueError, match="coverage"):
        universe_data_end([date(2026, 7, 31)], coverage=1.5)


def test_closed_interval_boundaries_match_what_the_backend_expects():
    idx = pd.DatetimeIndex([ist_midnight(date(2025, 8, 27)), ist_midnight(date(2025, 8, 28))])
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    w = ResearchWindows(date(2026, 8, 27))

    start, end = w.training(is_index=False, timeframe="day")
    trained = frame[(frame.index >= start) & (frame.index <= end)]
    assert list(trained["close"]) == [1.0]

    start, end = w.full(is_index=False, timeframe="day")
    full = frame[(frame.index >= start) & (frame.index <= end)]
    assert list(full["close"]) == [1.0, 2.0]
