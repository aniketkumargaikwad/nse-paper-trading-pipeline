"""NIFTY, BANKNIFTY and India VIX minutes from Dhan (design §2.1)."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import index_minutes  # noqa: E402
from providers.dhan import DhanProvider  # noqa: E402
from test_dhan_provider import FakeHttp, FakeResponse  # noqa: E402

UTC = timezone.utc


def minute_payload(start_epoch: int, n: int) -> dict:
    ts = [start_epoch + 60 * i for i in range(n)]
    return {"open": [100.0 + i for i in range(n)], "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)], "close": [100.5 + i for i in range(n)],
            "volume": [0] * n, "timestamp": ts}


def provider(http) -> DhanProvider:
    class Tokens:
        def get_access_token(self):
            return "tok"

        def invalidate(self):
            pass

    return DhanProvider(token_manager=Tokens(), instrument_resolver=index_minutes.IndexResolver(),
                        client_id="CID", http=http, sleep_fn=lambda s: None)


def test_one_minute_candles_ask_dhan_for_interval_1_on_the_index_segment():
    # 2026-10-01 09:15 IST = 03:45 UTC
    http = FakeHttp([FakeResponse(minute_payload(1790826300, 3))])
    frame = provider(http).fetch_minutes("NIFTY", datetime(2026, 10, 1, tzinfo=UTC),
                                         datetime(2026, 10, 2, tzinfo=UTC))
    body = http.calls[0]["json"]
    assert (body["securityId"], body["exchangeSegment"], body["instrument"], body["interval"]) == (
        "13", "IDX_I", "INDEX", "1")
    assert len(frame) == 3


class FakeProvider:
    """Answers fetch_minutes with one 09:15 candle per requested IST day."""

    def __init__(self):
        self.asked = []

    def fetch_minutes(self, symbol, from_utc, to_utc):
        self.asked.append((symbol, from_utc, to_utc))
        days = pd.date_range(from_utc.astimezone(index_minutes.IST).date(),
                             to_utc.astimezone(index_minutes.IST).date(), freq="D")
        idx = pd.DatetimeIndex([pd.Timestamp(f"{d.date()} 09:15", tz=index_minutes.IST) for d in days]).tz_convert("UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}, index=idx)


def test_a_first_fill_starts_at_dhan_s_archive_and_is_stored_by_year(tmp_path):
    p = FakeProvider()
    n = index_minutes.fill(p, tmp_path, "NIFTY", today=date(2017, 12, 31), log=lambda *a, **k: None)
    assert p.asked[0][1].astimezone(index_minutes.IST).date() == index_minutes.ARCHIVE_BEGINS
    assert n == (date(2017, 12, 31) - index_minutes.ARCHIVE_BEGINS).days + 1
    assert index_minutes.path_for(tmp_path, "NIFTY", 2017).exists()
    assert index_minutes.last_stored(tmp_path, "NIFTY") == date(2017, 12, 31)


def test_a_later_fill_refetches_the_last_day_and_never_duplicates(tmp_path):
    p = FakeProvider()
    index_minutes.fill(p, tmp_path, "BANKNIFTY", today=date(2018, 1, 10), log=lambda *a, **k: None)
    p.asked.clear()
    index_minutes.fill(p, tmp_path, "BANKNIFTY", today=date(2018, 1, 12), log=lambda *a, **k: None)
    assert p.asked[0][1].astimezone(index_minutes.IST).date() == date(2018, 1, 10)
    got = index_minutes.read(tmp_path, "BANKNIFTY", date(2018, 1, 1), date(2018, 1, 31))
    assert len(got) == 12 and got["ts"].is_unique
    assert str(got["ts"].dt.tz) == "Asia/Kolkata"


def test_an_unknown_index_is_refused():
    import pytest
    from providers.base import ProviderError
    with pytest.raises(ProviderError, match="FINNIFTY"):
        index_minutes.IndexResolver().resolve("FINNIFTY")


def test_only_the_trading_session_is_kept(tmp_path):
    """Dhan's 2021-22 index days run 05:40-18:45 (785 candles): the previous close
    repeated before the open, the 09:00 pre-open auction, the close repeated after.
    Only 09:15-15:29 is the market."""
    stamps = ["05:40", "09:00", "09:14", "09:15", "12:00", "15:29", "15:30", "18:45"]
    idx = pd.DatetimeIndex([pd.Timestamp(f"2021-10-01 {s}", tz=index_minutes.IST) for s in stamps]).tz_convert("UTC")
    canonical = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}, index=idx)

    class Provider:
        def fetch_minutes(self, symbol, from_utc, to_utc):
            return canonical

    index_minutes.fill(Provider(), tmp_path, "NIFTY", today=date(2017, 4, 3), log=lambda *a, **k: None)
    got = index_minutes.read(tmp_path, "NIFTY", date(2021, 10, 1), date(2021, 10, 1))
    assert [t.strftime("%H:%M") for t in got["ts"]] == ["09:15", "12:00", "15:29"]


def test_off_minute_candles_and_non_session_days_are_dropped(tmp_path):
    """Aug 2021 carries candles at 09:15:30 and the like; Saturdays such as 8 Jan 2022
    carry NSE's mock-trading sessions. Neither is the market."""
    stamps = ["2022-01-07 09:15:00", "2022-01-07 09:15:30", "2022-01-08 09:15:00"]
    idx = pd.DatetimeIndex([pd.Timestamp(s, tz=index_minutes.IST) for s in stamps]).tz_convert("UTC")
    canonical = pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}, index=idx)

    class Provider:
        def fetch_minutes(self, symbol, from_utc, to_utc):
            return canonical

    index_minutes.fill(Provider(), tmp_path, "NIFTY", today=date(2017, 4, 3), sessions={date(2022, 1, 7)},
                       log=lambda *a, **k: None)
    got = index_minutes.read(tmp_path, "NIFTY", date(2022, 1, 1), date(2022, 1, 31))
    assert [t.strftime("%Y-%m-%d %H:%M:%S") for t in got["ts"]] == ["2022-01-07 09:15:00"]


class FakeUpstox:
    def __init__(self, frame):
        self.frame, self.asked = frame, []

    def index_minutes(self, name, start, end):
        self.asked.append((name, start, end))
        days = self.frame["ts"].dt.date
        return self.frame[(days >= start) & (days <= end)].reset_index(drop=True)


def test_the_upstox_provider_hands_fill_the_same_shape_as_dhan(tmp_path):
    ts = pd.to_datetime(["2026-10-16 09:15", "2026-10-16 09:16"]).tz_localize(index_minutes.IST)
    frame = pd.DataFrame({"ts": ts, "open": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.5, 1.5],
                          "close": [1.2, 2.2], "volume": [0, 0], "oi": [0, 0]})
    up = FakeUpstox(frame)
    provider = index_minutes.UpstoxIndexProvider(up)
    stored = index_minutes.fill(provider, tmp_path, "INDIAVIX", today=date(2026, 10, 16),
                                log=lambda *a, **k: None)
    assert stored == 2
    assert up.asked[0][0] == "INDIAVIX"
    back = index_minutes.read(tmp_path, "INDIAVIX", date(2026, 10, 16), date(2026, 10, 16))
    assert list(back.columns) == list(index_minutes.COLUMNS)
    assert back["ts"].dt.strftime("%H:%M").tolist() == ["09:15", "09:16"]
    assert str(back["ts"].dt.tz) == str(index_minutes.IST)
