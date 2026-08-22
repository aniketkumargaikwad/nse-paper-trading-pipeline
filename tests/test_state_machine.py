"""The v3 state machine: parsing a document, and running it over candles.

WHY A STATE MACHINE
-------------------
The audit's finding was that v2 "cannot express state, and most real
discretionary strategies are state machines: wait for X, then watch for Y,
then enter, then manage." A boolean-per-candle model has nowhere to put "the
low of the candle that confirmed the sweep" — the thing the entry and the
stop both refer to.

THE SEQUENCING RULE, tested here rather than assumed: at most ONE transition
fires per bar. Two events that a strategy waits for happen on different bars
by definition, so cascading through several states within a single bar buys no
expressiveness and costs predictability — plus a cycle in the state graph
would spin forever. One bar, one move.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from state_runner import run_machine  # noqa: E402
from strategy.v3 import StateMachineError, parse_machine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


BARS_PER_SESSION = 25


def candles(rows) -> pd.DataFrame:
    """rows: list of (open, high, low, close). One 15m bar each.

    Laid out as real sessions, 25 bars from 09:15 IST and then on to the next
    day. Emitting one unbroken run of 15m stamps instead would put every bar
    on the same calendar date, and `prev_day.*` would be NaN throughout — the
    machine under test would look correct while never firing.
    """
    session_start = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    stamps = []
    for i in range(len(rows)):
        day, slot = divmod(i, BARS_PER_SESSION)
        stamps.append(
            (session_start + timedelta(days=day, minutes=15 * slot)).astimezone(UTC)
        )
    index = pd.DatetimeIndex(stamps, name="ts")
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [1000.0] * len(rows),
        },
        index=index,
    ).astype(float)


def machine(**overrides) -> dict:
    """A minimal two-state machine; tests override one piece at a time."""
    doc = {
        "version": 3,
        "name": "t",
        "timeframe": "15m",
        "position_type": "long",
        "instruments": ["NSE:RELIANCE"],
        "initial": "waiting",
        "states": [
            {
                "name": "waiting",
                "transitions": [{"when": "close > 100", "goto": "armed"}],
            },
            {
                "name": "armed",
                "transitions": [{"when": "close > 110", "enter": {"side": "long"},
                        "goto": "holding"}],
            },
            {
                "name": "holding",
                "transitions": [{"when": "close < 105", "exit": {}, "goto": "waiting"}],
            },
        ],
        "risk": {
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        },
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }
    doc.update(overrides)
    return doc


# --- parsing ----------------------------------------------------------------


def test_a_machine_parses() -> None:
    m = parse_machine(machine())
    assert m.initial == "waiting"
    assert [s.name for s in m.states] == ["waiting", "armed", "holding"]


def test_transitions_keep_their_written_order() -> None:
    """First match wins, so order is meaning, not presentation."""
    doc = machine()
    doc["states"][0]["transitions"] = [
        {"when": "close > 100", "goto": "armed"},
        {"when": "close > 50", "goto": "holding"},
    ]
    m = parse_machine(doc)
    assert [t.goto for t in m.states[0].transitions] == ["armed", "holding"]


def test_an_unknown_goto_is_rejected() -> None:
    """A typo'd state name would silently strand the machine forever."""
    doc = machine()
    doc["states"][0]["transitions"][0]["goto"] = "wating"
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "wating" in str(exc.value)


def test_an_unknown_initial_state_is_rejected() -> None:
    doc = machine(initial="nowhere")
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "nowhere" in str(exc.value)


def test_duplicate_state_names_are_rejected() -> None:
    doc = machine()
    doc["states"].append({"name": "waiting", "transitions": []})
    with pytest.raises(StateMachineError):
        parse_machine(doc)


def test_a_bad_expression_is_reported_with_its_state() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["when"] = "close >"
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    message = str(exc.value)
    assert "waiting" in message


def test_an_unreachable_state_is_rejected() -> None:
    """Dead states are always a mistake — usually a rename half-applied."""
    doc = machine()
    doc["states"].append({"name": "orphan", "transitions": []})
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "orphan" in str(exc.value)


