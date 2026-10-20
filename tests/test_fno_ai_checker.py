"""The options research checker: forces the research rules, refuses what the data cannot test."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.ai.checker import CheckError, check_proposal, research_name  # noqa: E402

DAY = date(2026, 10, 21)

INTRADAY = """
version: options/1
name: anything
underlying: BANKNIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry:
  window: {from: "09:20", to: "09:30"}
  when: "vix < 16"
legs:
  - {side: sell, type: CE, strike: {atm: 0}, lots: 1}
  - {side: sell, type: PE, strike: {atm: 0}, lots: 1}
exit: {stop_pct: 30, target_pct: 50, time: "15:15"}
"""

POSITIONAL = """
version: options/1
name: x
underlying: NIFTY
mode: positional
expiry: {cycle: weekly, which: 0}
legs:
  - {side: sell, type: CE, strike: {delta: 0.2}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.2}, lots: 1}
exit: {stop_pct: 100, target_pct: 50}
"""


def test_the_name_says_where_it_came_from():
    assert research_name(DAY, "Calm-day straddle!", 2) == "O-20261021-calm-day-straddle-v2"


def test_a_proposal_is_tested_on_both_indexes_with_its_own_rules():
    checked = check_proposal(INTRADAY, idea="calm straddle", day=DAY, version=1)
    assert checked.mode == "intraday"
    assert checked.document["name"] == "O-20261021-calm-straddle-v1"
    nifty, bank = checked.for_index("NIFTY"), checked.for_index("BANKNIFTY")
    assert (nifty.underlying, bank.underlying) == ("NIFTY", "BANKNIFTY")
    assert nifty.window == ("09:20", "09:30") and nifty.exit.stop_pct == 30


@pytest.mark.parametrize("text,mode,margin", [(INTRADAY, "intraday", 100.0), (POSITIONAL, "positional", 25.0)])
def test_missing_sizing_takes_the_atlas_convention(text, mode, margin):
    checked = check_proposal(text, idea="i", day=DAY, version=1)
    assert checked.for_index("NIFTY").max_margin_pct == margin


def test_the_ais_own_sizing_is_kept():
    checked = check_proposal(POSITIONAL + "sizing: {max_margin_pct: 40}\n", idea="i", day=DAY, version=1)
    assert checked.for_index("NIFTY").max_margin_pct == 40.0


def test_a_parse_refusal_is_sent_back_with_the_parsers_reason():
    with pytest.raises(CheckError, match="unknown name 'vix_change'"):
        check_proposal(INTRADAY.replace("vix < 16", "vix_change < 16"), idea="i", day=DAY, version=1)


def test_intraday_strikes_beyond_the_minute_band_are_refused():
    text = INTRADAY.replace("{atm: 0}, lots: 1}\n  - {side: sell, type: PE",
                            "{otm_pct: 7}, lots: 1}\n  - {side: sell, type: PE")
    with pytest.raises(CheckError, match="within 5%"):
        check_proposal(text, idea="i", day=DAY, version=1)


def test_a_positional_strike_far_out_is_fine():
    text = POSITIONAL.replace("{delta: 0.2}", "{otm_pct: 7}")
    assert check_proposal(text, idea="i", day=DAY, version=1).mode == "positional"


def test_intraday_expiries_past_the_next_one_are_refused():
    with pytest.raises(CheckError, match="which"):
        check_proposal(INTRADAY.replace("which: 0", "which: 2"), idea="i", day=DAY, version=1)


def test_not_a_mapping_is_refused():
    with pytest.raises(CheckError, match="mapping"):
        check_proposal("- just\n- a list\n", idea="i", day=DAY, version=1)
