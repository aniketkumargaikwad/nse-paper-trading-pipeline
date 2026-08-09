"""Tests for assembling the Dhan stack and the instrument resolver."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dhan_factory import SupabaseInstrumentResolver  # noqa: E402
from instruments import InstrumentError  # noqa: E402


class FakeQuery:
    def __init__(self, client, rows):
        self.client = client
        self.rows = rows

    def select(self, *a, **kw): return self
    def eq(self, *a, **kw): return self
    def limit(self, *a, **kw): return self

    def execute(self):
        self.client.queries += 1
        return type("R", (), {"data": self.rows})()


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.queries = 0

    def table(self, name):
        return FakeQuery(self, self.rows)


def test_resolver_returns_dhan_identifiers() -> None:
    client = FakeClient([{
        "dhan_security_id": "2885", "dhan_segment": "NSE_EQ",
        "instrument_type": "EQUITY",
    }])
    assert SupabaseInstrumentResolver(client).resolve("NSE:RELIANCE") == (
        "2885", "NSE_EQ", "EQUITY"
    )


def test_resolver_caches_so_repeated_lookups_do_not_re_query() -> None:
    """A backfill resolves the same symbol once per window otherwise."""
    client = FakeClient([{
        "dhan_security_id": "2885", "dhan_segment": "NSE_EQ",
        "instrument_type": "EQUITY",
    }])
    resolver = SupabaseInstrumentResolver(client)
    resolver.resolve("NSE:RELIANCE")
    resolver.resolve("NSE:RELIANCE")
    assert client.queries == 1


def test_unknown_symbol_says_how_to_fix_it() -> None:
    with pytest.raises(InstrumentError, match="refresh-instruments"):
        SupabaseInstrumentResolver(FakeClient([])).resolve("NSE:NOSUCH")


def test_malformed_symbol_rejected_before_querying() -> None:
    """Validate the shape first - do not waste a round trip on 'reliance'."""
    client = FakeClient([])
    with pytest.raises(InstrumentError):
        SupabaseInstrumentResolver(client).resolve("reliance")
    assert client.queries == 0


def test_ids_are_returned_as_strings() -> None:
    """Dhan's API expects string identifiers even though the column is text."""
    client = FakeClient([{
        "dhan_security_id": 2885, "dhan_segment": "NSE_EQ",
        "instrument_type": "EQUITY",
    }])
    security_id, _, _ = SupabaseInstrumentResolver(client).resolve("NSE:RELIANCE")
    assert security_id == "2885"
    assert isinstance(security_id, str)


def test_dhan_provider_requires_a_supabase_connection() -> None:
    """Dhan caches candles in Supabase, so --no-db cannot work with it."""
    from config import Settings
    from data_provider import create_data_client

    settings = Settings(
        supabase_url="u", supabase_service_role_key="k", data_provider="dhan"
    )
    with pytest.raises(RuntimeError, match="Supabase"):
        create_data_client(settings, None, __import__("datetime").date.today())


def test_describe_provider_mentions_dhan_without_leaking_keys() -> None:
    from config import Settings
    from data_provider import describe_provider

    settings = Settings(
        supabase_url="u", supabase_service_role_key="SECRETKEY",
        data_provider="dhan", kite_api_key="KITEKEY",
    )
    text = describe_provider(settings)
    assert "dhan" in text.lower()
    assert "SECRETKEY" not in text
    assert "KITEKEY" not in text


def test_adapter_presents_the_market_data_client_interface() -> None:
    """The engines call this interface; dhan must satisfy it."""
    from dhan_factory import CandleStoreDataClient

    class FakeStore:
        def get_candles(self, *a, **kw): ...

    client = CandleStoreDataClient(FakeStore())
    for method in ("resolve_instrument_tokens", "fetch_historical_candles",
                   "max_history_days"):
        assert callable(getattr(client, method)), f"missing {method}"
    assert client.requires_daily_login is False


def test_symbols_are_their_own_handles() -> None:
    from dhan_factory import CandleStoreDataClient
    import datetime as _dt

    client = CandleStoreDataClient(object())
    out = client.resolve_instrument_tokens(
        ["NSE:RELIANCE", "NSE:TCS", "NSE:RELIANCE"], _dt.date.today()
    )
    assert out == {"NSE:RELIANCE": "NSE:RELIANCE", "NSE:TCS": "NSE:TCS"}


def test_adapter_drops_the_forming_candle_by_default() -> None:
    """The hard rule: decisions are made on CLOSED candles only."""
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo

    from dhan_factory import CandleStoreDataClient

    IST = ZoneInfo("Asia/Kolkata")
    UTCZ = timezone.utc
    start = datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    idx = pd.DatetimeIndex(
        [(start + timedelta(minutes=15 * i)).astimezone(UTCZ) for i in range(3)],
        name="ts",
    )
    frame = pd.DataFrame(
        {"open": np.full(3, 100.0), "high": np.full(3, 101.0),
         "low": np.full(3, 99.0), "close": np.full(3, 100.5),
         "volume": np.full(3, 10.0)},
        index=idx,
    )

    class FakeStore:
        def get_candles(self, symbol, timeframe, from_utc, to_utc):
            return frame

    client = CandleStoreDataClient(FakeStore())
    # 09:50 IST: the 09:45 candle has not closed yet.
    now = datetime(2026, 8, 3, 9, 50, tzinfo=IST).astimezone(UTCZ)
    closed = client.fetch_historical_candles(
        "NSE:RELIANCE", "15m", idx[0], now, closed_only=True, now_utc=now
    )
    assert len(closed) == 2
    raw = client.fetch_historical_candles(
        "NSE:RELIANCE", "15m", idx[0], now, closed_only=False, now_utc=now
    )
    assert len(raw) == 3
