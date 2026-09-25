"""Where a run's prices end, for the timeframes it actually reads (25 Sep 2026).

The situation these mirror: sessions after July 2026 were recovered from
Yahoo, whose 5-minute day stops at 15:15. 193 of 200 stocks got their daily
candles through 24 Sep; seven were refused (a corporate action in the overlap)
and stop in July.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.evaluate import data_end_basis, place_data_end  # noqa: E402
from research.windows import ist_midnight  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
DHAN_END = date(2026, 7, 31)
YAHOO_END = date(2026, 9, 24)


def daily(*days: date) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([ist_midnight(d) for d in days])


def session(day: date, last: time) -> list[datetime]:
    t, out = datetime.combine(day, time(9, 15), tzinfo=IST), []
    while t.time() <= last:
        out.append(t.astimezone(UTC))
        t += timedelta(minutes=5)
    return out


STOCKS = [f"NSE:S{i}" for i in range(200)]
REFUSED = set(STOCKS[:7])
DAY_INDEX = {s: daily(DHAN_END) if s in REFUSED else daily(DHAN_END, YAHOO_END) for s in STOCKS}


def five_min(symbol: str, _from: datetime) -> pd.DatetimeIndex:
    stamps = session(DHAN_END, time(15, 25))            # Dhan: the whole day
    if symbol not in REFUSED:
        stamps += session(YAHOO_END, time(15, 15))      # Yahoo: stops at 15:15
    return pd.DatetimeIndex(stamps)


def test_a_daily_only_run_uses_the_recovered_prices():
    data_end, ends = place_data_end(STOCKS, DAY_INDEX, five_min, timeframes=("day",))
    assert data_end == YAHOO_END
    assert len(ends) == 200


def test_a_daily_only_run_never_reads_a_5_minute_candle():
    def refuse(symbol, _from):
        raise AssertionError(f"read 5-minute candles for {symbol}")
    place_data_end(STOCKS, DAY_INDEX, refuse, timeframes=("day",))


def test_any_intraday_bar_keeps_the_run_where_the_5_minute_sessions_are_whole():
    """60m is built from the 5-minute candles, so it inherits the missing close."""
    for timeframes in (("60m", "day"), ("5m", "15m", "25m", "30m", "60m", "day")):
        data_end, _ = place_data_end(STOCKS, DAY_INDEX, five_min, timeframes=timeframes)
        assert data_end == DHAN_END, timeframes


def test_the_refused_stocks_do_not_hold_the_universe_back():
    """Seven of 200 is inside the 10% the coverage rule allows to lag."""
    data_end, ends = place_data_end(STOCKS, DAY_INDEX, five_min, timeframes=("day",))
    assert sorted(ends)[:7] == [DHAN_END] * 7
    assert data_end == YAHOO_END


def test_no_daily_candle_anywhere_is_refused():
    empty = {s: pd.DatetimeIndex([], tz="UTC") for s in STOCKS}
    with pytest.raises(ValueError, match="no daily candles"):
        place_data_end(STOCKS, empty, five_min, timeframes=("day",))


def test_the_run_says_what_its_data_end_was_measured_on():
    assert data_end_basis(("day",)) == "daily candles"
    assert data_end_basis(("60m", "day")) == "whole 5-minute sessions"
