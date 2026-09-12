"""Research rules, applied to whatever Opus proposes (design 5.6)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.checker import CheckError, check_proposal, research_name  # noqa: E402

GOOD = """
name: whatever-opus-called-it
position_type: long
timeframe: 60m
instruments: [NSE:RELIANCE]
entry:
  all:
    - indicator: close
      operator: ">"
      compare_to: {indicator: sma, params: {period: 50}}
exit:
  any:
    - indicator: close
      operator: "<"
      compare_to: {indicator: sma, params: {period: 50}}
risk:
  stop_loss: {type: atr, period: 14, multiplier: 2.0}
  target: {type: atr, period: 14, multiplier: 4.0}
sizing: {type: fixed_quantity, quantity: 1}
max_cycles_per_day: 2
"""


def checked(text=GOOD, **kwargs):
    return check_proposal(text, idea="dip buyer", day=date(2026, 9, 12), version=1, **kwargs)


def test_sizing_is_forced_to_one_lakh_notional():
    """A fixed share count would rank symbols by share price, not by signal."""
    assert checked().document["sizing"] == {"type": "notional", "notional_per_trade": 100000}


def test_the_strategy_is_never_enabled():
    assert checked().document["enabled"] is False


def test_symbols_and_timeframe_are_replaced_because_the_sweep_supplies_them():
    doc = checked().document
    assert doc["instruments"] == ["NSE:RELIANCE"]
    assert doc["timeframe"] == "5m"


def test_the_name_is_generated_not_taken_from_the_proposal():
    assert checked().document["name"] == "R-20260912-dip-buyer-v1"


def test_research_name_slugifies_awkward_titles():
    assert research_name(date(2026, 9, 12), "RSI(2) + Volume!! spike", 3) == \
        "R-20260912-rsi-2-volume-spike-v3"


def test_a_short_strategy_must_square_off():
    text = GOOD.replace("position_type: long", "position_type: short")
    with pytest.raises(CheckError, match="square_off"):
        check_proposal(text, idea="x", day=date(2026, 9, 12), version=1)


def test_a_short_strategy_with_square_off_is_accepted():
    text = (GOOD.replace("position_type: long", "position_type: short")
            + 'session:\n  square_off: "15:10"\n')
    got = check_proposal(text, idea="x", day=date(2026, 9, 12), version=1)
    assert got.document["position_type"] == "short"


def test_broken_yaml_is_a_check_error_naming_the_problem():
    with pytest.raises(CheckError, match="YAML"):
        check_proposal("entry: [unclosed", idea="x", day=date(2026, 9, 12), version=1)


def test_an_invalid_strategy_reports_the_parser_message():
    text = GOOD.replace('operator: ">"', 'operator: "≥"')
    with pytest.raises(CheckError, match="operator"):
        check_proposal(text, idea="x", day=date(2026, 9, 12), version=1)


def test_a_v3_machine_is_accepted_and_still_gets_the_research_rules():
    text = yaml.safe_dump({
        "version": 3, "name": "x", "timeframe": "60m", "universe": "NIFTY50",
        "initial": "flat", "states": [
            {"name": "flat", "transitions": [
                {"when": "close > sma(20)", "enter": {"side": "long"}, "goto": "holding"}]},
            {"name": "holding", "transitions": [
                {"when": "close < sma(20)", "exit": {"reason": "signal"}, "goto": "flat"}]},
        ],
        "risk": {"stop_loss": {"type": "percent", "value": 2.0},
                 "target": {"type": "percent", "value": 4.0}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
        "max_cycles_per_day": 2,
    })
    got = check_proposal(text, idea="machine", day=date(2026, 9, 12), version=2)
    assert got.document["sizing"]["notional_per_trade"] == 100000
    assert got.document["name"] == "R-20260912-machine-v2"
    assert got.strategy.position_type == "long"
