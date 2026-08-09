"""Pure range arithmetic for the candle cache.

Coverage is modelled as ONE contiguous [first_ts, last_ts] range per
(instrument, timeframe). That deliberate simplification means a request
disjoint from existing coverage fetches from the existing boundary rather than
leaving an interior hole - slightly more data than strictly needed, in
exchange for coverage that is trivially verifiable. The candle_coverage table
enforces the same shape: one row per (instrument, timeframe), with a
first_ts <= last_ts CHECK.

The property that matters most: coverage is only ever extended over data
GENUINELY RETRIEVED. Overstating it would make every backtest built on that
range silently wrong while looking perfectly healthy.

Pure module: no I/O, no network, no database, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CoverageRange:
    """A contiguous cached span."""

    first_ts: datetime
    last_ts: datetime

    def __post_init__(self) -> None:
        # Mirrors the candle_coverage_range_ok CHECK constraint, so an
        # impossible range is caught here rather than by the database.
        if self.first_ts > self.last_ts:
            raise ValueError(
                f"CoverageRange first_ts ({self.first_ts}) must be <= "
                f"last_ts ({self.last_ts})"
            )


def missing_ranges(
    covered: CoverageRange | None, from_utc: datetime, to_utc: datetime
) -> list[tuple[datetime, datetime]]:
    """What must be fetched so [from_utc, to_utc] is fully cached.

    Returns the gaps in chronological order. An empty list means the request
    is already satisfied by the cache - which is what lets backtests run with
    no network at all.
    """
    if from_utc >= to_utc:
        raise ValueError(f"from ({from_utc}) must be before to ({to_utc})")

    if covered is None:
        return [(from_utc, to_utc)]

    gaps: list[tuple[datetime, datetime]] = []
    if from_utc < covered.first_ts:
        gaps.append((from_utc, covered.first_ts))
    if to_utc > covered.last_ts:
        gaps.append((covered.last_ts, to_utc))
    return gaps


def extend_coverage(
    covered: CoverageRange | None, fetched_from: datetime, fetched_to: datetime
) -> CoverageRange:
    """Widen coverage to include a genuinely retrieved span. Never shrinks.

    Callers MUST pass the range they actually stored - not the range they
    requested. If a fetch dies part-way, pass the timestamp of the last
    candle written, so coverage stops exactly there.
    """
    if covered is None:
        return CoverageRange(fetched_from, fetched_to)
    return CoverageRange(
        min(covered.first_ts, fetched_from),
        max(covered.last_ts, fetched_to),
    )
