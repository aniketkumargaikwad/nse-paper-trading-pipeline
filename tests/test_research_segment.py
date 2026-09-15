"""Which kind of trading a strategy does, measured from its own trades."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.segment import (  # noqa: E402
    INTRADAY,
    LONG_TERM,
    SWING,
    UNKNOWN,
    classify,
    describe,
    holding_days,
    meets_target,
)
from research_helpers import ist, trade  # noqa: E402


def held(days: float, *, count: int = 3):
    """`count` trades, each held for `days`."""
    start = ist(2025, 1, 2, 10)
    return [
        trade(entry=start + timedelta(days=7 * i),
              exit_=start + timedelta(days=7 * i + days))
        for i in range(count)
    ]


def test_a_position_closed_the_same_day_is_intraday():
    assert classify(held(0.25)) == INTRADAY


def test_a_position_held_a_few_days_is_swing():
    assert classify(held(4)) == SWING


def test_a_position_held_for_months_is_long_term():
    assert classify(held(120)) == LONG_TERM


def test_the_boundary_days_fall_the_documented_way():
    assert classify(held(0.99)) == INTRADAY
    assert classify(held(1.0)) == SWING          # no longer inside one session
    assert classify(held(20)) == SWING
    assert classify(held(21)) == LONG_TERM


def test_one_long_hold_among_many_day_trades_does_not_rename_the_strategy():
    """The median resists it; a mean would call fifty day trades long-term."""
    trades = held(0.25, count=50) + held(1000, count=1)
    assert classify(trades) == INTRADAY


def test_no_trades_is_said_plainly_rather_than_guessed():
    assert classify([]) == UNKNOWN
    assert holding_days([]) is None


def test_the_description_carries_the_holding_period():
    assert describe(INTRADAY, 0.25) == "intraday (closed same day)"
    assert describe(SWING, 4.0) == "swing (held ~4 days)"
    assert describe(LONG_TERM, 121.76) == "long-term (held ~4 months)"
    assert describe(UNKNOWN, None) == UNKNOWN


def test_the_monthly_target_says_which_side_of_the_band_it_is_on():
    assert meets_target(5.0).startswith("IN the 4-7% target")
    assert meets_target(4.0).startswith("IN the")
    assert meets_target(7.0).startswith("IN the")
    assert meets_target(1.2).startswith("below")
    assert meets_target(12.0).startswith("above")
    assert meets_target(None) == "not measured"
