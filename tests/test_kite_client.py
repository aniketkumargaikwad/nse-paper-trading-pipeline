"""Unit tests for kite_client.py — no network, no real Kite account.

Everything network-shaped is exercised through fakes: a fake Kite object for
retry/token behavior, and recorded-style candle payloads for conversion.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from kiteconnect.exceptions import InputException, NetworkException, TokenException

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kite_client import (  # noqa: E402
    KiteClientError,
    MarketDataClient,
    TokenExpiredError,
    candles_to_dataframe,
    chunk_date_range,
    drop_forming_candle,
    lookback_start_utc,
    split_instrument,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


# ---------------------------------------------------------------------------
# chunk_date_range
# ---------------------------------------------------------------------------


def test_chunk_range_single_chunk_when_short() -> None:
    chunks = chunk_date_range(utc(2026, 1, 1), utc(2026, 2, 1), max_days=200)
    assert chunks == [(utc(2026, 1, 1), utc(2026, 2, 1))]


def test_chunk_range_splits_and_is_contiguous() -> None:
    chunks = chunk_date_range(utc(2024, 1, 1), utc(2026, 1, 1), max_days=200)
    assert len(chunks) == 4  # 731 days -> 200+200+200+131
    assert chunks[0][0] == utc(2024, 1, 1)
    assert chunks[-1][1] == utc(2026, 1, 1)
    for (_, prev_end), (next_start, _) in zip(chunks, chunks[1:]):
        assert prev_end == next_start


def test_chunk_range_rejects_inverted_range() -> None:
    with pytest.raises(ValueError):
        chunk_date_range(utc(2026, 2, 1), utc(2026, 1, 1), max_days=200)


# ---------------------------------------------------------------------------
# candles_to_dataframe
# ---------------------------------------------------------------------------


def kite_candle(ist_dt: datetime, o=100.0, h=101.0, low=99.0, c=100.5, v=1000):
    """One candle dict shaped like kiteconnect's historical_data output."""
    return {
        "date": ist_dt, "open": o, "high": h, "low": low, "close": c, "volume": v,
    }


