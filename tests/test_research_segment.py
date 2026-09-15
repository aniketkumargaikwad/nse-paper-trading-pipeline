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
    min_trades_per_month,
    trades_fast_enough,
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


# --- how often it has to trade depends on what it is -------------------------


def test_each_segment_has_its_own_floor():
    assert min_trades_per_month(INTRADAY) == 10.0
    assert min_trades_per_month(SWING) == 4.0
    assert min_trades_per_month(LONG_TERM) == 1.0


def test_an_unclassifiable_combination_faces_the_strictest_floor():
    assert min_trades_per_month(UNKNOWN) == 10.0


def test_a_swing_strategy_is_judged_against_four_not_ten():
    """A flat floor of ten would have ruled every swing idea out."""
    swing = held(4, count=30)                       # 30 trades
    assert trades_fast_enough(swing, months=6) is True      # 5.0 a month
    assert trades_fast_enough(swing, months=10) is False    # 3.0 a month


def test_a_long_term_strategy_only_has_to_trade_monthly():
    slow = held(120, count=24)
    assert trades_fast_enough(slow, months=24) is True      # 1.0 a month
    assert trades_fast_enough(slow, months=48) is False     # 0.5 a month


def test_an_intraday_strategy_still_faces_ten():
    fast = held(0.25, count=60)
    assert trades_fast_enough(fast, months=6) is True       # 10.0 a month
    assert trades_fast_enough(fast, months=12) is False     # 5.0 a month


def test_nothing_to_measure_is_not_fast_enough():
    assert trades_fast_enough([], months=12) is False
    assert trades_fast_enough(held(1, count=5), months=0) is False
