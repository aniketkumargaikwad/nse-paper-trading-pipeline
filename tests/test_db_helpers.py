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