def test_a_variable_used_before_it_is_ever_set_is_rejected() -> None:
    """Caught at parse time, not as a NaN that quietly never fires."""
    doc = machine()
    doc["states"][1]["transitions"][0]["when"] = "close > confirm_low"
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "confirm_low" in str(exc.value)


def test_a_variable_set_earlier_may_be_used_later() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["set"] = {"trigger": "high"}
    doc["states"][1]["transitions"][0]["when"] = "close > trigger"
    m = parse_machine(doc)
    assert "trigger" in m.variables


# --- running ----------------------------------------------------------------


def test_the_machine_advances_one_state_per_bar() -> None:
    doc = machine()
    # Bar 0 satisfies BOTH 'close > 100' and 'close > 110'. It must move to
    # 'armed' only, leaving the entry for a later bar.
    df = candles([(120, 121, 119, 120)] * 3)
    result = run_machine(parse_machine(doc), df)
    assert result.states[0] == "waiting"     # state ON ENTRY to bar 0
    assert result.states[1] == "armed"
    assert result.states[2] == "holding"


def test_an_entry_is_recorded_with_its_bar() -> None:
    doc = machine()
    df = candles([
        (101, 102, 100, 101),   # -> armed
        (111, 112, 110, 111),   # -> enter, holding
        (111, 112, 110, 111),
    ])
    result = run_machine(parse_machine(doc), df)
    assert len(result.entries) == 1
    assert result.entries[0].bar == 1
    assert result.entries[0].side == "long"


def test_an_exit_is_recorded() -> None:
    doc = machine()
    df = candles([
        (101, 102, 100, 101),
        (111, 112, 110, 111),
        (100, 101, 99, 100),    # -> exit
    ])
    result = run_machine(parse_machine(doc), df)
    assert len(result.exits) == 1
    assert result.exits[0].bar == 2


def test_no_transition_means_the_state_holds() -> None:
    doc = machine()
    df = candles([(50, 51, 49, 50)] * 4)
    result = run_machine(parse_machine(doc), df)
    assert set(result.states) == {"waiting"}
    assert result.entries == []


def test_the_first_matching_transition_wins() -> None:
    doc = machine()
    doc["states"][0]["transitions"] = [
        {"when": "close > 100", "goto": "holding"},
        {"when": "close > 100", "goto": "armed"},
    ]
    df = candles([(120, 121, 119, 120)] * 2)
    result = run_machine(parse_machine(doc), df)
    assert result.states[1] == "holding"


# --- variables --------------------------------------------------------------


def test_a_variable_is_captured_at_the_transition_bar() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["set"] = {"trigger": "high"}
    doc["states"][1]["transitions"][0] = {
        "when": "close > trigger", "enter": {"side": "long"}, "goto": "holding",
    }
    df = candles([
        (101, 105, 100, 101),   # -> armed, trigger = 105
        (104, 106, 103, 104),   # 104 < 105, no entry
        (107, 108, 106, 107),   # 107 > 105, entry
    ])
    result = run_machine(parse_machine(doc), df)
    assert [e.bar for e in result.entries] == [2]


def test_a_variable_persists_until_reassigned() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["set"] = {"trigger": "high"}
    doc["states"][1]["transitions"][0] = {
        "when": "close > trigger", "enter": {"side": "long"}, "goto": "holding",
    }
    doc["states"][2]["transitions"][0] = {"when": "close < 1", "exit": {}, "goto": "waiting"}
    df = candles([
        (101, 105, 100, 101),
        (102, 103, 101, 102),
        (102, 103, 101, 102),
        (107, 108, 106, 107),
    ])
    result = run_machine(parse_machine(doc), df)
    # trigger stayed 105 across the idle bars, so bar 3 still triggers.
    assert [e.bar for e in result.entries] == [3]


def test_a_variable_may_hold_an_arithmetic_result() -> None:
    """`level = (B * C) / A`, the audit's example, as a captured variable."""
    doc = machine()
    doc["states"][0]["transitions"][0]["set"] = {"level": "(high * close) / low"}
    doc["states"][1]["transitions"][0] = {
        "when": "close > level", "enter": {"side": "long"}, "goto": "holding",
    }
    df = candles([
        (101, 101, 101, 101),   # close > 100 fires; level = (101*101)/101 = 101
        (99, 99, 99, 99),       # 99 > 101 -> no
        (102, 102, 102, 102),   # 102 > 101 -> entry
    ])
    result = run_machine(parse_machine(doc), df)
    assert [e.bar for e in result.entries] == [2]


