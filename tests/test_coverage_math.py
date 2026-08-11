"""Tests for cache-coverage range arithmetic (pure)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from coverage_math import CoverageRange, extend_coverage, missing_ranges  # noqa: E402

UTC = timezone.utc


def d(day: int) -> datetime:
    return datetime(2026, 8, day, tzinfo=UTC)


# --- missing_ranges ---------------------------------------------------------


def test_no_existing_coverage_fetches_everything() -> None:
    assert missing_ranges(None, d(1), d(10)) == [(d(1), d(10))]


def test_fully_covered_request_fetches_nothing() -> None:
    assert missing_ranges(CoverageRange(d(1), d(10)), d(3), d(7)) == []


def test_request_matching_coverage_exactly_fetches_nothing() -> None:
    assert missing_ranges(CoverageRange(d(1), d(10)), d(1), d(10)) == []


def test_request_extending_after_fetches_only_the_tail() -> None:
    assert missing_ranges(CoverageRange(d(1), d(10)), d(5), d(15)) == [(d(10), d(15))]


def test_request_extending_before_fetches_only_the_head() -> None:
    assert missing_ranges(CoverageRange(d(10), d(20)), d(5), d(15)) == [(d(5), d(10))]


def test_request_extending_both_ends_fetches_both() -> None:
    assert missing_ranges(CoverageRange(d(10), d(15)), d(5), d(20)) == [
        (d(5), d(10)), (d(15), d(20))
    ]


def test_disjoint_later_request_fills_the_gap_to_stay_contiguous() -> None:
    """Coverage is ONE contiguous range, so a disjoint request fetches from
    the existing boundary rather than leaving an interior hole."""
    assert missing_ranges(CoverageRange(d(1), d(5)), d(10), d(12)) == [(d(5), d(12))]


def test_disjoint_earlier_request_fills_the_gap() -> None:
    assert missing_ranges(CoverageRange(d(10), d(15)), d(1), d(3)) == [(d(1), d(10))]


def test_request_touching_the_boundary_is_not_a_gap() -> None:
    assert missing_ranges(CoverageRange(d(1), d(5)), d(5), d(8)) == [(d(5), d(8))]


# --- extend_coverage --------------------------------------------------------


def test_extend_coverage_from_nothing() -> None:
    assert extend_coverage(None, d(1), d(5)) == CoverageRange(d(1), d(5))


def test_extend_coverage_widens_both_ends() -> None:
    assert extend_coverage(CoverageRange(d(5), d(10)), d(1), d(20)) == CoverageRange(
        d(1), d(20)
    )


def test_extend_coverage_never_shrinks() -> None:
    """A narrow successful fetch must not discard wider existing coverage."""
    assert extend_coverage(CoverageRange(d(1), d(20)), d(5), d(10)) == CoverageRange(
        d(1), d(20)
    )


def test_partial_fetch_records_only_what_was_retrieved() -> None:
    """THE central safety property: a failure part-way through must not
    advance coverage past the last candle actually stored."""
    # Asked for d(5)->d(20) but the provider died after d(12).
    assert extend_coverage(CoverageRange(d(1), d(5)), d(5), d(12)) == CoverageRange(
        d(1), d(12)
    )


def test_extend_is_idempotent() -> None:
    once = extend_coverage(CoverageRange(d(1), d(10)), d(1), d(10))
    twice = extend_coverage(once, d(1), d(10))
    assert once == twice == CoverageRange(d(1), d(10))


# --- validation -------------------------------------------------------------


def test_inverted_request_rejected() -> None:
    with pytest.raises(ValueError):
        missing_ranges(None, d(10), d(1))


def test_equal_bounds_request_rejected() -> None:
    """An empty request is a caller bug, not a no-op to swallow silently."""
    with pytest.raises(ValueError):
        missing_ranges(None, d(5), d(5))


def test_inverted_coverage_range_rejected() -> None:
    """Mirrors the candle_coverage_range_ok CHECK in the database."""
    with pytest.raises(ValueError):
        CoverageRange(d(10), d(1))


# --- round-trip property ----------------------------------------------------


def test_fetching_the_missing_ranges_makes_the_request_covered() -> None:
    """After fetching every gap it reports, the request must be fully covered.
    This is the invariant the candle store depends on."""
    covered = CoverageRange(d(10), d(15))
    request = (d(5), d(20))
    for gap_from, gap_to in missing_ranges(covered, *request):
        covered = extend_coverage(covered, gap_from, gap_to)
    assert missing_ranges(covered, *request) == []


def test_round_trip_from_no_coverage() -> None:
    covered = None
    request = (d(1), d(9))
    for gap_from, gap_to in missing_ranges(covered, *request):
        covered = extend_coverage(covered, gap_from, gap_to)
    assert missing_ranges(covered, *request) == []
