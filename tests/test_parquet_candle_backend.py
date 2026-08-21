"""Parquet candle storage. Pure: local temp files, no network, no database.

The contract these pin is the one candle_store depends on, so a swap of
storage cannot quietly change behaviour above it.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parquet_candle_backend import ParquetCandleBackend  # noqa: E402

UTC = timezone.utc


class InnerSpy:
    """Stands in for the Supabase backend; records what was delegated."""

    def __init__(self):
        self.calls = []

    def instrument_id(self, symbol):
        self.calls.append(("instrument_id", symbol))
        return 42

    def read_coverage(self, instrument_id, timeframe):
        self.calls.append(("read_coverage", instrument_id, timeframe))
        return None

    def write_coverage(self, instrument_id, timeframe, coverage, source):
        self.calls.append(("write_coverage", instrument_id, timeframe, source))

    def write_quality_flags(self, rows):
        self.calls.append(("write_quality_flags", len(rows)))


def frame(start: datetime, n: int, *, step_min: int = 5, base: float = 100.0):
    idx = pd.DatetimeIndex([start + timedelta(minutes=step_min * i) for i in range(n)])
    return pd.DataFrame(
        {
            "open": [base + i for i in range(n)],
            "high": [base + i + 1 for i in range(n)],
            "low": [base + i - 1 for i in range(n)],
            "close": [base + i + 0.5 for i in range(n)],
            "volume": [1000 + i for i in range(n)],
        },
        index=idx,
    )


@pytest.fixture
def backend(tmp_path):
    return ParquetCandleBackend(InnerSpy(), str(tmp_path))


def test_a_round_trip_preserves_the_candles(backend):
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    original = frame(start, 100)
    backend.write_candles(42, "5m", original)

    back = backend.read_candles(42, "5m", start, start + timedelta(minutes=5 * 99))
    assert len(back) == 100
    pd.testing.assert_frame_equal(
        back.astype("float64"), original.astype("float64"),
        check_freq=False, check_names=False,
    )


def test_nothing_stored_returns_none_not_an_empty_frame(backend):
    """None means "no data at all"; empty means "a file exists but this range
    is empty". Collapsing them would let a gap read as absence."""
    start = datetime(2026, 3, 2, tzinfo=UTC)
    assert backend.read_candles(42, "5m", start, start + timedelta(days=1)) is None


def test_a_range_outside_the_stored_data_is_empty_not_none(backend):
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 10))
    far = datetime(2026, 3, 20, tzinfo=UTC)
    out = backend.read_candles(42, "5m", far, far + timedelta(days=1))
    assert out is not None and out.empty


def test_writing_twice_does_not_duplicate_candles(backend):
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 50))
    backend.write_candles(42, "5m", frame(start, 50))
    back = backend.read_candles(42, "5m", start, start + timedelta(days=1))
    assert len(back) == 50


def test_a_refetch_wins_over_the_stored_copy(backend):
    """A correction must be applicable. Keeping the older row would make one
    impossible to apply."""
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 5, base=100.0))
    backend.write_candles(42, "5m", frame(start, 5, base=900.0))
    back = backend.read_candles(42, "5m", start, start + timedelta(hours=1))
    assert len(back) == 5
    assert back["open"].iloc[0] == 900.0


def test_data_spanning_a_year_boundary_is_split_and_rejoined(backend):
    """Files are partitioned by year so a backfill rewrites a bounded file."""
    start = datetime(2025, 12, 31, 18, 0, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 200))     # crosses into 2026

    root = Path(backend._root)
    assert (root / "5m" / "42" / "2025.parquet").exists()
    assert (root / "5m" / "42" / "2026.parquet").exists()

    back = backend.read_candles(42, "5m", start, start + timedelta(minutes=5 * 199))
    assert len(back) == 200
    assert back.index.is_monotonic_increasing


def test_timeframes_are_stored_separately(backend):
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 10))
    backend.write_candles(42, "day", frame(start, 3, step_min=1440))
    assert len(backend.read_candles(42, "5m", start, start + timedelta(days=5))) == 10
    assert len(backend.read_candles(42, "day", start, start + timedelta(days=5))) == 3


