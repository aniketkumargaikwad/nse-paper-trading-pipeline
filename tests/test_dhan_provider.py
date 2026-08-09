"""Tests for the Dhan adapter. No network: HTTP is faked."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.base import ProviderError  # noqa: E402
from providers.dhan import (  # noqa: E402
    MAX_DAYS_PER_REQUEST,
    DhanProvider,
    date_windows,
    parse_candle_payload,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def sample_payload() -> dict:
    """Dhan returns parallel ARRAYS, not a list of candle objects."""
    base = int(datetime(2026, 8, 3, 9, 15, tzinfo=IST).timestamp())
    return {
        "open":      [100.0, 101.0, 102.0],
        "high":      [105.0, 106.0, 107.0],
        "low":       [99.0, 100.0, 101.0],
        "close":     [104.0, 105.0, 106.0],
        "volume":    [1000, 2000, 3000],
        "timestamp": [base, base + 300, base + 600],
    }


# --- payload parsing --------------------------------------------------------


def test_parse_payload_builds_canonical_frame() -> None:
    df = parse_candle_payload(sample_payload())
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert len(df) == 3
    assert df.index[0] == datetime(2026, 8, 3, 9, 15, tzinfo=IST).astimezone(UTC)
    assert df["open"].iloc[0] == 100.0
    assert df["volume"].iloc[2] == 3000


def test_parse_payload_lands_inside_the_nse_session() -> None:
    """A 5h30m misread would silently shift every candle out of session."""
    df = parse_candle_payload(sample_payload())
    first_ist = df.index[0].tz_convert(IST)
    assert first_ist.hour == 9 and first_ist.minute == 15


def test_parse_empty_payload_gives_empty_frame() -> None:
    df = parse_candle_payload({"open": [], "high": [], "low": [],
                               "close": [], "volume": [], "timestamp": []})
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_parse_payload_missing_key_fails_loudly() -> None:
    payload = sample_payload()
    del payload["volume"]
    with pytest.raises(ProviderError, match="missing"):
        parse_candle_payload(payload)


def test_parse_payload_ragged_arrays_fail_loudly() -> None:
    """Refusing to guess how mismatched arrays align."""
    payload = sample_payload()
    payload["close"] = [1.0]
    with pytest.raises(ProviderError, match="same length"):
        parse_candle_payload(payload)


# --- request windowing ------------------------------------------------------


def test_short_range_is_one_window() -> None:
    assert date_windows(utc(2026, 1, 1), utc(2026, 2, 1)) == [
        (utc(2026, 1, 1), utc(2026, 2, 1))
    ]


def test_long_range_is_split_and_contiguous() -> None:
    windows = date_windows(utc(2024, 1, 1), utc(2026, 1, 1))
    assert len(windows) > 1
    assert windows[0][0] == utc(2024, 1, 1)
    assert windows[-1][1] == utc(2026, 1, 1)
    for (_, prev_end), (next_start, _) in zip(windows, windows[1:]):
        assert prev_end == next_start
    for start, end in windows:
        assert (end - start).days <= MAX_DAYS_PER_REQUEST


def test_inverted_range_rejected() -> None:
    with pytest.raises(ValueError):
        date_windows(utc(2026, 2, 1), utc(2026, 1, 1))


# --- fetch behaviour --------------------------------------------------------


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self._payload


class FakeHttp:
    """Records calls and returns queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return self.responses.pop(0)


def make_provider(http, token="tok") -> DhanProvider:
    class FakeTokens:
        def get_access_token(self):
            return token

    class FakeInstruments:
        def resolve(self, symbol):
            return ("2885", "NSE_EQ", "EQUITY")

    return DhanProvider(
        token_manager=FakeTokens(), instrument_resolver=FakeInstruments(),
        client_id="CID", http=http, sleep_fn=lambda s: None,
    )


def test_fetch_returns_canonical_candles() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    df = make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4))
    assert len(df) == 3
    assert http.calls[0]["json"]["securityId"] == "2885"
    assert http.calls[0]["json"]["interval"] == "5"


def test_fetch_pages_long_ranges() -> None:
    http = FakeHttp([FakeResponse(sample_payload()), FakeResponse(sample_payload())])
    # Exactly 180 days: the largest span two 90-day windows can cover without
    # exceeding MAX_DAYS_PER_REQUEST (Jan1->Jul1 is 181 days, one too many).
    make_provider(http).fetch(
        "NSE:RELIANCE", "5m", utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(days=180)
    )
    assert len(http.calls) == 2  # 180 days -> two 90-day windows


def test_daily_uses_the_historical_endpoint_and_sends_no_interval() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "day", utc(2026, 1, 1), utc(2026, 2, 1))
    assert http.calls[0]["url"].endswith("/charts/historical")
    assert "interval" not in http.calls[0]["json"]


def test_token_is_sent_but_never_the_client_secret() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    headers = http.calls[0]["headers"]
    assert headers["access-token"] == "tok"
    assert headers["client-id"] == "CID"


