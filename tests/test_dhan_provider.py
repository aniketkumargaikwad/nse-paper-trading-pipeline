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


def test_payload_with_extra_keys_is_accepted() -> None:
    """Dhan also returns open_interest; extra keys must not break parsing."""
    payload = sample_payload()
    payload["open_interest"] = [0, 0, 0]
    df = parse_candle_payload(payload)
    assert len(df) == 3
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


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

        def invalidate(self):
            pass

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


def test_headers_match_the_documented_chart_contract() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    headers = http.calls[0]["headers"]
    assert headers["access-token"] == "tok"
    assert headers["Content-Type"] == "application/json"
    assert headers["Accept"] == "application/json"
    # The chart endpoints take no client-id; that belongs to the auth API.
    assert "client-id" not in headers


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


def test_unsubscribed_error_explains_the_paid_subscription() -> None:
    """DH-902 means the account lacks the paid Data API entitlement - the
    message must say so rather than blaming a bad parameter."""
    http = FakeHttp([FakeResponse(
        {"errorType": "Invalid_Access", "errorCode": "DH-902",
         "errorMessage": "User has not subscribed to Data APIs"}, 401)])
    with pytest.raises(ProviderError, match="Data APIs"):
        make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))


def test_derived_timeframe_is_refused() -> None:
    """15m is resampled from the 5m base and must never be fetched."""
    with pytest.raises(ProviderError, match="stored timeframes"):
        make_provider(FakeHttp([])).fetch(
            "NSE:RELIANCE", "15m", utc(2026, 8, 1), utc(2026, 8, 2)
        )


def test_max_history_days() -> None:
    """Intraday depth is counted from the archive's fixed START DATE, not a
    rolling five years.

    This number is not cosmetic: CandleStore.ensure_coverage clamps every
    request to it. While it said 5 years, every intraday backfill was silently
    capped at five no matter what was asked for - and Dhan actually serves
    back to 2017-04-03.
    """
    from datetime import date

    from providers.dhan import INTRADAY_ARCHIVE_BEGINS

    p = make_provider(FakeHttp([]))
    expected = (date.today() - INTRADAY_ARCHIVE_BEGINS).days
    assert p.max_history_days("5m") >= expected
    assert p.max_history_days("1m") >= expected
    # Comfortably more than the five years everyone repeats.
    assert p.max_history_days("5m") > 6 * 365
    assert p.max_history_days("day") > p.max_history_days("5m")


def test_intraday_depth_grows_as_the_archive_recedes() -> None:
    """A fixed duration rots: each passing day puts one more day between now
    and a start date that does not move."""
    from datetime import date, timedelta

    from providers.dhan import INTRADAY_ARCHIVE_BEGINS

    today = (date.today() - INTRADAY_ARCHIVE_BEGINS).days
    next_year = (date.today() + timedelta(days=365) - INTRADAY_ARCHIVE_BEGINS).days
    assert next_year == today + 365


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


def test_intraday_dates_are_ist_datetimes() -> None:
    """Intraday windows are datetime-precise; a dropped .astimezone(IST)
    would silently shift the requested window."""
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 2))
    body = http.calls[0]["json"]
    assert body["fromDate"] == "2026-08-01 05:30:00"   # 00:00 UTC -> 05:30 IST
    assert body["toDate"] == "2026-08-02 05:30:00"
    assert body["oi"] is False
    assert "expiryCode" not in body


def test_daily_dates_are_date_only_with_expiry_code() -> None:
    http = FakeHttp([FakeResponse(sample_payload())])
    make_provider(http).fetch("NSE:RELIANCE", "day", utc(2026, 8, 1), utc(2026, 8, 2))
    body = http.calls[0]["json"]
    assert body["fromDate"] == "2026-08-01"
    assert body["toDate"] == "2026-08-02"
    assert body["expiryCode"] == 0
    assert body["oi"] is False
    assert "interval" not in body


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


# ---------------------------------------------------------------------------
# A 401 from a token that died before its stated expiry.
#
# Dhan has been observed rejecting a token hours early. get_access_token's
# clock check cannot see that, so without a 401-triggered refresh the same
# dead token is replayed until a human deletes the row — every symbol in a
# backfill failing identically, with no path to recovery.
# ---------------------------------------------------------------------------


