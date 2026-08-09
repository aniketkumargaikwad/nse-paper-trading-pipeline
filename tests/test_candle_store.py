"""Tests for the candle repository, using an in-memory fake backend."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_store import CandleStore  # noqa: E402
from coverage_math import CoverageRange  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def five_min_frame(count: int, start_ist: datetime | None = None) -> pd.DataFrame:
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=5 * i)).astimezone(UTC) for i in range(count)],
        name="ts",
    )
    return pd.DataFrame(
        {"open": np.full(count, 100.0), "high": np.full(count, 101.0),
         "low": np.full(count, 99.0), "close": np.full(count, 100.5),
         "volume": np.full(count, 1000.0)},
        index=index,
    )


class FakeBackend:
    """Stands in for Supabase: candles, coverage and flags held in memory."""

    def __init__(self):
        self.candles: dict[tuple[int, str], pd.DataFrame] = {}
        self.coverage: dict[tuple[int, str], CoverageRange] = {}
        self.flags: list[dict] = []
        self.instrument_ids = {"NSE:RELIANCE": 1, "NSE:TCS": 2}

    def instrument_id(self, symbol: str) -> int:
        return self.instrument_ids[symbol]

    def read_candles(self, instrument_id, timeframe, from_utc, to_utc):
        df = self.candles.get((instrument_id, timeframe))
        if df is None:
            return None
        return df[(df.index >= from_utc) & (df.index <= to_utc)]

    def write_candles(self, instrument_id, timeframe, df):
        key = (instrument_id, timeframe)
        existing = self.candles.get(key)
        merged = df if existing is None else pd.concat([existing, df])
        self.candles[key] = merged[~merged.index.duplicated(keep="last")].sort_index()

    def read_coverage(self, instrument_id, timeframe):
        return self.coverage.get((instrument_id, timeframe))

    def write_coverage(self, instrument_id, timeframe, coverage, source):
        self.coverage[(instrument_id, timeframe)] = coverage

    def write_quality_flags(self, rows):
        self.flags.extend(rows)


class FakeProvider:
    """Returns canned frames and records every fetch it was asked to do."""

    name = "fake"

    def __init__(self, frame=None, fail_after=None):
        self.frame = frame if frame is not None else five_min_frame(6)
        self.calls: list[tuple] = []
        self.fail_after = fail_after

    def max_history_days(self, timeframe):
        return 5 * 365

    def fetch(self, symbol, timeframe, from_utc, to_utc):
        self.calls.append((symbol, timeframe, from_utc, to_utc))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("provider exploded")
        return self.frame


def make_store(provider=None, backend=None):
    backend = backend or FakeBackend()
    provider = provider or FakeProvider()
    return CandleStore(backend=backend, provider=provider), backend, provider


FROM = datetime(2026, 8, 3, 0, 0, tzinfo=UTC)
TO = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)


# --- caching ----------------------------------------------------------------


def test_first_call_fetches_and_stores() -> None:
    store, backend, provider = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert len(provider.calls) == 1
    assert (1, "5m") in backend.candles
    assert backend.coverage[(1, "5m")] is not None


def test_second_identical_call_makes_no_network_call() -> None:
    """The property that makes backtests fast and offline-capable."""
    store, backend, provider = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert len(provider.calls) == 1  # not 2


def test_reading_works_with_no_network_when_cached() -> None:
    """Market closed / weekend: cached reads must still work."""
    store, backend, _ = make_store()
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)

    class ExplodingProvider:
        name = "exploding"
        def max_history_days(self, timeframe): return 5 * 365
        def fetch(self, *a, **kw):
            raise AssertionError("must not hit the network when cached")

    offline = CandleStore(backend=backend, provider=ExplodingProvider())
    assert len(offline.get_candles("NSE:RELIANCE", "5m", FROM, TO)) == 6


# --- the safety property ----------------------------------------------------


def test_partial_failure_does_not_overstate_coverage() -> None:
    """A provider failure part-way must leave coverage honest."""
    backend = FakeBackend()
    provider = FakeProvider(fail_after=0)
    store = CandleStore(backend=backend, provider=provider)
    with pytest.raises(RuntimeError):
        store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert backend.coverage.get((1, "5m")) is None  # nothing claimed


def test_coverage_stops_at_the_last_stored_candle_not_the_requested_end() -> None:
    """Provider returns less than asked for; coverage must reflect reality."""
    store, backend, _ = make_store(provider=FakeProvider(frame=five_min_frame(3)))
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    stored = backend.candles[(1, "5m")]
    assert backend.coverage[(1, "5m")].last_ts == stored.index[-1]


def test_empty_provider_response_claims_no_coverage() -> None:
    """No data must never be recorded as covered."""
    from providers.base import empty_frame

    store, backend, _ = make_store(provider=FakeProvider(frame=empty_frame()))
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert backend.coverage.get((1, "5m")) is None


# --- quality gating ---------------------------------------------------------


def test_invalid_candles_are_dropped_and_flagged() -> None:
    bad = five_min_frame(3)
    bad.iloc[1, bad.columns.get_loc("high")] = 1.0  # high below close
    store, backend, _ = make_store(provider=FakeProvider(frame=bad))
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert len(backend.candles[(1, "5m")]) == 2          # bad row removed
    assert any(f["flag_type"] == "ohlc_invalid" for f in backend.flags)


def test_all_candles_invalid_claims_no_coverage() -> None:
    bad = five_min_frame(2)
    bad["high"] = 1.0  # every row structurally impossible
    store, backend, _ = make_store(provider=FakeProvider(frame=bad))
    store.ensure_coverage("NSE:RELIANCE", "5m", FROM, TO)
    assert backend.coverage.get((1, "5m")) is None
    assert len(backend.flags) == 2


# --- reading and resampling -------------------------------------------------


def test_get_candles_resamples_to_requested_timeframe() -> None:
    store, _, _ = make_store(provider=FakeProvider(frame=five_min_frame(6)))
    out = store.get_candles("NSE:RELIANCE", "15m", FROM, TO)
    assert len(out) == 2                                  # 6 x 5m -> 2 x 15m
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_get_candles_at_base_timeframe_is_not_resampled() -> None:
    store, _, _ = make_store(provider=FakeProvider(frame=five_min_frame(6)))
    assert len(store.get_candles("NSE:RELIANCE", "5m", FROM, TO)) == 6


def test_get_candles_for_day_uses_the_day_timeframe() -> None:
    """Daily is stored directly, never resampled from intraday."""
    store, _, provider = make_store()
    store.get_candles("NSE:RELIANCE", "day", FROM, TO)
    assert provider.calls[0][1] == "day"


def test_get_candles_with_no_data_returns_canonical_empty() -> None:
    from providers.base import empty_frame

    store, _, _ = make_store(provider=FakeProvider(frame=empty_frame()))
    out = store.get_candles("NSE:RELIANCE", "15m", FROM, TO)
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]


def test_unsupported_timeframe_rejected() -> None:
    store, _, _ = make_store()
    with pytest.raises(Exception):
        store.get_candles("NSE:RELIANCE", "1m", FROM, TO)


# --- provider limits --------------------------------------------------------


def test_request_beyond_provider_history_is_clamped() -> None:
    """Asking for more than the provider serves would return an empty frame,
    which looks like 'no signals' instead of 'no data'."""
    store, _, provider = make_store()
    long_ago = datetime(2000, 1, 1, tzinfo=UTC)
    store.ensure_coverage("NSE:RELIANCE", "5m", long_ago, TO)
    assert provider.calls[0][2] > long_ago


def test_request_entirely_outside_provider_history_fetches_nothing() -> None:
    store, _, provider = make_store()
    store.ensure_coverage(
        "NSE:RELIANCE", "5m",
        datetime(1990, 1, 1, tzinfo=UTC), datetime(1990, 6, 1, tzinfo=UTC),
    )
    assert provider.calls == []
