"""What the dashboard needs in order to show and accept a v3 machine.

The page itself is Streamlit and cannot be exercised headlessly, so the parts
worth testing are pulled out as pure functions: which format a document is,
what its states look like as rows, and — the one that actually breaks —
whether a pasted v3 document survives the storage path without being run
through the v1->v2 migration.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_pages.strategies import _machine_states_count, _scope_of  # noqa: E402
from strategy.v3 import is_v3_document, machine_outline  # noqa: E402

V3_TEXT = """
version: 3
name: ui-machine
timeframe: 15m
instruments: [NSE:RELIANCE]
initial: watching
on_position_closed: watching
states:
  - name: watching
    timeout: {bars: 12, goto: watching}
    transitions:
      - when: "low < prev_day.low"
        set: {floor: "low"}
        goto: armed
  - name: armed
    transitions:
      - when: "close > floor"
        enter: {side: long, stop: "floor", target: "close + 5"}
        goto: holding
  - name: holding
    transitions:
      - when: "close < floor"
        exit: {fraction: 0.5, reason: scale_out}
        goto: watching
risk:
  stop_loss: {type: percent, value: 1.0}
  target: {type: percent, value: 2.0}
sizing: {type: notional, notional_per_trade: 100000}
"""

V2_DOC = {
    "version": 2,
    "name": "plain",
    "timeframe": "15m",
    "instruments": ["NSE:RELIANCE"],
    "entry": {"all": []},
}


def v3_doc() -> dict:
    return yaml.safe_load(V3_TEXT)


# --- telling the two formats apart ------------------------------------------


def test_a_v3_document_is_recognised() -> None:
    assert is_v3_document(v3_doc())


def test_a_v2_document_is_not_mistaken_for_v3() -> None:
    assert not is_v3_document(V2_DOC)


def test_junk_is_not_a_v3_document() -> None:
    for junk in [None, "version: 3", 3, [], {}]:
        assert not is_v3_document(junk)


def test_states_are_counted_only_for_v3() -> None:
    assert _machine_states_count(v3_doc()) == 3
    assert _machine_states_count(V2_DOC) == 0


# --- the outline the page renders -------------------------------------------


def test_the_outline_has_a_row_per_transition() -> None:
    rows = machine_outline(v3_doc())
    assert [r["State"] for r in rows] == [
        "watching", "watching", "armed", "holding",
    ]


def test_the_outline_names_the_timeout_row() -> None:
    rows = machine_outline(v3_doc())
    timeout = [r for r in rows if r["Does"] == "timeout"]
    assert len(timeout) == 1
    assert "12 bars" in timeout[0]["When"]


def test_the_outline_describes_an_entry() -> None:
    rows = machine_outline(v3_doc())
    entry = next(r for r in rows if r["State"] == "armed")
    assert "ENTER long" in entry["Does"]
    assert "stop floor" in entry["Does"]


def test_the_outline_marks_a_partial_exit_with_its_share() -> None:
    """A full exit and a half exit must not read the same on screen."""
    rows = machine_outline(v3_doc())
    exit_row = next(r for r in rows if r["State"] == "holding")
    assert exit_row["Does"] == "EXIT 50%"


def test_the_outline_lists_captured_variables() -> None:
    rows = machine_outline(v3_doc())
    assert any("set floor" in r["Does"] for r in rows)


def test_the_outline_survives_a_broken_draft() -> None:
    """A draft that failed validation is exactly when you want to see its
    shape, and it cannot be parsed by definition."""
    broken = {"version": 3, "states": [
        {"name": "a"},                       # no transitions at all
        {"name": "b", "transitions": "nope"},  # not even a list
        "not a mapping",
    ]}
    rows = machine_outline(broken)
    assert [r["State"] for r in rows] == ["a", "b"]


def test_the_outline_of_a_v2_document_is_empty() -> None:
    assert machine_outline(V2_DOC) == []


# --- the grid reads a machine the same way it reads a strategy --------------


def test_scope_reads_instruments_from_a_machine() -> None:
    assert _scope_of(v3_doc()) == "NSE:RELIANCE"


def test_scope_reads_a_universe_from_a_machine() -> None:
    doc = v3_doc()
    del doc["instruments"]
    doc["universe"] = "NIFTY50"
    assert _scope_of(doc) == "universe NIFTY50"


# --- pasting one ------------------------------------------------------------


class FakeStore:
    """Just enough SupabaseStore to exercise save_strategy_text's routing."""

    def __init__(self) -> None:
        self.saved: dict | None = None

    def known_universe_names(self):
        return {"NIFTY50"}

    def save_strategy_document(self, doc, *, raw_source=None):
        self.saved = dict(doc)
        return "saved"


