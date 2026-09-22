"""What a day is steered towards, and what it is no longer swept across."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from research.checker import PLACEHOLDER_TIMEFRAME  # noqa: E402
from research.directions import (  # noqa: E402
    DIRECTIONS,
    OUT_OF_REACH,
    direction_block,
    direction_for,
)
from research.facts import FACTS  # noqa: E402
from research.prompts import propose_prompt  # noqa: E402
from research.universe import (  # noqa: E402
    STOCK_TIMEFRAMES,
    SWEEP_TIMEFRAMES,
    lowest_timeframe,
    research_combos,
)


def test_a_day_sweeps_hourly_and_daily_only():
    assert SWEEP_TIMEFRAMES == ("60m", "day")
    assert all(tf in STOCK_TIMEFRAMES for tf in SWEEP_TIMEFRAMES)


def test_the_full_grid_is_still_reachable_for_a_one_off_run():
    combos = research_combos(["NSE:A"], include_indexes=False,
                             stock_timeframes=STOCK_TIMEFRAMES)
    assert len(combos) == 6


def test_the_checker_validates_against_the_lowest_bar_the_sweep_runs():
    """Otherwise a 15m-reading rule passes the check and skips every combination."""
    assert PLACEHOLDER_TIMEFRAME == "60m"
    assert lowest_timeframe(("day", "60m")) == "60m"
    assert lowest_timeframe(STOCK_TIMEFRAMES) == "5m"


def test_lowest_timeframe_refuses_a_set_it_does_not_know():
    with pytest.raises(ValueError, match="no known stock timeframe"):
        lowest_timeframe(("4h",))


def test_directions_rotate_by_day_and_by_idea():
    first = date(2026, 9, 22)
    assert direction_for(first, 1) != direction_for(first, 2)
    assert direction_for(first, 1) != direction_for(date(2026, 9, 23), 1)
    # A full turn of the rotation comes back to where it started.
    assert direction_for(first, 1) == direction_for(first, 1 + len(DIRECTIONS))


def test_no_direction_names_something_the_engine_cannot_express():
    """The whole point: an assigned direction must be buildable today."""
    for name in OUT_OF_REACH:
        assert all(d.name != name for d in DIRECTIONS)


def test_the_out_of_reach_list_says_what_each_one_would_take():
    assert OUT_OF_REACH
    assert all(requirement.strip() for requirement in OUT_OF_REACH.values())


def test_the_direction_sits_above_the_evidence_not_below_it():
    block = direction_block(DIRECTIONS[0])
    prompt = propose_prompt(formats=[], notes=[], ideas_tried=["an old idea"],
                            direction=block)
    assert block in prompt
    assert prompt.index(block) < prompt.index(FACTS)


def test_a_later_version_of_an_idea_gets_no_direction():
    prompt = propose_prompt(formats=[], notes=[], ideas_tried=[])
    assert "TODAY'S DIRECTION" not in prompt


def test_the_rules_no_longer_promise_sub_hour_bars():
    prompt = propose_prompt(formats=[], notes=[], ideas_tried=[])
    assert "5m, 15m, 25m, 30m, 60m and day" not in prompt
    assert "60m and day bars" in prompt