class RotatingTokens:
    """Hands out a new token each time it is invalidated."""

    def __init__(self):
        self.tokens = ["stale", "fresh"]
        self.invalidated = 0

    def get_access_token(self):
        return self.tokens[0]

    def invalidate(self):
        self.invalidated += 1
        if len(self.tokens) > 1:
            self.tokens.pop(0)


def make_provider_with(tokens, http) -> DhanProvider:
    class FakeInstruments:
        def resolve(self, symbol):
            return ("2885", "NSE_EQ", "EQUITY")

    return DhanProvider(
        token_manager=tokens, instrument_resolver=FakeInstruments(),
        client_id="CID", http=http, sleep_fn=lambda s: None,
    )


def unauthorised() -> FakeResponse:
    return FakeResponse(
        {"errorCode": "DH-901",
         "errorMessage": "Client ID or user generated access token is invalid or expired."},
        status_code=401,
    )


def test_a_401_invalidates_the_token_and_retries_once() -> None:
    tokens = RotatingTokens()
    http = FakeHttp([unauthorised(), FakeResponse(sample_payload())])
    df = make_provider_with(tokens, http).fetch(
        "NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4)
    )
    assert len(df) == 3, "the retry should succeed on a fresh token"
    assert tokens.invalidated == 1
    assert http.calls[0]["headers"]["access-token"] == "stale"
    assert http.calls[1]["headers"]["access-token"] == "fresh", (
        "the retry must carry the NEW token, not replay the dead one"
    )


def test_a_second_401_gives_up_with_a_clear_error() -> None:
    """A fresh token still rejected is a real credentials or subscription
    problem. Retrying it only delays the message and burns TOTP codes."""
    tokens = RotatingTokens()
    http = FakeHttp([unauthorised(), unauthorised()])
    with pytest.raises(ProviderError) as exc:
        make_provider_with(tokens, http).fetch(
            "NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4)
        )
    assert "401" in str(exc.value)
    assert tokens.invalidated == 1, "exactly one refresh attempt, not a loop"
    assert len(http.calls) == 2


def test_other_4xx_still_does_not_trigger_a_token_refresh() -> None:
    tokens = RotatingTokens()
    http = FakeHttp([FakeResponse({"errorMessage": "bad parameter"}, status_code=400)])
    with pytest.raises(ProviderError):
        make_provider_with(tokens, http).fetch(
            "NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4)
        )
    assert tokens.invalidated == 0


def test_an_unsubscribed_401_does_not_burn_a_token_refresh() -> None:
    """DH-902 is a 401 too, but a fresh token cannot fix a lapsed subscription
    — refreshing would spend a TOTP code to reach the same error later."""
    tokens = RotatingTokens()
    http = FakeHttp([FakeResponse(
        {"errorCode": "DH-902",
         "errorMessage": "User has not subscribed to Data APIs"},
        status_code=401,
    )])
    with pytest.raises(ProviderError, match="Data APIs"):
        make_provider_with(tokens, http).fetch(
            "NSE:RELIANCE", "5m", utc(2026, 8, 1), utc(2026, 8, 4)
        )
    assert tokens.invalidated == 0
    assert len(http.calls) == 1


def test_daily_is_not_paged_into_ninety_day_chunks() -> None:
    """The 90-day limit is an INTRADAY limit. Dhan's daily endpoint returns
    twenty years in one call, and paging it anyway turned a 2-second request
    into 81 requests taking 73 seconds — an hour instead of two minutes across
    fifty symbols, for byte-identical candles."""
    from datetime import datetime, timedelta, timezone

    from providers.dhan import (
        MAX_DAYS_PER_DAILY_REQUEST,
        MAX_DAYS_PER_REQUEST,
        date_windows,
    )

    to = datetime(2026, 8, 21, tzinfo=timezone.utc)
    frm = to - timedelta(days=20 * 365)

    intraday_pages = date_windows(frm, to, MAX_DAYS_PER_REQUEST)
    daily_pages = date_windows(frm, to, MAX_DAYS_PER_DAILY_REQUEST)

    assert len(intraday_pages) > 50, "intraday must still page"
    assert len(daily_pages) == 1, "twenty years of daily should be one request"
