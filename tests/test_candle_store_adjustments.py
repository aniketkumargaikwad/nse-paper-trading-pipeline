"""The read path must actually apply corporate-action corrections.

price_adjust.py is tested on its own, but the bug this whole thing exists to
kill only dies if the corrections reach a BACKTEST. Between the two sits
CandleStore, resampling, and a memo - so these tests exercise the seam rather
than the maths.

The scenario throughout is EICHERMOT's 1:10 split: raw intraday prices of
21,780 before it and 2,178 after, which no strategy can survive uncorrected.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_store import CandleStore  # noqa: E402
from coverage_math import CoverageRange  # noqa: E402
from price_adjust import Adjustment  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

SPLIT_DAY = date(2020, 8, 24)


def session(day: date, price: float, bars: int = 12) -> pd.DataFrame:
    """One trading day of flat 5-minute candles at `price`."""
    start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(bars)],
        name="ts",
    )
    return pd.DataFrame(
        {
            "open": np.full(bars, price),
            "high": np.full(bars, price * 1.01),
            "low": np.full(bars, price * 0.99),
            "close": np.full(bars, price),
            "volume": np.full(bars, 1000.0),
        },
        index=index,
    )


# Two sessions either side of the split, exactly as Dhan's raw feed serves
# them: no price actually moved, but the numbers fall by 90%.
RAW = pd.concat([
    session(date(2020, 8, 21), 21_780.0),
    session(SPLIT_DAY, 2_178.0),
])

# The daily feed for the same two sessions, already adjusted by Dhan: both
# days sit on the post-split basis and nothing dramatic happened.
DAILY = pd.concat([
    session(date(2020, 8, 21), 2_178.0, bars=1),
    session(SPLIT_DAY, 2_178.0, bars=1),
])

FROM = datetime(2020, 8, 20, tzinfo=UTC)
TO = datetime(2020, 8, 25, tzinfo=UTC)

# Everything before the split needs dividing by ten to reach today's basis.
CORRECTION = Adjustment(
    effective_from=date(2017, 4, 3),
    effective_to=date(2020, 8, 21),
    price_factor=0.1,
    volume_factor=10.0,
    sample_days=833,
)


class Backend:
    """In-memory backend that serves RAW and a configurable correction set."""

    def __init__(self, adjustments=()):
        self.adjustments = list(adjustments)
        self.asked_for: list[str] = []

    @property
    def reads(self) -> int:
        return len(self.asked_for)

    def instrument_id(self, symbol):
        return 7

    def read_candles(self, instrument_id, timeframe, from_utc, to_utc):
        frame = {"5m": RAW, "day": DAILY}.get(timeframe)
        if frame is None:
            return None
        return frame[(frame.index >= from_utc) & (frame.index <= to_utc)]

    def write_candles(self, instrument_id, timeframe, df):
        pass

    def read_coverage(self, instrument_id, timeframe):
        return CoverageRange(first_ts=FROM, last_ts=TO)

    def write_coverage(self, instrument_id, timeframe, coverage, source):
        pass

    def write_quality_flags(self, rows):
        pass

    def read_price_adjustments(self, instrument_id, timeframe):
        self.asked_for.append(timeframe)
        return self.adjustments


class OlderBackend(Backend):
    """A backend predating sql/010: no adjustment table, no such method."""

    read_price_adjustments = None

    def __getattribute__(self, name):
        if name == "read_price_adjustments":
            raise AttributeError(name)
        return object.__getattribute__(self, name)


class Provider:
    name = "fake"

    def max_history_days(self, timeframe):
        return 9 * 365

    def fetch(self, symbol, timeframe, from_utc, to_utc):
        return RAW.iloc[0:0]


def make_store(adjustments=(), backend_class=Backend):
    backend = backend_class(adjustments)
    return CandleStore(backend=backend, provider=Provider()), backend


def closes_by_day(frame: pd.DataFrame) -> dict[date, float]:
    return frame["close"].groupby(frame.index.tz_convert(IST).date).last().to_dict()


# ---------------------------------------------------------------------------


def test_uncorrected_read_still_shows_the_fake_crash():
    """The premise. If this ever passes trivially, the fixture is wrong."""
    store, _ = make_store()
    closes = closes_by_day(store.get_candles("NSE:EICHERMOT", "5m", FROM, TO))
    assert closes[date(2020, 8, 21)] / closes[SPLIT_DAY] == pytest.approx(10.0)


def test_a_stored_correction_removes_the_crash():
    store, _ = make_store([CORRECTION])
    closes = closes_by_day(store.get_candles("NSE:EICHERMOT", "5m", FROM, TO))
    # Both sides now on the post-split basis: the overnight move is nothing.
    assert closes[date(2020, 8, 21)] == pytest.approx(2_178.0)
    assert closes[SPLIT_DAY] == pytest.approx(2_178.0)


def test_every_price_column_moves_together():
    """A corrected close beside a raw high is worse than no correction.

    Stops would fire against prices ten times the close and the frame would
    fail its own OHLC sanity check.
    """
    store, _ = make_store([CORRECTION])
    frame = store.get_candles("NSE:EICHERMOT", "5m", FROM, TO)
    before = frame[frame.index.tz_convert(IST).date == date(2020, 8, 21)]
    assert (before["low"] <= before["close"]).all()
    assert (before["close"] <= before["high"]).all()
    assert before["high"].max() == pytest.approx(2_178.0 * 1.01)


def test_volume_is_left_alone():
    """Deliberate: the measured volume gap tracks feed changes, not splits.

    See the note on price_adjust.Adjustment. Applying volume_factor would
    have multiplied nine years of RELIANCE volume by two for no reason.
    """
    store, _ = make_store([CORRECTION])
    frame = store.get_candles("NSE:EICHERMOT", "5m", FROM, TO)
    assert (frame["volume"] == 1000.0).all()


def test_resampled_timeframes_are_corrected_too():
    """A 1-hour backtest is exactly as exposed to the split as a 5-minute one."""
    store, _ = make_store([CORRECTION])
    hourly = store.get_candles("NSE:EICHERMOT", "60m", FROM, TO)
    assert not hourly.empty
    assert hourly["close"].max() < 2_500.0


def test_candles_outside_the_period_are_untouched():
    store, _ = make_store([CORRECTION])
    frame = store.get_candles("NSE:EICHERMOT", "5m", FROM, TO)
    after = frame[frame.index.tz_convert(IST).date == SPLIT_DAY]
    assert (after["close"] == 2_178.0).all()


def test_a_backend_without_the_table_still_serves_candles():
    """A database missing sql/010 must degrade, not fall over.

    get_candles is the single read path for the entire platform; making it
    depend on an optional table would take everything down with it.
    """
    store, _ = make_store(backend_class=OlderBackend)
    assert not store.get_candles("NSE:EICHERMOT", "5m", FROM, TO).empty


def test_corrections_are_read_once_per_instrument():
    """A backtest calls get_candles repeatedly; corrections change on demand.

    Not a micro-optimisation: a sweep over 50 symbols x 20 strategies would
    otherwise add a thousand database round trips to a path that is
    otherwise served entirely from cache.
    """
    store, backend = make_store([CORRECTION])
    for _ in range(5):
        store.get_candles("NSE:EICHERMOT", "5m", FROM, TO)
    assert backend.reads == 1


def test_daily_candles_are_not_corrected():
    """Dhan's daily feed arrives already adjusted.

    This backend deliberately returns the same correction for every
    timeframe, the way a backend with a stray 'day' row would. The read path
    must refuse it on its own rather than trusting the table's constraint.
    """
    store, backend = make_store([CORRECTION])
    store.get_candles("NSE:EICHERMOT", "5m", FROM, TO)
    daily = store.get_candles("NSE:EICHERMOT", "day", FROM, TO)

    # Never even asked. Refusing outright beats relying on the table's
    # constraint to keep 'day' rows from existing.
    assert backend.asked_for == ["5m"]
    # Untouched: applying the 5m factor here would divide an already-correct
    # 2,178 by ten and invent the crash all over again.
    assert (daily["close"] == 2_178.0).all()
