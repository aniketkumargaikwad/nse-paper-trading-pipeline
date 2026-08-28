"""Running detection twice must not destroy the corrections it made.

THE BUG THIS PINS
-----------------
Detection originally read candles through CandleStore, which APPLIES stored
corrections. So the second run measured an already-corrected symbol, found it
clean - correctly, it was clean - and wrote that empty result, deleting the
corrections that made it look clean. Run three re-detected them. Run four
deleted them again.

It hid for a whole session because every re-run used --dry-run, which never
writes. "Detection converges" was measuring nothing.

Real consequence: after one NIFTY200 sweep, EICHERMOT's 1:10 split was
uncorrected again, and a 90% overnight crash that never happened was back in
the backtest data.

The fix is to measure RAW candles. The gap between the two feeds is an
absolute fact that does not move when corrections are stored, so measuring it
gives the same answer every time.
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
from price_adjust import (  # noqa: E402
    daily_ratio,
    find_segments,
    material_adjustments,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
SPLIT = date(2020, 8, 24)


def _session(day: date, price: float, bars: int) -> pd.DataFrame:
    start = datetime(day.year, day.month, day.day, 9, 15, tzinfo=IST)
    idx = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(bars)],
        name="ts")
    return pd.DataFrame(
        {"open": np.full(bars, price), "high": np.full(bars, price),
         "low": np.full(bars, price), "close": np.full(bars, price),
         "volume": np.full(bars, 1000.0)}, index=idx)


def _history(pre: float, post: float):
    """Ten sessions either side of a 1:10 split, raw and adjusted-daily."""
    days = [date(2020, 8, 10) + timedelta(days=n) for n in range(20)]
    raw, adj = [], []
    for d in days:
        if d.weekday() >= 5:
            continue
        before = d < SPLIT
        raw.append(_session(d, pre if before else post, 12))
        adj.append(_session(d, post, 1))       # daily feed: already adjusted
    return pd.concat(raw), pd.concat(adj)


RAW, DAILY = _history(21_780.0, 2_178.0)


class Backend:
    """Backend whose read_candles returns RAW candles, as the real one does."""

    def __init__(self):
        self.stored: dict[tuple[int, str], list] = {}

    def instrument_id(self, symbol): return 1
    def write_candles(self, *a, **k): pass
    def write_coverage(self, *a, **k): pass
    def write_quality_flags(self, rows): pass

    def read_coverage(self, instrument_id, timeframe):
        return CoverageRange(first_ts=RAW.index[0], last_ts=RAW.index[-1])

    def read_candles(self, instrument_id, timeframe, from_utc, to_utc):
        frame = {"5m": RAW, "day": DAILY}.get(timeframe)
        if frame is None:
            return None
        return frame[(frame.index >= from_utc) & (frame.index <= to_utc)]

    def read_price_adjustments(self, instrument_id, timeframe):
        return list(self.stored.get((instrument_id, timeframe), []))

    def replace_price_adjustments(self, instrument_id, timeframe, adjustments):
        self.stored[(instrument_id, timeframe)] = list(adjustments)
        return len(adjustments)


class Provider:
    name = "fake"
    def max_history_days(self, timeframe): return 9 * 365
    def fetch(self, *a, **k): return RAW.iloc[0:0]


def _last_close_per_day(frame):
    return frame["close"].groupby(frame.index.tz_convert(IST).date).last()


def _detect_and_store(backend, read):
    """One detection pass, mirroring scripts/detect_adjustments.py."""
    lo, hi = RAW.index[0], RAW.index[-1]
    intraday, daily = read(backend, "5m", lo, hi), read(backend, "day", lo, hi)
    found = material_adjustments(find_segments(
        daily_ratio(_last_close_per_day(intraday), _last_close_per_day(daily))))
    backend.replace_price_adjustments(1, "5m", found)
    return found


def _read_raw(backend, timeframe, lo, hi):
    return backend.read_candles(1, timeframe, lo, hi)


def _read_corrected(backend, timeframe, lo, hi):
    """The old, broken path: through CandleStore, so corrections apply."""
    return CandleStore(backend=backend, provider=Provider()).get_candles(
        "NSE:X", timeframe, lo, hi)


def test_detection_finds_the_split():
    backend = Backend()
    found = _detect_and_store(backend, _read_raw)
    assert len(found) == 1
    assert found[0].price_factor == pytest.approx(0.1, rel=1e-3)


def test_running_detection_twice_keeps_the_correction():
    """The regression. Second run must not empty the store."""
    backend = Backend()
    _detect_and_store(backend, _read_raw)
    first = backend.read_price_adjustments(1, "5m")
    _detect_and_store(backend, _read_raw)
    second = backend.read_price_adjustments(1, "5m")

    assert second, "second run deleted the corrections"
    assert len(second) == len(first)
    assert second[0].price_factor == first[0].price_factor


def test_detection_is_stable_over_many_runs():
    backend = Backend()
    for _ in range(5):
        _detect_and_store(backend, _read_raw)
        assert backend.read_price_adjustments(1, "5m")


def test_reading_corrected_candles_is_what_broke_it():
    """Proves the diagnosis rather than trusting it.

    Feed detection CORRECTED candles and the second pass empties the store -
    exactly the failure seen in production. If this ever stops failing, the
    reasoning behind reading raw has changed and the comment is stale.
    """
    backend = Backend()
    _detect_and_store(backend, _read_raw)
    assert backend.read_price_adjustments(1, "5m")

    _detect_and_store(backend, _read_corrected)
    assert backend.read_price_adjustments(1, "5m") == [], (
        "expected the known-bad path to wipe the corrections")
