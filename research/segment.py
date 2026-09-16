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

# What the owner is aiming for, as a monthly return. Ambitious: 5% a month
# compounds to 80% a year and 7% to 125%, which is far above what most
# professional funds sustain. It is a target to steer towards and to measure
# the distance from, NOT a filter - filtering on it would simply produce empty
# days. The floor was 4% until 2026-09-16, when the owner restated it as 5%.
TARGET_MONTHLY_MIN = 5.0
TARGET_MONTHLY_MAX = 7.0

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


# How often a strategy must trade to be worth considering, PER SEGMENT. One
# flat number cannot work: ten trades a month on a single symbol is a trade
# every two days, which only an intraday or fast-swing system does, so a flat
# floor would quietly rule long-term out of the research entirely.
#
# The floors are what the owner chose: an active intraday system, a swing
# system that turns over weekly, and a long-term one that still has to do
# something twelve times a year rather than sit in one position for a decade.
MIN_TRADES_PER_MONTH: dict[str, float] = {
    INTRADAY: 10.0,
    SWING: 4.0,
    LONG_TERM: 1.0,
}


def min_trades_per_month(segment: str) -> float:
    """The floor this segment has to clear. An unclassifiable one clears nothing."""
    return MIN_TRADES_PER_MONTH.get(segment, MIN_TRADES_PER_MONTH[INTRADAY])


def trades_fast_enough(trades: Sequence[Any], months: float) -> bool:
    """Did this combination trade often enough for the segment it turned out to be?"""
    if months <= 0 or not trades:
        return False
    return len(trades) / months >= min_trades_per_month(classify(trades))


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
