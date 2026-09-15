"""Which kind of trading a strategy actually does, and the return being aimed at.

The segment is MEASURED from the trades, not taken from what the strategy says
about itself. A state machine that calls itself intraday but holds for three
weeks is a swing strategy, and the holding period is what decides the fees it
pays, the risk it carries overnight and whether the owner can run it at all.

WHAT THE TOOL SUPPORTS
----------------------
intraday, swing and long-term are the same cash-equity machinery with
different holding periods, so all three are testable today. FUTURES AND
OPTIONS ARE NOT: the candle store holds NSE cash equities only, with no
expiries, strikes, lot sizes or margin, so an F&O strategy cannot be tested
here at all. `UNSUPPORTED` names that rather than letting a caller assume.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from typing import Any

# A position closed inside its own session. NSE trades for about six and a
# quarter hours, so anything under a day was squared off.
INTRADAY_DAYS = 1.0
# Beyond this a position is being held for a trend, not a swing.
SWING_DAYS = 20.0

INTRADAY = "intraday"
SWING = "swing"
LONG_TERM = "long-term"
UNKNOWN = "no trades"

# Not a classification - a refusal. See the module docstring.
UNSUPPORTED = ("f&o", "futures", "options")

# What the owner is aiming for, as a monthly compounded return. Ambitious:
# 4% a month compounds to 60% a year and 7% to 125%, which is far above what
# most professional funds sustain. It is a target to steer towards and to
# measure the distance from, NOT a filter - filtering on it would simply
# produce empty days.
TARGET_MONTHLY_MIN = 4.0
TARGET_MONTHLY_MAX = 7.0

# A strategy that trades less often than this is not worth considering: the
# owner wants an active system, and a handful of trades over eight years
# cannot be judged, however good the total looks.
MIN_TRADES_PER_MONTH = 10.0


def holding_days(trades: Sequence[Any]) -> float | None:
    """The MEDIAN holding period in days.

    Median rather than mean: one position held for three years among fifty
    day trades would drag an average into "long-term" and describe neither.
    """
    spans = [
        (t.exit_fill_ts - t.entry_fill_ts).total_seconds() / 86400.0
        for t in trades
        if getattr(t, "exit_fill_ts", None) and getattr(t, "entry_fill_ts", None)
    ]
    return statistics.median(spans) if spans else None


def classify(trades: Sequence[Any]) -> str:
    """Which segment these trades belong to, measured from how long they were held."""
    held = holding_days(trades)
    if held is None:
        return UNKNOWN
    if held < INTRADAY_DAYS:
        return INTRADAY
    if held <= SWING_DAYS:
        return SWING
    return LONG_TERM


def describe(segment: str, held_days: float | None) -> str:
    """The segment with its holding period, for someone reading a message."""
    if segment == UNKNOWN or held_days is None:
        return UNKNOWN
    if segment == INTRADAY:
        return "intraday (closed same day)"
    if segment == SWING:
        return f"swing (held ~{held_days:.0f} days)"
    return f"long-term (held ~{held_days / 30.44:.0f} months)"


def meets_target(monthly_pct: float | None) -> str:
    """How a monthly return compares with what the owner is aiming for."""
    if monthly_pct is None:
        return "not measured"
    if monthly_pct < TARGET_MONTHLY_MIN:
        return f"below the {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}% target"
    if monthly_pct > TARGET_MONTHLY_MAX:
        return f"above the {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}% target"
    return f"IN the {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}% target"
