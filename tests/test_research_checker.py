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


# ---- A daily-only sweep (research.universe.SWEEP_TIMEFRAMES, 25 Sep 2026) ----
#
# Every daily candle is stamped at midnight IST, so each of these would run
# without an error and quietly mean something else. They go back to Opus.

DAILY = ("day",)


def machine(*, side="long", when="close > sma(20)", session=None):
    doc = {
        "version": 3, "name": "x", "timeframe": "60m", "initial": "flat",
        "states": [
            {"name": "flat", "transitions": [
                {"when": when, "enter": {"side": side}, "goto": "holding"}]},
            {"name": "holding", "transitions": [
                {"when": "close < sma(20)", "exit": {"reason": "signal"}, "goto": "flat"}]},
        ],
        "risk": {"stop_loss": {"type": "percent", "value": 2.0},
                 "target": {"type": "percent", "value": 4.0}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
    }
    if session:
        doc["session"] = session
    return yaml.safe_dump(doc)


def test_a_daily_sweep_checks_the_proposal_on_daily_bars():
    assert checked(timeframes=DAILY).document["timeframe"] == "day"


def test_the_default_still_checks_on_the_lowest_stock_timeframe():
    """research.atlas and research.evaluate keep the whole whitelist."""
    assert checked().document["timeframe"] == "5m"


def test_a_daily_sweep_accepts_a_long_machine_reading_daily_values():
    got = checked(machine(when="daily.close > daily.sma(200) and prev_day.low < low"),
                  timeframes=DAILY)
    assert got.strategy.position_type == "long"


def test_a_daily_sweep_refuses_a_v2_short_even_with_a_square_off():
    """square_off compares 00:00 with 15:10 on a daily bar, and never fires."""
    text = (GOOD.replace("position_type: long", "position_type: short")
            + 'session:\n  square_off: "15:10"\n')
    with pytest.raises(CheckError, match="LONG only"):
        checked(text, timeframes=DAILY)


def test_a_daily_sweep_says_long_only_not_add_a_square_off():
    """Asking for a square-off would send Opus to add one, which is refused too."""
    text = GOOD.replace("position_type: long", "position_type: short")
    with pytest.raises(CheckError, match="LONG only") as err:
        checked(text, timeframes=DAILY)
    assert "square_off, e.g." not in str(err.value)


def test_a_daily_sweep_refuses_a_v3_short():
    """The old square-off rule only read v2's position_type; v3 declares a side per entry."""
    with pytest.raises(CheckError, match="LONG only"):
        checked(machine(side="short"), timeframes=DAILY)


@pytest.mark.parametrize("key", ["no_entry_before", "no_entry_after", "square_off"])
def test_a_daily_sweep_refuses_any_session_time(key):
    """no_entry_before 09:30 against a midnight stamp would block every single entry."""
    value = {"no_entry_before": "09:30", "no_entry_after": "15:00", "square_off": "15:10"}[key]
    with pytest.raises(CheckError, match=f"session.{key}"):
        checked(machine(session={key: value}), timeframes=DAILY)


def test_a_daily_sweep_refuses_an_hourly_reference_in_a_machine():
    """Aggregating daily bars to 'hourly' makes one bucket per day: the daily
    close under another name, with no error anywhere."""
    with pytest.raises(CheckError, match=r"hourly\."):
        checked(machine(when="close > hourly.ema(20)"), timeframes=DAILY)


def test_a_daily_sweep_refuses_a_lower_timeframe_operand_in_v2():
    text = GOOD.replace(
        "compare_to: {indicator: sma, params: {period: 50}}\nexit:",
        "compare_to: {indicator: sma, params: {period: 50}, timeframe: 60m}\nexit:",
    )
    with pytest.raises(CheckError, match="LOWER"):
        checked(text, timeframes=DAILY)


def test_the_word_hourly_in_a_description_is_not_a_reference():
    """Only rules count - a strategy may say 'not hourly' in its description."""
    doc = yaml.safe_load(machine())
    doc["description"] = "avoids hourly.noise entirely"
    assert checked(yaml.safe_dump(doc), timeframes=DAILY).strategy.position_type == "long"


def test_an_intraday_sweep_keeps_the_old_short_rule():
    """Re-opening intraday bars with --stock-timeframes must not start refusing
    what those bars can test."""
    text = (GOOD.replace("position_type: long", "position_type: short")
            + 'session:\n  square_off: "15:10"\n')
    got = checked(text, timeframes=("60m", "day"))
    assert got.document["timeframe"] == "60m"
    assert got.document["position_type"] == "short"