def test_instruments_are_stored_separately(backend):
    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 10))
    backend.write_candles(99, "5m", frame(start, 4))
    assert len(backend.read_candles(42, "5m", start, start + timedelta(days=1))) == 10
    assert len(backend.read_candles(99, "5m", start, start + timedelta(days=1))) == 4


def test_an_empty_write_is_a_no_op(backend):
    backend.write_candles(42, "5m", pd.DataFrame())
    assert not list(Path(backend._root).rglob("*.parquet"))


def test_metadata_is_delegated_untouched(backend):
    """Coverage is the honesty ledger and stays transactional in Supabase."""
    backend.instrument_id("NSE:TCS")
    backend.read_coverage(42, "5m")
    backend.write_coverage(42, "5m", object(), "dhan")
    backend.write_quality_flags([{"a": 1}])
    kinds = [c[0] for c in backend._inner.calls]
    assert kinds == [
        "instrument_id", "read_coverage", "write_coverage", "write_quality_flags",
    ]


def test_it_satisfies_the_candle_backend_protocol():
    from candle_store import CandleBackend

    assert isinstance(ParquetCandleBackend(InnerSpy(), "/tmp/x"), CandleBackend)


# ---------------------------------------------------------------------------
# Reading must ASK which years exist, not probe a range.
#
# The first version iterated range(from.year, to.year + 1). A migration passes
# 2000-2100, so one read became 101 lookups - invisible on local disk, where a
# missing file fails instantly, and crippling over a network: 31.9s against
# Supabase Storage, versus 1.3s once it listed instead.
# ---------------------------------------------------------------------------


class CountingStore:
    """Wraps local files and counts reads, to prove misses are not attempted."""

    def __init__(self, root):
        from parquet_candle_backend import LocalFiles

        self._inner = LocalFiles()
        self.reads = 0

    def read(self, path):
        self.reads += 1
        return self._inner.read(path)

    def write(self, path, data):
        self._inner.write(path, data)

    def years(self, prefix):
        return self._inner.years(prefix)


def test_a_wide_range_does_not_probe_every_year(tmp_path):
    counting = CountingStore(str(tmp_path))
    backend = ParquetCandleBackend(InnerSpy(), str(tmp_path), store=counting)

    start = datetime(2026, 3, 2, 3, 45, tzinfo=UTC)
    backend.write_candles(42, "5m", frame(start, 20))
    counting.reads = 0

    wide_from = datetime(2000, 1, 1, tzinfo=UTC)
    wide_to = datetime(2100, 1, 1, tzinfo=UTC)
    got = backend.read_candles(42, "5m", wide_from, wide_to)

    assert len(got) == 20
    assert counting.reads == 1, (
        f"one stored year should mean one read, not {counting.reads} - a range "
        "scan would attempt 101"
    )


def test_only_the_requested_years_are_read(tmp_path):
    counting = CountingStore(str(tmp_path))
    backend = ParquetCandleBackend(InnerSpy(), str(tmp_path), store=counting)

    for year in (2024, 2025, 2026):
        backend.write_candles(
            42, "5m", frame(datetime(year, 3, 2, 3, 45, tzinfo=UTC), 5)
        )
    counting.reads = 0

    got = backend.read_candles(
        42, "5m",
        datetime(2025, 1, 1, tzinfo=UTC),
        datetime(2025, 12, 31, tzinfo=UTC),
    )
    assert len(got) == 5
    assert counting.reads == 1, "stored years outside the window must be skipped"


def test_years_are_parsed_from_filenames():
    from parquet_candle_backend import _years_from_names

    assert _years_from_names(["2024.parquet", "2026.parquet"]) == {2024, 2026}
    # Anything unexpected is ignored rather than crashing a read.
    assert _years_from_names(["notes.txt", "abc.parquet", "5m/42/2025.parquet"]) == {2025}
