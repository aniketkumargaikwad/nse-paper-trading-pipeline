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