def test_server_error_retries_then_fails() -> None:
    http = FakeHttp([FakeResponse({}, 500)] * 4)
    with pytest.raises(ProviderError, match="after 4 attempts"):
        make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    assert len(http.calls) == 4


def test_client_error_is_not_retried() -> None:
    """A 400 will not improve on retry - fail fast with a clear message."""
    http = FakeHttp([FakeResponse({}, 400)])
    with pytest.raises(ProviderError, match="not a network issue"):
        make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    assert len(http.calls) == 1


def test_rate_limit_is_retried() -> None:
    http = FakeHttp([FakeResponse({}, 429), FakeResponse(sample_payload())])
    df = make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4))
    assert len(df) == 3
    assert len(http.calls) == 2


def test_derived_timeframe_is_refused() -> None:
    """15m is resampled from the 5m base and must never be fetched."""
    with pytest.raises(ProviderError, match="stored timeframes"):
        make_provider(FakeHttp([])).fetch(
            "NSE:RELIANCE", "15m", utc(2026, 8, 1), utc(2026, 8, 2)
        )


def test_max_history_days() -> None:
    p = make_provider(FakeHttp([]))
    assert p.max_history_days("5m") == 5 * 365
    assert p.max_history_days("day") > 5 * 365


def test_provider_satisfies_the_candle_provider_protocol() -> None:
    from providers.base import CandleProvider

    assert isinstance(make_provider(FakeHttp([])), CandleProvider)


def test_module_contains_no_order_endpoints() -> None:
    """Hard project rule: no code path may place a real order."""
    source = (Path(__file__).resolve().parent.parent / "providers" / "dhan.py").read_text()
    for forbidden in ("/orders", "placeOrder", "place_order", "cancelOrder", "modifyOrder"):
        assert forbidden not in source


def test_parser_handles_the_recorded_real_response() -> None:
    """Guards against the live API shape drifting away from our parser."""
    fixture = Path(__file__).resolve().parent / "fixtures" / "dhan_intraday_5m.json"
    if not fixture.exists():
        pytest.skip("run scripts/capture_dhan_fixture.py to record a fixture")
    df = parse_candle_payload(json.loads(fixture.read_text(encoding="utf-8")))
    assert not df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"
    assert df.index.is_monotonic_increasing
    # Every candle inside the NSE session, AND the first candle exactly at
    # the open. The second assertion is what actually catches a 5h30m shift:
    # a shifted fixture starts at 14:45 IST, not 09:15, at any trim length.
    ist = df.index.tz_convert(IST)
    assert (ist[0].hour, ist[0].minute) == (9, 15)
    assert ((ist.hour * 60 + ist.minute) >= 9 * 60 + 15).all()
    assert ((ist.hour * 60 + ist.minute) <= 15 * 60 + 30).all()


# --- security / edge cases --------------------------------------------------


def test_transport_error_never_carries_the_prepared_request() -> None:
    """A requests exception holds the PreparedRequest, whose body has the token."""
    import requests

    class BoomHttp:
        def post(self, *a, **k):
            prepared = requests.Request(
                "POST", "https://api.dhan.co/v2/x", json={"apiSecret": "LEAKME"}
            ).prepare()
            raise requests.exceptions.ConnectionError("boom", request=prepared)

    with pytest.raises(ProviderError) as excinfo:
        make_provider(BoomHttp()).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    assert "LEAKME" not in str(excinfo.value)


def test_request_dates_are_ist_calendar_dates() -> None:
    """A dropped .astimezone(IST) would silently shift request windows a day."""
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    assert http.calls[0]["json"]["fromDate"] == "2026-08-01"
    assert http.calls[0]["json"]["toDate"] == "2026-08-02"


def test_overlapping_windows_are_deduplicated() -> None:
    """Seam days are requested twice; the same candle must appear once."""
    http = FakeHttp([FakeResponse(sample_payload()), FakeResponse(sample_payload())])
    df = make_provider(http).fetch(
        "NSE:RELIANCE", "5m", utc(2026, 1, 1), utc(2026, 1, 1) + timedelta(days=180)
    )
    assert len(df) == 3
    assert df.index.is_monotonic_increasing


def test_all_empty_windows_give_a_canonical_empty_frame() -> None:
    empty = {"open": [], "high": [], "low": [], "close": [],
             "volume": [], "timestamp": []}
    http = FakeHttp([FakeResponse(empty)] * 3)
    df = make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 1, 1), utc(2026, 7, 1))
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert str(df.index.tz) == "UTC"


def test_non_json_success_is_not_retried() -> None:
    class BadJson(FakeResponse):
        def json(self):
            raise ValueError("not json")

    http = FakeHttp([BadJson(None)])
    with pytest.raises(ProviderError, match="non-JSON"):
        make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    assert len(http.calls) == 1


def test_only_the_two_chart_endpoints_are_reachable() -> None:
    """Stronger than a blacklist: pin the exact endpoint set."""
    source = (Path(__file__).resolve().parent.parent / "providers" / "dhan.py").read_text()
    assert set(re.findall(r"/charts/\w+", source)) == {"/charts/intraday", "/charts/historical"}