# --- timeout ----------------------------------------------------------------


def test_a_timeout_returns_to_the_named_state() -> None:
    """Without this, 'wait for confirmation' waits forever."""
    doc = machine()
    doc["states"][1]["timeout"] = {"bars": 2, "goto": "waiting"}
    df = candles([
        (101, 102, 100, 101),   # -> armed
        (101, 102, 100, 101),   # armed, 1 bar
        (101, 102, 100, 101),   # armed, 2 bars -> timeout back to waiting
        (101, 102, 100, 101),
    ])
    result = run_machine(parse_machine(doc), df)
    assert result.states[3] == "waiting"


def test_a_timeout_does_not_fire_when_a_transition_does() -> None:
    doc = machine()
    doc["states"][1]["timeout"] = {"bars": 1, "goto": "waiting"}
    df = candles([
        (101, 102, 100, 101),   # -> armed
        (111, 112, 110, 111),   # transition wins over timeout
        (111, 112, 110, 111),   # a third bar, so states[2] exists to inspect
    ])
    result = run_machine(parse_machine(doc), df)
    assert result.states[2] == "holding"
    assert len(result.entries) == 1


# --- the audit's example ----------------------------------------------------


def liquidity_sweep_machine() -> dict:
    """§12 of the audit, transcribed. The strategy v2 could not express."""
    return {
        "version": 3,
        "name": "liquidity-sweep",
        "timeframe": "15m",
        "position_type": "short",
        "instruments": ["NSE:RELIANCE"],
        "initial": "waiting_for_sweep",
        "states": [
            {
                "name": "waiting_for_sweep",
                "transitions": [{
                    "when": "low < prev_day.low",
                    "set": {"swept_low": "low"},
                    "goto": "waiting_for_confirmation",
                }],
            },
            {
                "name": "waiting_for_confirmation",
                "timeout": {"bars": 20, "goto": "waiting_for_sweep"},
                "transitions": [{
                    "when": "candle.is_bearish and close > prev_day.low",
                    "set": {"confirm_high": "high", "confirm_low": "low"},
                    "goto": "armed",
                }],
            },
            {
                "name": "armed",
                "transitions": [{
                    "when": "low < confirm_low",
                    "enter": {"side": "short", "stop": "confirm_high"},
                    "goto": "in_position",
                }],
            },
            {
                "name": "in_position",
                "transitions": [{
                    "when": "close > confirm_high",
                    "exit": {},
                    "goto": "waiting_for_sweep",
                }],
            },
        ],
        "risk": {
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        },
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }


def test_the_audit_liquidity_strategy_parses() -> None:
    """The headline claim: 'NOT SUPPORTED' in v2, expressible in v3."""
    m = parse_machine(liquidity_sweep_machine())
    assert [s.name for s in m.states] == [
        "waiting_for_sweep", "waiting_for_confirmation", "armed", "in_position",
    ]
    assert m.variables == {"swept_low", "confirm_high", "confirm_low"}


def test_the_audit_liquidity_strategy_runs_and_can_enter() -> None:
    m = parse_machine(liquidity_sweep_machine())
    # Day 1 sets the reference; day 2 sweeps below it, confirms, then breaks.
    day1 = [(100, 102, 98, 100)] * 25          # prev_day.low = 98
    day2 = [
        (99, 100, 97, 99),      # low 97 < 98 -> swept
        (99, 100, 99, 98.5),    # bearish, close 98.5 > 98 -> armed
        (98.5, 99, 96, 97),     # low 96 < confirm_low 99 -> ENTER short
        (97, 98, 96, 97),
    ]
    df = candles(day1 + day2)
    result = run_machine(m, df)
    assert len(result.entries) == 1
    assert result.entries[0].side == "short"
    # The stop refers to the CONFIRMING candle's high, not to entry price.
    assert result.entries[0].stop == pytest.approx(100.0)


