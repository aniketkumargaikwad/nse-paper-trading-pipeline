"""The options strategy format (design §3): what parses, and what is refused with a reason."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.strategy import StrategyError, parse  # noqa: E402

# The design's own example, verbatim apart from the comments.
SPEC_EXAMPLE = """
version: options/1
name: weekly-20-delta-strangle
underlying: NIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry:
  window: {from: "09:30", to: "10:00"}
  when: "vix > 11 and vix < 16 and not event_today and dte <= 4"
legs:
  - {side: sell, type: CE, strike: {delta: 0.20}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.20}, lots: 1}
exit:
  target_pct: 50
  stop_pct: 100
  time: "15:15"
  days_before_expiry: 0
  when: "vix_chg_pct > 15"
reentry: {max_per_day: 1}
"""

POSITIONAL = """
version: options/1
name: monthly-condor
underlying: BANKNIFTY
mode: positional
expiry: {cycle: monthly, which: 0}
entry:
  when: "expiry_dte >= 25 and iv_rank > 50"
legs:
  - {side: sell, type: CE, strike: {delta: 0.2}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.2}, lots: 1}
  - {side: buy, type: CE, strike: {from_leg: 0, offset: 500}, lots: 1}
  - {side: buy, type: PE, strike: {from_leg: 1, offset: -500}, lots: 1}
exit: {target_pct: 50, stop_pct: 200, days_before_expiry: 2}
sizing: {max_margin_pct: 60}
"""


def test_the_design_example_parses():
    s = parse(SPEC_EXAMPLE)
    assert (s.name, s.underlying, s.mode, s.cycle, s.which) == (
        "weekly-20-delta-strangle", "NIFTY", "intraday", "weekly", 0)
    assert s.window == ("09:30", "10:00")
    assert (s.legs[0].rule, s.legs[0].value, s.legs[0].sign) == ("delta", 0.2, -1)
    assert s.legs[1].value == -0.2
    assert (s.exit.target_pct, s.exit.stop_pct, s.exit.time) == (50.0, 100.0, "15:15")
    assert s.exit.when is not None and s.entry_when is not None
    assert (s.max_per_day, s.max_margin_pct) == (1, 100.0)


def test_a_positional_condor_parses():
    s = parse(POSITIONAL)
    assert s.mode == "positional" and s.window is None and s.cycle == "monthly"
    assert (s.legs[2].rule, s.legs[2].value) == ("from_leg", (0, 500.0))
    assert (s.legs[3].rule, s.legs[3].value, s.legs[3].sign) == ("from_leg", (1, -500.0), 1)
    assert s.exit.days_before_expiry == 2
    assert s.max_margin_pct == 60.0


@pytest.mark.parametrize("old,new,match", [
    ("vix_chg_pct > 15", "vix_change > 15", "unknown name 'vix_change'"),
    ("{delta: -0.20}", "{delta: 0.20}", "a put's delta is negative"),
    ("version: options/1", "version: options/2", "version must be options/1"),
    ("{delta: 0.20}, lots: 1}", "{delta: 0.20, atm: 0}, lots: 1}", "exactly one of"),
    ("underlying: NIFTY", "underlying: FINNIFTY", "daily data only"),
    ('time: "15:15"', "time: 15:15", "HH:MM"),
    ("{delta: 0.20}, lots: 1}", "{delta: 0.20}, lots: 0}", "lots must be"),
    ("stop_pct: 100", "stop_pc: 100", "unknown key"),
    ("mode: intraday", "mode: scalping", "mode must be"),
])
def test_refusals_say_what_to_change(old, new, match):
    text = SPEC_EXAMPLE.replace(old, new, 1)
    assert text != SPEC_EXAMPLE
    with pytest.raises(StrategyError, match=match):
        parse(text)


def test_from_leg_must_point_to_an_earlier_leg():
    with pytest.raises(StrategyError, match="EARLIER leg"):
        parse(POSITIONAL.replace("{from_leg: 0, offset: 500}", "{from_leg: 2, offset: 500}"))


def test_a_window_is_refused_on_a_positional_strategy():
    text = POSITIONAL.replace("entry:\n", 'entry:\n  window: {from: "09:30", to: "10:00"}\n')
    with pytest.raises(StrategyError, match="window is for intraday"):
        parse(text)


def test_dotted_names_are_refused_on_daily_bars():
    with pytest.raises(StrategyError, match="unknown name 'prev_day.close'"):
        parse(POSITIONAL.replace("iv_rank > 50", "prev_day.close > 1"))


def test_indicators_on_the_index_are_allowed():
    s = parse(POSITIONAL.replace("iv_rank > 50", "close > sma(200) and rsi(14) < 70"))
    assert s.entry_when is not None


def test_not_yaml_is_refused():
    with pytest.raises(StrategyError, match="YAML"):
        parse("legs: [unclosed")


def test_the_day_so_far_can_be_named_in_an_intraday_strategy():
    s = parse(SPEC_EXAMPLE.replace("dte <= 4", "close > or_high and or_low > 0 and day_open > 0"))
    assert s.entry_when is not None