def test_candles_convert_ist_to_utc_and_sort() -> None:
    c1 = kite_candle(datetime(2026, 7, 17, 9, 30, tzinfo=IST))
    c2 = kite_candle(datetime(2026, 7, 17, 9, 15, tzinfo=IST))
    df = candles_to_dataframe([c1, c2, c1])  # unsorted + duplicate
    assert len(df) == 2  # duplicate dropped
    assert df.index[0] == utc(2026, 7, 17, 3, 45)  # 09:15 IST == 03:45 UTC
    assert str(df.index.tz) == "UTC"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_candles_empty_payload_gives_empty_frame() -> None:
    df = candles_to_dataframe([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_candles_missing_field_fails_loudly() -> None:
    bad = [{"date": datetime(2026, 7, 17, 9, 15, tzinfo=IST), "open": 1}]
    with pytest.raises(KiteClientError, match="missing field"):
        candles_to_dataframe(bad)


# ---------------------------------------------------------------------------
# drop_forming_candle — the no-look-ahead guarantee
# ---------------------------------------------------------------------------


def test_forming_15m_candle_is_dropped() -> None:
    candles = [
        kite_candle(datetime(2026, 7, 17, 10, 0, tzinfo=IST)),
        kite_candle(datetime(2026, 7, 17, 10, 15, tzinfo=IST)),  # still forming
    ]
    df = candles_to_dataframe(candles)
    # At 10:20 IST the 10:15 candle has not closed (closes 10:30).
    now = datetime(2026, 7, 17, 10, 20, tzinfo=IST).astimezone(UTC)
    out = drop_forming_candle(df, "15m", now)
    assert len(out) == 1
    assert out.index[-1] == datetime(2026, 7, 17, 10, 0, tzinfo=IST).astimezone(UTC)


def test_exactly_closed_15m_candle_is_kept() -> None:
    df = candles_to_dataframe([kite_candle(datetime(2026, 7, 17, 10, 15, tzinfo=IST))])
    now = datetime(2026, 7, 17, 10, 30, tzinfo=IST).astimezone(UTC)  # exact close
    assert len(drop_forming_candle(df, "15m", now)) == 1


def test_todays_day_candle_dropped_before_session_close() -> None:
    df = candles_to_dataframe([kite_candle(datetime(2026, 7, 17, 0, 0, tzinfo=IST))])
    before_close = datetime(2026, 7, 17, 14, 0, tzinfo=IST).astimezone(UTC)
    after_close = datetime(2026, 7, 17, 15, 30, tzinfo=IST).astimezone(UTC)
    assert drop_forming_candle(df, "day", before_close).empty
    assert len(drop_forming_candle(df, "day", after_close)) == 1


def test_drop_forming_requires_aware_now() -> None:
    df = candles_to_dataframe([kite_candle(datetime(2026, 7, 17, 10, 0, tzinfo=IST))])
    with pytest.raises(ValueError, match="timezone-aware"):
        drop_forming_candle(df, "15m", datetime(2026, 7, 17, 10, 30))


# ---------------------------------------------------------------------------
# lookback window
# ---------------------------------------------------------------------------


def test_lookback_covers_needed_candles_generously() -> None:
    now = utc(2026, 7, 17, 10, 0)
    start = lookback_start_utc("15m", 100, now)
    # 100 x 15m = 1500 trading minutes = 4 trading days -> must reach back
    # clearly further than 4 calendar days to survive weekends/holidays.
    assert (now - start) >= timedelta(days=9)
    assert (now - start) <= timedelta(days=30)  # but not absurdly far


# ---------------------------------------------------------------------------
# split_instrument
# ---------------------------------------------------------------------------


def test_split_instrument() -> None:
    assert split_instrument("NSE:RELIANCE") == ("NSE", "RELIANCE")
    for bad in ("RELIANCE", "NSE:", ":RELIANCE"):
        with pytest.raises(KiteClientError):
            split_instrument(bad)


# ---------------------------------------------------------------------------
# Retry / token behavior via a fake Kite
# ---------------------------------------------------------------------------


class FlakyThenOk:
    """Callable failing with `exc` N times, then returning `result`."""

    def __init__(self, failures: int, exc: Exception, result):
        self.remaining = failures
        self.exc = exc
        self.result = result
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise self.exc
        return self.result


def make_client() -> tuple[MarketDataClient, list[float]]:
    """Client with no store and a sleep spy instead of real sleeping."""
    sleeps: list[float] = []
    client = MarketDataClient(kite=object(), store=None, sleep_fn=sleeps.append)
    return client, sleeps


def test_transient_network_errors_are_retried_with_backoff() -> None:
    client, sleeps = make_client()
    fn = FlakyThenOk(2, NetworkException("gateway timeout"), result="data")
    assert client._call("testing", fn) == "data"
    assert fn.calls == 3
    assert sleeps == [1, 2]  # exponential: 1s then 2s


def test_persistent_network_error_fails_after_max_attempts() -> None:
    client, _ = make_client()
    fn = FlakyThenOk(99, NetworkException("down"), result=None)
    with pytest.raises(KiteClientError, match="Still failing after 4 attempts"):
        client._call("testing", fn)
    assert fn.calls == 4


def test_token_exception_is_never_retried() -> None:
    client, sleeps = make_client()
    fn = FlakyThenOk(99, TokenException("token expired"), result=None)
    with pytest.raises(TokenExpiredError, match="login.py"):
        client._call("testing", fn)
    assert fn.calls == 1  # immediate — retrying an expired token is pointless
    assert sleeps == []


def test_input_exception_is_never_retried() -> None:
    client, _ = make_client()
    fn = FlakyThenOk(99, InputException("bad interval"), result=None)
    with pytest.raises(KiteClientError, match="not a network issue"):
        client._call("testing", fn)
    assert fn.calls == 1


# ---------------------------------------------------------------------------
# Instrument resolution via a fake Kite + fake store
# ---------------------------------------------------------------------------


class FakeKite:
    def __init__(self):
        self.instrument_downloads = 0

    def instruments(self, exchange):
        self.instrument_downloads += 1
        assert exchange == "NSE"
        return [
            {"tradingsymbol": "RELIANCE", "instrument_token": 738561},
            {"tradingsymbol": "INFY", "instrument_token": 408065},
        ]


class FakeStore:
    def __init__(self, cached: dict[str, int]):
        self.cached = dict(cached)
        self.upserts: list[dict] = []

    def get_cached_instrument_tokens(self, instruments):
        return {k: v for k, v in self.cached.items() if k in instruments}

    def upsert_instrument_tokens(self, tokens, refreshed_on):
        self.upserts.append(dict(tokens))
        self.cached.update(tokens)


def test_cache_hit_avoids_instrument_download() -> None:
    kite, store = FakeKite(), FakeStore({"NSE:RELIANCE": 738561})
    client = MarketDataClient(kite, store, sleep_fn=lambda s: None)
    tokens = client.resolve_instrument_tokens(["NSE:RELIANCE"], today_ist=datetime(2026, 7, 17).date())
    assert tokens == {"NSE:RELIANCE": 738561}
    assert kite.instrument_downloads == 0  # never touched the network


def test_cache_miss_downloads_once_per_exchange_and_backfills_cache() -> None:
    kite, store = FakeKite(), FakeStore({})
    client = MarketDataClient(kite, store, sleep_fn=lambda s: None)
    tokens = client.resolve_instrument_tokens(
        ["NSE:RELIANCE", "NSE:INFY"], today_ist=datetime(2026, 7, 17).date()
    )
    assert tokens == {"NSE:RELIANCE": 738561, "NSE:INFY": 408065}
    assert kite.instrument_downloads == 1  # ONE dump for two symbols
    assert store.upserts == [{"NSE:RELIANCE": 738561, "NSE:INFY": 408065}]


def test_unknown_symbol_fails_with_actionable_message() -> None:
    kite, store = FakeKite(), FakeStore({})
    client = MarketDataClient(kite, store, sleep_fn=lambda s: None)
    with pytest.raises(KiteClientError, match="strategies.yaml"):
        client.resolve_instrument_tokens(["NSE:RELAINCE"], today_ist=datetime(2026, 7, 17).date())


# ---------------------------------------------------------------------------
# Timeframe capability guard
# ---------------------------------------------------------------------------


def test_kite_maps_cover_the_same_timeframes() -> None:
    """Guards the class of bug where a timeframe passes the guard and then
    KeyErrors on a lookup in a narrower map."""
    from kite_client import TIMEFRAME_MAX_DAYS_PER_REQUEST, TIMEFRAME_TO_KITE_INTERVAL

    assert set(TIMEFRAME_MAX_DAYS_PER_REQUEST) == set(TIMEFRAME_TO_KITE_INTERVAL)


def test_unsupported_timeframe_rejected_cleanly() -> None:
    """25m must raise a clear error, never a bare KeyError."""
    client, _ = make_client()
    with pytest.raises(KiteClientError, match="cannot serve"):
        client.fetch_historical_candles(
            1, "25m", utc(2026, 8, 1), utc(2026, 8, 2), now_utc=utc(2026, 8, 2)
        )
