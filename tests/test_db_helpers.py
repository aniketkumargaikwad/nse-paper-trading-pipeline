"""Unit tests for the pure (no-network) parts of db.py.

Connectivity itself is exercised by `python db.py` against a real Supabase
project; these tests cover the conversion logic that must be correct for
idempotency and timezone safety.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import OpenPosition, _iso, _parse_ts  # noqa: E402


def test_iso_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="naive datetime"):
        _iso(datetime(2026, 7, 17, 9, 15))  # no tzinfo — must be refused


def test_iso_normalizes_to_utc() -> None:
    # 09:15 IST == 03:45 UTC.
    from zoneinfo import ZoneInfo

    ist = datetime(2026, 7, 17, 9, 15, tzinfo=ZoneInfo("Asia/Kolkata"))
    assert _iso(ist) == "2026-07-17T03:45:00+00:00"


def test_parse_ts_handles_offset_and_z_suffix() -> None:
    a = _parse_ts("2026-07-17T03:45:00+00:00")
    b = _parse_ts("2026-07-17T03:45:00Z")
    c = _parse_ts("2026-07-17T09:15:00+05:30")  # same instant, IST offset
    assert a == b == c
    assert a.tzinfo is not None
    assert a.utcoffset().total_seconds() == 0  # normalized to UTC


def test_open_position_from_row_parses_types() -> None:
    row = {
        "id": "3f2a7a3e-0000-0000-0000-000000000000",
        "strategy_name": "TF-EMA-RSI-15m-v1",
        "instrument": "NSE:RELIANCE",
        "position_type": "long",
        "quantity": "1",  # PostgREST may hand back strings for numerics
        "entry_signal_candle_ts": "2026-07-17T04:30:00+00:00",
        "entry_fill_ts": "2026-07-17T04:45:00+00:00",
        "intended_entry_price": "2911.0000",
        "entry_price": "2912.4555",
        "stop_loss_price": "2892.0683",
        "target_price": "2956.1423",
    }
    pos = OpenPosition.from_row(row)
    assert pos.quantity == 1
    assert pos.entry_price == pytest.approx(2912.4555)
    assert pos.entry_signal_candle_ts == datetime(2026, 7, 17, 4, 30, tzinfo=timezone.utc)
    # Fill must be one candle AFTER the signal candle close (next-open fills).
    assert pos.entry_fill_ts > pos.entry_signal_candle_ts


# ---------------------------------------------------------------------------
# Inserting against a database that is one migration behind
# ---------------------------------------------------------------------------


class _FakeTable:
    """Rejects columns the 'database' does not have, one per attempt.

    PostgREST reports only the first unknown column it meets, so a caller that
    handles a single rejection would still lose the batch when two columns are
    missing. Naming two here is what makes the retry loop meaningful.
    """

    def __init__(self, unknown: set[str]) -> None:
        self.unknown = unknown
        self.attempts: list[list[dict]] = []
        self.stored: list[dict] | None = None

    def insert(self, payload):
        self.attempts.append([dict(r) for r in payload])
        self._payload = payload
        return self

    def execute(self):
        from postgrest.exceptions import APIError

        for row in self._payload:
            for column in row:
                if column in self.unknown:
                    raise APIError({
                        "code": "PGRST204",
                        "message": (
                            f"Could not find the '{column}' column of "
                            "'backtest_results' in the schema cache"
                        ),
                    })
        self.stored = [dict(r) for r in self._payload]
        return self


class _FakeClient:
    def __init__(self, table: _FakeTable) -> None:
        self._t = table

    def table(self, name: str):
        return self._t


def test_insert_drops_columns_the_database_does_not_have_yet(capsys) -> None:
    """A run must survive an unapplied migration, minus the new columns.

    Losing a fifty-symbol backtest — minutes of fetching and simulation —
    because sql/006 has not been pasted into the SQL editor is the expensive
    failure. The old rows are still worth storing.
    """
    from db import SupabaseStore

    table = _FakeTable(unknown={"sharpe_daily", "capital_base"})
    store = SupabaseStore(_FakeClient(table))

    count = store.insert_backtest_results([
        {"instrument": "NSE:TCS", "net_pnl": 10.0,
         "sharpe_daily": 1.2, "capital_base": 100000.0},
    ])

    assert count == 1
    # First attempt full, then one column dropped, then the other.
    assert len(table.attempts) == 3
    assert table.stored == [{"instrument": "NSE:TCS", "net_pnl": 10.0}]
    assert "sharpe_daily" in capsys.readouterr().out


def test_insert_still_raises_for_errors_that_are_not_missing_columns() -> None:
    from postgrest.exceptions import APIError

    from db import DatabaseError, SupabaseStore

    class Broken(_FakeTable):
        def execute(self):
            raise APIError({"code": "42501", "message": "permission denied"})

    store = SupabaseStore(_FakeClient(Broken(unknown=set())))
    with pytest.raises(DatabaseError, match="permission denied"):
        store.insert_backtest_results([{"instrument": "NSE:TCS"}])


def test_missing_table_is_recognised_in_both_postgrest_wordings() -> None:
    """PostgREST has two ways of saying a table is not there.

    The PGRST205 wording is the one a fresh migration actually produces, and
    a guard that only knew the Postgres wording let it abort a finished
    backtest — the exact failure the guard was written to stop.
    """
    from postgrest.exceptions import APIError

    from db import _is_missing_table

    pgrst205 = APIError({
        "code": "PGRST205",
        "message": "Could not find the table 'public.backtest_equity' in the schema cache",
    })
    relation = APIError({
        "code": "42P01",
        "message": 'relation "public.backtest_equity" does not exist',
    })
    unrelated = APIError({"code": "42501", "message": "permission denied"})

    assert _is_missing_table(pgrst205)
    assert _is_missing_table(relation)
    assert not _is_missing_table(unrelated)


def test_equity_insert_returns_zero_when_the_table_is_absent(capsys) -> None:
    from postgrest.exceptions import APIError

    from db import SupabaseStore

    class Absent(_FakeTable):
        def execute(self):
            raise APIError({
                "code": "PGRST205",
                "message": (
                    "Could not find the table 'public.backtest_equity' in the "
                    "schema cache"
                ),
            })

    store = SupabaseStore(_FakeClient(Absent(unknown=set())))
    assert store.insert_backtest_equity([{"day": "2026-08-01", "equity": 1.0}]) == 0
    assert "backtest_equity" in capsys.readouterr().out