def test_the_sweep_machine_times_out_without_confirmation() -> None:
    m = parse_machine(liquidity_sweep_machine())
    day1 = [(100, 102, 98, 100)] * 25
    # Sweep, then bars that never confirm AND never sweep again: they sit
    # above yesterday's low of 98 and close flat, so neither the confirmation
    # rule nor a second sweep can fire while the timeout counts down.
    day2 = [(99, 100, 97, 99)] + [(99, 99.5, 98.5, 99)] * 22
    df = candles(day1 + day2)
    result = run_machine(m, df)
    assert result.entries == []
    assert result.states[-1] == "waiting_for_sweep"


# --- no look-ahead ----------------------------------------------------------


def test_the_machine_cannot_see_the_future() -> None:
    """Same property as the expressions, at the machine level: the state on
    entry to bar i must not depend on anything after bar i."""
    m = parse_machine(liquidity_sweep_machine())
    day1 = [(100, 102, 98, 100)] * 25
    day2 = [
        (99, 100, 97, 99), (99, 100, 99, 98.5), (98.5, 99, 96, 97),
        (97, 98, 96, 97), (97, 101, 96, 100.5),
    ]
    df = candles(day1 + day2)
    full = run_machine(m, df)
    for i in range(25, len(df)):
        truncated = run_machine(m, df.iloc[:i + 1])
        assert truncated.states[i] == full.states[i], f"state differs at bar {i}"


# --- the YAML surface -------------------------------------------------------
#
# Every test above builds documents as Python dicts, where "on" is just a
# string. The real workflow is YAML pasted from an AI tool — and YAML 1.1
# reads a bare `on:` as the BOOLEAN true. A format whose keys silently change
# type between the tests and the product is a trap, so the surface is tested
# as text here rather than as dicts.


def test_a_yaml_document_round_trips() -> None:
    import yaml

    text = """
version: 3
name: yaml-machine
timeframe: 15m
instruments: [NSE:RELIANCE]
initial: flat
states:
  - name: flat
    transitions:
      - when: "close > prev_day.high"
        set: {trigger: "high"}
        enter: {side: long, stop: "low"}
        goto: holding
  - name: holding
    transitions:
      - when: "close < trigger"
        exit: {}
        goto: flat
risk:
  stop_loss: {type: percent, value: 1.0}
  target: {type: percent, value: 2.0}
sizing: {type: notional, notional_per_trade: 100000}
"""
    machine = parse_machine(yaml.safe_load(text))
    assert [s.name for s in machine.states] == ["flat", "holding"]
    assert machine.variables == {"trigger"}


def test_the_yaml_boolean_trap_is_named_not_just_rejected() -> None:
    """`on:` becomes True, the state ends up with no transitions, and the
    machine strands. The error has to say WHY, because the document looks
    perfectly reasonable to whoever wrote it."""
    import yaml

    text = """
version: 3
name: trap
timeframe: 15m
instruments: [NSE:RELIANCE]
initial: flat
states:
  - name: flat
    on:
      - when: "close > 100"
        goto: flat
risk:
  stop_loss: {type: percent, value: 1.0}
  target: {type: percent, value: 2.0}
sizing: {type: notional, notional_per_trade: 100000}
"""
    loaded = yaml.safe_load(text)
    assert True in loaded["states"][0], "precondition: YAML really does do this"

    with pytest.raises(StateMachineError) as exc:
        parse_machine(loaded)
    message = str(exc.value)
    assert "on:" in message and "transitions:" in message


# --- functions are checked at parse time ------------------------------------


def test_an_unknown_function_is_rejected_when_the_machine_is_parsed() -> None:
    """Not mid-backtest, after minutes of fetching, on whichever symbol
    reached that transition first."""
    doc = machine()
    doc["states"][0]["transitions"][0]["when"] = "supersignal(9) > 1"
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "supersignal" in str(exc.value)


def test_a_multi_output_indicator_without_an_output_is_rejected() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["when"] = "macd(12, 26, 9) > 0"
    with pytest.raises(StateMachineError) as exc:
        parse_machine(doc)
    assert "macd.line" in str(exc.value)


def test_a_swing_call_is_accepted() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["when"] = "close > swing.high(5)"
    assert parse_machine(doc) is not None


def test_a_daily_wrapped_call_is_accepted() -> None:
    doc = machine()
    doc["states"][0]["transitions"][0]["when"] = "close > daily.ema(50)"
    assert parse_machine(doc) is not None