def test_pasting_a_v3_document_reaches_storage_unchanged() -> None:
    """The v1->v2 migration would reject a machine for having no `entry:`.
    A v3 paste must skip it entirely."""
    from db import SupabaseStore

    store = FakeStore()
    result = SupabaseStore.save_strategy_text(store, V3_TEXT)

    assert result == "saved"
    assert store.saved is not None
    assert store.saved["version"] == 3
    assert [s["name"] for s in store.saved["states"]] == [
        "watching", "armed", "holding",
    ]


def test_a_v3_document_wrapped_in_a_strategies_list_is_still_v3() -> None:
    """An AI tool may wrap it the way v2 documents are wrapped. That must not
    send it down the migration path."""
    from db import SupabaseStore

    wrapped = yaml.safe_dump({"version": 3, "strategies": [v3_doc()]})
    store = FakeStore()
    SupabaseStore.save_strategy_text(store, wrapped)
    assert store.saved is not None
    assert store.saved["version"] == 3


def test_pasting_a_v2_document_still_migrates() -> None:
    from db import SupabaseStore

    text = yaml.safe_dump({
        "version": 2,
        "strategies": [{
            "name": "v2-paste", "enabled": True, "position_type": "long",
            "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
            "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
            "exit": {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
            "risk": {"stop_loss": {"type": "percent", "value": 1.0},
                     "target": {"type": "percent", "value": 2.0}},
            "sizing": {"type": "notional", "notional_per_trade": 100000},
        }],
    })
    store = FakeStore()
    SupabaseStore.save_strategy_text(store, text)
    assert store.saved is not None
    assert store.saved["name"] == "v2-paste"
    assert "states" not in store.saved


# --- setups in progress -----------------------------------------------------
#
# `positions` records what is open and `trades` what finished. Neither can say
# that a symbol swept its previous-day low an hour ago and is still waiting for
# a confirming candle — so without this panel a v3 strategy looks completely
# idle right up until the moment it trades.


def a_machine():
    from strategy.v3 import parse_machine
    return parse_machine(v3_doc())


def test_a_setup_row_says_what_it_is_waiting_for() -> None:
    from strategy.v3 import setups_in_progress

    rows = [{
        "strategy_name": "ui-machine", "instrument": "NSE:RELIANCE",
        "state": "armed", "variables": {"floor": 97.3}, "bars_in_state": 2,
    }]
    out = setups_in_progress(rows, {"ui-machine": a_machine()})
    assert out[0]["Symbol"] == "NSE:RELIANCE"
    assert out[0]["Waiting in"] == "armed"
    assert out[0]["For"] == "close > floor"
    assert out[0]["Remembered"] == "floor=97.3"
    assert out[0]["Bars"] == 2


def test_variables_arriving_as_json_text_are_read() -> None:
    """PostgREST usually hands back a dict, but a jsonb column can arrive as
    text depending on the client — a blank cell here would look like a machine
    that had captured nothing."""
    from strategy.v3 import setups_in_progress

    rows = [{
        "strategy_name": "ui-machine", "instrument": "NSE:TCS",
        "state": "armed", "variables": '{"floor": 12.5}', "bars_in_state": 1,
    }]
    out = setups_in_progress(rows, {"ui-machine": a_machine()})
    assert out[0]["Remembered"] == "floor=12.5"


def test_a_machine_with_no_variables_yet_shows_a_dash() -> None:
    from strategy.v3 import setups_in_progress

    rows = [{
        "strategy_name": "ui-machine", "instrument": "NSE:TCS",
        "state": "watching", "variables": {}, "bars_in_state": 0,
    }]
    assert setups_in_progress(rows, {"ui-machine": a_machine()})[0]["Remembered"] == "—"


def test_a_state_removed_from_the_strategy_is_named_not_blank() -> None:
    """The strategy was edited while a machine sat in a state that no longer
    exists. Saying so beats an empty cell."""
    from strategy.v3 import setups_in_progress

    rows = [{
        "strategy_name": "ui-machine", "instrument": "NSE:TCS",
        "state": "deleted_state", "variables": {}, "bars_in_state": 4,
    }]
    out = setups_in_progress(rows, {"ui-machine": a_machine()})
    assert "no longer" in out[0]["For"]


def test_an_unknown_strategy_still_renders_its_row() -> None:
    from strategy.v3 import setups_in_progress

    rows = [{
        "strategy_name": "gone", "instrument": "NSE:TCS",
        "state": "armed", "variables": {}, "bars_in_state": 1,
    }]
    out = setups_in_progress(rows, {})
    assert out[0]["Strategy"] == "gone"
    assert out[0]["For"] == "—"


def test_the_page_helper_skips_a_document_that_no_longer_parses() -> None:
    """One broken strategy must not take down a read-only panel."""
    import pandas as pd
    from app_pages.paper_trading import _v3_machines

    frame = pd.DataFrame([
        {"name": "good", "definition": v3_doc()},
        {"name": "broken", "definition": {"version": 3, "states": "nope"}},
        {"name": "a-v2-one", "definition": V2_DOC},
    ])
    machines = _v3_machines(frame)
    assert list(machines) == ["good"]
