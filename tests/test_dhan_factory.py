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
