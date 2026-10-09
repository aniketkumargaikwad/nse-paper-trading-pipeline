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
