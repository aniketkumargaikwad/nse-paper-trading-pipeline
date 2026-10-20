"""Upstox's expired-instrument API, read-only (design §2.1). No test touches the network."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import upstox  # noqa: E402

TOKEN = "secret-token-value"


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class Http:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses):
    http = Http(*responses)
    return upstox.UpstoxClient(TOKEN, http=http, sleep=lambda s: None), http


def test_expiries_are_dates_for_the_underlying_index():
    c, http = client(Resp({"status": "success", "data": ["2025-04-17", "2025-04-09"]}))
    assert c.expiries("NIFTY") == [date(2025, 4, 9), date(2025, 4, 17)]
    call = http.calls[0]
    assert call["url"].endswith("/v2/expired-instruments/expiries")
    assert call["params"] == {"instrument_key": "NSE_INDEX|Nifty 50"}
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_option_contracts_keep_calls_and_puts():
    data = [
        {"instrument_key": "NSE_FO|47983|17-04-2025", "expiry": "2025-04-17", "strike_price": 20400,
         "instrument_type": "PE", "lot_size": 75, "weekly": True},
        {"instrument_key": "NSE_FO|47984|17-04-2025", "expiry": "2025-04-17", "strike_price": 20400,
         "instrument_type": "CE", "lot_size": 75, "weekly": True},
        {"instrument_key": "NSE_FO|1|17-04-2025", "expiry": "2025-04-17", "strike_price": 0,
         "instrument_type": "FUT", "lot_size": 75, "weekly": False},
    ]
    c, http = client(Resp({"status": "success", "data": data}))
    got = c.option_contracts("NIFTY", date(2025, 4, 17))
    assert [(x.option_type, x.strike, x.lot_size, x.weekly) for x in got] == [
        ("PE", 20400.0, 75, True), ("CE", 20400.0, 75, True)]
    assert http.calls[0]["params"] == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2025-04-17"}


def test_minute_candles_ask_to_then_from_and_come_back_oldest_first_in_ist():
    candles = [["2025-04-17T09:16:00+05:30", 11, 12, 10, 11.5, 300, 9000],
               ["2025-04-17T09:15:00+05:30", 10, 11, 9, 10.5, 200, 8000]]
    c, http = client(Resp({"status": "success", "data": {"candles": candles}}))
    frame = c.minute_candles("NSE_FO|47983|17-04-2025", date(2025, 4, 3), date(2025, 4, 17))
    assert http.calls[0]["url"].endswith(
        "/v2/expired-instruments/historical-candle/NSE_FO%7C47983%7C17-04-2025/1minute/2025-04-17/2025-04-03")
    assert list(frame.columns) == list(upstox.CANDLE_COLUMNS)
    assert [t.strftime("%H:%M") for t in frame["ts"]] == ["09:15", "09:16"]
    assert str(frame["ts"].dt.tz) == "Asia/Kolkata"
    assert list(frame["oi"]) == [8000, 9000]


def test_a_long_range_is_asked_for_in_pieces():
    empty = Resp({"status": "success", "data": {"candles": []}})
    c, http = client(empty, empty)
    c.minute_candles("K", date(2025, 3, 1), date(2025, 4, 15))
    assert [call["url"].rsplit("/", 2)[-2:] for call in http.calls] == [
        ["2025-03-30", "2025-03-01"], ["2025-04-15", "2025-03-31"]]


def test_needing_upstox_plus_says_so():
    c, _ = client(Resp({"status": "error", "errors": [{"errorCode": "UDAPI1149",
                                                        "message": "Plus required"}]}, 403))
    with pytest.raises(upstox.UpstoxPlusRequired, match="Upstox Plus"):
        c.expiries("NIFTY")


def test_a_refused_token_names_the_fix_and_never_the_token():
    c, _ = client(Resp({"status": "error", "errors": [{"errorCode": "UDAPI100050"}]}, 401))
    with pytest.raises(upstox.UpstoxAuthError) as err:
        c.expiries("NIFTY")
    assert "FNO_SETUP" in str(err.value) and TOKEN not in str(err.value)


def test_rate_limits_and_server_errors_are_retried():
    ok = Resp({"status": "success", "data": ["2025-04-17"]})
    c, http = client(Resp({}, 429), Resp({}, 503), ok)
    assert c.expiries("NIFTY") == [date(2025, 4, 17)]
    assert len(http.calls) == 3


def test_the_token_comes_from_the_environment_or_a_clear_error():
    assert upstox.token_from_env({"UPSTOX_ANALYTICS_TOKEN": " t "}) == "t"
    with pytest.raises(upstox.UpstoxAuthError, match="UPSTOX_ANALYTICS_TOKEN"):
        upstox.token_from_env({})


def test_requests_are_paced_under_upstox_s_2000_per_30_minutes():
    assert upstox.PAUSE_SECONDS * 2000 >= 30 * 60


def test_a_rate_limit_is_waited_out_in_minutes_not_seconds():
    """14 Oct 2026: the first fill hit Upstox's 2,000-per-30-minutes limit; waits of
    4-16 s could not outlast it and the run stopped."""
    waits = []
    http = Http(Resp({}, 429), Resp({}, 429), Resp({"status": "success", "data": ["2025-04-17"]}))
    c = upstox.UpstoxClient(TOKEN, http=http, sleep=waits.append)
    assert c.expiries("NIFTY") == [date(2025, 4, 17)]
    assert waits == [upstox.PAUSE_SECONDS, *upstox.RATE_LIMIT_WAITS[:2]]
    assert upstox.RATE_LIMIT_WAITS[0] >= 60 and sum(upstox.RATE_LIMIT_WAITS) >= 30 * 60


def test_index_minutes_use_the_v3_candle_endpoint_and_come_back_oldest_first():
    rows = [["2026-10-16T09:16:00+05:30", 23050.0, 23060.0, 23040.0, 23055.0, 0, 0],
            ["2026-10-16T09:15:00+05:30", 23065.1, 23076.55, 23017.45, 23050.25, 0, 0]]
    c, http = client(Resp({"status": "success", "data": {"candles": rows}}))
    frame = c.index_minutes("INDIAVIX", date(2026, 10, 16), date(2026, 10, 16))
    assert http.calls[0]["url"] == ("https://api.upstox.com/v3/historical-candle/"
                                    "NSE_INDEX%7CIndia%20VIX/minutes/1/2026-10-16/2026-10-16")
    assert [t.strftime("%H:%M") for t in frame["ts"]] == ["09:15", "09:16"]
    assert frame["open"].iloc[0] == 23065.1


def test_index_minutes_ask_at_most_thirty_days_at_a_time():
    empty = Resp({"status": "success", "data": {"candles": []}})
    c, http = client(empty, empty, empty)
    frame = c.index_minutes("NIFTY", date(2026, 1, 1), date(2026, 3, 2))
    assert len(http.calls) == 3 and frame.empty
    assert http.calls[0]["url"].endswith("/minutes/1/2026-01-30/2026-01-01")
