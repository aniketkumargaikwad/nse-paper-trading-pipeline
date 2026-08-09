"""Tests for the Supabase candle backend's row shaping (no network)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_math import CoverageRange  # noqa: E402
from dhan_auth import StoredToken  # noqa: E402
from supabase_candle_backend import (  # noqa: E402
    SupabaseCandleBackend,
    candles_to_rows,
    coverage_to_row,
    rows_to_frame,
)

UTC = timezone.utc


def frame(count: int = 2) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime(2026, 8, 3, 4, 0, tzinfo=UTC) + timedelta(minutes=5 * i)
         for i in range(count)],
        name="ts",
    )
    return pd.DataFrame(
        {"open": np.full(count, 100.0), "high": np.full(count, 101.0),
         "low": np.full(count, 99.0), "close": np.full(count, 100.5),
         "volume": np.full(count, 1234.0)},
        index=index,
    )


# --- pure row shaping -------------------------------------------------------


def test_candles_to_rows_shape() -> None:
    rows = candles_to_rows(instrument_id=7, timeframe="5m", df=frame(2))
    assert len(rows) == 2
    row = rows[0]
    assert row["instrument_id"] == 7
    assert row["timeframe"] == "5m"
    assert row["ts"].endswith("+00:00")
    assert row["open"] == 100.0
    assert row["volume"] == 1234          # stored as an integer
    assert isinstance(row["volume"], int)


def test_candles_to_rows_keys_match_the_table_columns() -> None:
    row = candles_to_rows(7, "5m", frame(1))[0]
    assert set(row) == {
        "instrument_id", "timeframe", "ts", "open", "high", "low", "close", "volume"
    }


def test_rows_to_frame_roundtrip() -> None:
    rows = candles_to_rows(instrument_id=7, timeframe="5m", df=frame(3))
    out = rows_to_frame(rows)
    assert len(out) == 3
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert str(out.index.tz) == "UTC"
    assert out.index.is_monotonic_increasing
    assert out["close"].iloc[0] == 100.5


def test_rows_to_frame_of_nothing_is_canonical_empty() -> None:
    out = rows_to_frame([])
    assert out.empty
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert str(out.index.tz) == "UTC"


def test_rows_to_frame_sorts_and_dedupes() -> None:
    rows = candles_to_rows(7, "5m", frame(2))
    out = rows_to_frame([rows[1], rows[0], rows[1]])
    assert len(out) == 2
    assert out.index.is_monotonic_increasing


def test_coverage_to_row_shape() -> None:
    row = coverage_to_row(
        instrument_id=7, timeframe="5m",
        coverage=CoverageRange(datetime(2026, 8, 1, tzinfo=UTC),
                               datetime(2026, 8, 5, tzinfo=UTC)),
        source="dhan",
    )
    assert row["instrument_id"] == 7
    assert row["source"] == "dhan"
    assert row["first_ts"].endswith("+00:00")
    assert row["last_ts"].endswith("+00:00")
    assert "last_refreshed_at" in row


def test_naive_timestamps_rejected() -> None:
    """A naive datetime read as the wrong zone is the classic trading bug."""
    df = frame(1)
    df.index = df.index.tz_localize(None)
    with pytest.raises(ValueError, match="timezone-aware"):
        candles_to_rows(instrument_id=1, timeframe="5m", df=df)


def test_naive_coverage_timestamps_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        coverage_to_row(
            1, "5m",
            CoverageRange(datetime(2026, 8, 1), datetime(2026, 8, 5)),
            "dhan",
        )


# --- backend behaviour against a fake client --------------------------------


class FakeQuery:
    """Records the query chain and returns a canned payload."""

    def __init__(self, client, table):
        self.client = client
        self.table_name = table
        self.filters: dict = {}
        self.payload = None
        self.op = None

    def select(self, *a, **kw): self.op = "select"; return self
    def insert(self, rows): self.op = "insert"; self.payload = rows; return self
    def upsert(self, rows, on_conflict=None):
        self.op = "upsert"; self.payload = rows
        self.filters["on_conflict"] = on_conflict
        return self
    def eq(self, col, val): self.filters[col] = val; return self
    def gte(self, col, val): self.filters[f"{col}__gte"] = val; return self
    def lte(self, col, val): self.filters[f"{col}__lte"] = val; return self
    def order(self, *a, **kw): return self
    def limit(self, n): return self
    def range(self, a, b): self.filters["range"] = (a, b); return self

    def execute(self):
        self.client.calls.append(
            {"table": self.table_name, "op": self.op,
             "payload": self.payload, "filters": dict(self.filters)}
        )
        data = self.client.responses.get(self.table_name, [])
        if self.op in ("insert", "upsert"):
            return type("R", (), {"data": self.payload})()
        # Only return rows on the first page; later pages come back empty so
        # the paging loop terminates.
        key = (self.table_name, self.filters.get("range"))
        if self.filters.get("range") and self.filters["range"][0] > 0:
            return type("R", (), {"data": []})()
        return type("R", (), {"data": data})()


class FakeClient:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[dict] = []

    def table(self, name):
        return FakeQuery(self, name)


def test_instrument_id_resolves_and_caches() -> None:
    client = FakeClient({"instruments": [{"id": 42}]})
    backend = SupabaseCandleBackend(client)
    assert backend.instrument_id("NSE:RELIANCE") == 42
    assert backend.instrument_id("NSE:RELIANCE") == 42
    selects = [c for c in client.calls if c["table"] == "instruments"]
    assert len(selects) == 1, "second lookup should hit the memo, not the database"


def test_unknown_instrument_says_how_to_fix_it() -> None:
    backend = SupabaseCandleBackend(FakeClient({"instruments": []}))
    with pytest.raises(Exception, match="refresh-instruments"):
        backend.instrument_id("NSE:NOSUCH")


def test_write_candles_upserts_on_the_composite_key() -> None:
    client = FakeClient()
    SupabaseCandleBackend(client).write_candles(7, "5m", frame(3))
    call = [c for c in client.calls if c["table"] == "candles"][0]
    assert call["op"] == "upsert"
    assert call["filters"]["on_conflict"] == "instrument_id,timeframe,ts"
    assert len(call["payload"]) == 3


def test_write_candles_chunks_large_frames() -> None:
    client = FakeClient()
    SupabaseCandleBackend(client).write_candles(7, "5m", frame(2500))
    calls = [c for c in client.calls if c["table"] == "candles"]
    assert len(calls) == 3          # 1000 + 1000 + 500
    assert sum(len(c["payload"]) for c in calls) == 2500


def test_write_empty_frame_makes_no_call() -> None:
    client = FakeClient()
    SupabaseCandleBackend(client).write_candles(7, "5m", frame(0))
    assert [c for c in client.calls if c["table"] == "candles"] == []


def test_read_coverage_parses_into_a_coverage_range() -> None:
    client = FakeClient({"candle_coverage": [
        {"first_ts": "2026-08-01T00:00:00+00:00", "last_ts": "2026-08-05T00:00:00+00:00"}
    ]})
    cov = SupabaseCandleBackend(client).read_coverage(7, "5m")
    assert cov == CoverageRange(
        datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 5, tzinfo=UTC)
    )


def test_read_coverage_of_nothing_is_none() -> None:
    assert SupabaseCandleBackend(FakeClient({"candle_coverage": []})).read_coverage(7, "5m") is None


def test_write_quality_flags_of_nothing_makes_no_call() -> None:
    client = FakeClient()
    SupabaseCandleBackend(client).write_quality_flags([])
    assert client.calls == []


def test_quality_flags_upsert_so_redetection_is_idempotent() -> None:
    """The table has UNIQUE(instrument_id, timeframe, flag_type, ts); repeated
    detection must update, not pile up duplicates."""
    client = FakeClient()
    SupabaseCandleBackend(client).write_quality_flags(
        [{"instrument_id": 1, "timeframe": "5m", "flag_type": "session_gap",
          "ts": "2026-08-03T03:45:00+00:00", "detail": {}}]
    )
    call = client.calls[0]
    assert call["op"] == "upsert"
    assert call["filters"]["on_conflict"] == "instrument_id,timeframe,flag_type,ts"


# --- token store ------------------------------------------------------------


def test_token_roundtrip() -> None:
    client = FakeClient({"provider_tokens": [
        {"access_token": "tok", "expires_at": "2026-08-03T12:00:00+00:00"}
    ]})
    backend = SupabaseCandleBackend(client)
    token = backend.get_token("dhan")
    assert token.access_token == "tok"
    assert token.expires_at == datetime(2026, 8, 3, 12, tzinfo=UTC)


def test_missing_token_is_none() -> None:
    assert SupabaseCandleBackend(FakeClient({"provider_tokens": []})).get_token("dhan") is None


def test_save_token_upserts_on_provider() -> None:
    client = FakeClient()
    SupabaseCandleBackend(client).save_token(
        "dhan", StoredToken("secret-token", datetime(2026, 8, 3, 12, tzinfo=UTC))
    )
    call = client.calls[0]
    assert call["op"] == "upsert"
    assert call["filters"]["on_conflict"] == "provider"


def test_backend_satisfies_both_protocols() -> None:
    backend = SupabaseCandleBackend(FakeClient())
    for method in ("instrument_id", "read_candles", "write_candles",
                   "read_coverage", "write_coverage", "write_quality_flags",
                   "get_token", "save_token"):
        assert callable(getattr(backend, method)), f"missing {method}"


def test_module_never_interpolates_a_token_into_a_message() -> None:
    """provider_tokens holds a live credential."""
    source = (Path(__file__).resolve().parent.parent / "supabase_candle_backend.py").read_text()
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if "access_token" in stripped and ("raise" in stripped or "print(" in stripped):
            raise AssertionError(f"token may leak into a message: {stripped}")
