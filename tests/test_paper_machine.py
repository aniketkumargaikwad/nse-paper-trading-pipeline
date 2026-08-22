"""Paper trading a v3 machine across stateless runs.

The engine wakes every 15 minutes, rebuilds everything from the database, acts
and exits. A machine that needs three candles to reach an entry therefore only
works if its state SURVIVES between those runs — otherwise it restarts from
its initial state every tick and can never reach an entry at all, while
reporting nothing wrong.

That is what these tests are really about: not "does a machine trade", but
"does it remember".
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings  # noqa: E402
from db import ClosedTrade, OpenPosition  # noqa: E402
from paper_engine import RunSummary, run_once  # noqa: E402
from paper_machine import min_candles_required  # noqa: E402
from strategy.v3 import parse_machine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

SETTINGS = Settings(
    supabase_url="https://x.supabase.co", supabase_service_role_key="k",
    slippage_pct=0.0, cost_per_trade_inr=0.0,
)

# Friday 2026-07-17, 10:15:30 IST — last CLOSED 15m candle starts 10:00.
NOW = datetime(2026, 7, 17, 10, 15, 30, tzinfo=IST).astimezone(UTC)
SESSION_START = datetime(2026, 7, 17, 9, 15, tzinfo=IST)


def frame(rows) -> pd.DataFrame:
    arr = np.asarray(rows, dtype=float)
    index = pd.DatetimeIndex(
        [(SESSION_START + timedelta(minutes=15 * i)).astimezone(UTC)
         for i in range(len(rows))]
    )
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": np.full(len(rows), 1000.0)},
        index=index,
    )


class FakeStore:
    """Positions, trades and machine state, all in memory."""

    def __init__(self):
        self.positions: dict[tuple[str, str], OpenPosition] = {}
        self.trades: list[ClosedTrade] = []
        self.machine_state: dict[tuple[str, str], dict] = {}
        self.cleared: list[tuple[str, str]] = []

    def universe_members(self, name):
        return ("NSE:RELIANCE",), "2026-07-01"

    def list_open_positions(self):
        return list(self.positions.values())

    def completed_cycles_between(self, *a, **k):
        return 0

    def open_position(self, **kw):
        key = (kw["strategy_name"], kw["instrument"])
        if key in self.positions:
            return None
        pos = OpenPosition(id=str(uuid.uuid4()), **kw)
        self.positions[key] = pos
        return pos

    def close_position(self, position, trade):
        self.trades.append(trade)
        self.positions.pop((position.strategy_name, position.instrument), None)
        return True

    def get_machine_state(self, name, instrument):
        return self.machine_state.get((name, instrument))

    def save_machine_state(self, name, instrument, *, state, variables,
                           bars_in_state, last_candle_ts):
        self.machine_state[(name, instrument)] = {
            "state": state, "variables": dict(variables),
            "bars_in_state": bars_in_state, "last_candle_ts": last_candle_ts,
        }

    def clear_machine_state(self, name, instrument):
        self.cleared.append((name, instrument))
        self.machine_state.pop((name, instrument), None)


class FakeClient:
    def __init__(self, df): self.df = df
    def resolve_instrument_tokens(self, instruments, today_ist):
        return {inst: 1 for inst in instruments}
    def fetch_historical_candles(self, token, tf, from_utc, to_utc, *,
                                 closed_only=True, now_utc=None):
        return self.df


def machine_doc(**overrides) -> dict:
    doc = {
        "version": 3, "name": "pm-test", "timeframe": "15m",
        "instruments": ["NSE:RELIANCE"],
        "initial": "watching", "on_position_closed": "watching",
        "states": [
            {"name": "watching",
             "transitions": [{"when": "close > 105", "set": {"trigger": "high"},
                              "goto": "armed"}]},
            {"name": "armed",
             "transitions": [{"when": "close > trigger",
                              "enter": {"side": "long", "stop": "trigger"},
                              "goto": "holding"}]},
            {"name": "holding",
             "transitions": [{"when": "close < 90", "exit": {}, "goto": "watching"}]},
        ],
        "risk": {"stop_loss": {"type": "percent", "value": 1.0},
                 "target": {"type": "percent", "value": 2.0}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
        "max_cycles_per_day": 5,
    }
    doc.update(overrides)
    return doc


def run(store, rows, doc=None, now=NOW) -> RunSummary:
    machine = parse_machine(doc or machine_doc())
    return run_once(now_utc=now, settings=SETTINGS, store=store,
                    client=FakeClient(frame(rows)), strategies=[machine])


QUIET = [(100, 101, 99, 100)] * 3
FORMING = (108, 108.5, 107.5, 108)


# --- warm-up ----------------------------------------------------------------


def test_history_needed_covers_the_widest_lookback() -> None:
    doc = machine_doc()
    doc["states"][0]["transitions"][0]["when"] = "close > swing.high(10)"
    assert min_candles_required(parse_machine(doc)) >= 22


def test_history_needed_covers_an_offset() -> None:
    doc = machine_doc()
    doc["states"][0]["transitions"][0]["when"] = "close > close[30]"
    assert min_candles_required(parse_machine(doc)) >= 31


# --- remembering across runs ------------------------------------------------


def test_a_transition_is_remembered_for_the_next_run() -> None:
    store = FakeStore()
    run(store, QUIET + [(100, 107, 99, 106), FORMING])
    saved = store.machine_state[("pm-test", "NSE:RELIANCE")]
    assert saved["state"] == "armed"
    assert saved["variables"]["trigger"] == 107.0


def test_a_machine_resumes_where_it_was_left() -> None:
    """THE test. Without persistence the machine restarts in `watching` every
    tick, never reaches `armed`, and never trades — silently."""
    store = FakeStore()
    # Run one: the 10:00 candle arms it.
    run(store, QUIET + [(100, 107, 99, 106), FORMING])
    assert store.positions == {}

    # Run two, 15 minutes later: a new closed candle breaks the trigger.
    later = datetime(2026, 7, 17, 10, 30, 30, tzinfo=IST).astimezone(UTC)
    rows = QUIET + [(100, 107, 99, 106), (107, 109, 106, 108), (109, 110, 108, 109)]
    run(store, rows, now=later)
    assert len(store.positions) == 1, "did not resume; machine restarted"
    pos = store.positions[("pm-test", "NSE:RELIANCE")]
    assert pos.stop_loss_price == 107.0, "the remembered level was lost"


def test_without_saved_state_the_machine_starts_at_the_beginning() -> None:
    store = FakeStore()
    later = datetime(2026, 7, 17, 10, 30, 30, tzinfo=IST).astimezone(UTC)
    rows = QUIET + [(100, 107, 99, 106), (107, 109, 106, 108), (109, 110, 108, 109)]
    run(store, rows, now=later)
    # One step only: it reaches `armed`, not an entry.
    assert store.positions == {}
    assert store.machine_state[("pm-test", "NSE:RELIANCE")]["state"] == "armed"


def test_a_machine_back_at_its_start_is_forgotten_not_stored() -> None:
    """A row saying "initial state, no variables" is indistinguishable from
    never having run, and would grow the table forever."""
    store = FakeStore()
    run(store, QUIET + [(100, 101, 99, 100), FORMING])
    assert ("pm-test", "NSE:RELIANCE") in store.cleared
    assert store.machine_state == {}


# --- idempotency ------------------------------------------------------------


def test_re_running_on_the_same_candle_does_not_step_twice() -> None:
    """A retried tick must not advance the machine through a state it never
    saw, nor let a timeout count the same candle twice."""
    store = FakeStore()
    rows = QUIET + [(100, 107, 99, 106), FORMING]
    run(store, rows)
    first = dict(store.machine_state[("pm-test", "NSE:RELIANCE")])

    summary = run(store, rows)
    assert store.machine_state[("pm-test", "NSE:RELIANCE")] == first
    assert any("already stepped" in note for note in summary.skipped)


# --- exits ------------------------------------------------------------------


def test_a_stop_out_returns_the_machine_to_its_reset_state() -> None:
    store = FakeStore()
    ts = datetime(2026, 7, 17, 9, 30, tzinfo=IST).astimezone(UTC)
    store.positions[("pm-test", "NSE:RELIANCE")] = OpenPosition(
        id="p1", strategy_name="pm-test", instrument="NSE:RELIANCE",
        position_type="long", quantity=1,
        entry_signal_candle_ts=ts, entry_fill_ts=ts,
        intended_entry_price=100.0, entry_price=100.0,
        stop_loss_price=99.0, target_price=102.0,
    )
    store.machine_state[("pm-test", "NSE:RELIANCE")] = {
        "state": "holding", "variables": {"trigger": 107.0},
        "bars_in_state": 3, "last_candle_ts": None,
    }
    # A candle that trades through the stop.
    summary = run(store, QUIET + [(100, 100, 98, 98), FORMING])
    assert summary.exits == 1
    assert store.machine_state[("pm-test", "NSE:RELIANCE")]["state"] == "watching"


def test_a_partial_exit_is_refused_rather_than_faked() -> None:
    """`positions` cannot hold a reduced quantity, so a partial would either
    book the whole position or leave a row claiming a size not held."""
    doc = machine_doc()
    doc["states"][2]["transitions"][0] = {
        "when": "close < 200", "exit": {"fraction": 0.5}, "goto": "watching",
    }
    store = FakeStore()
    ts = datetime(2026, 7, 17, 9, 30, tzinfo=IST).astimezone(UTC)
    store.positions[("pm-test", "NSE:RELIANCE")] = OpenPosition(
        id="p1", strategy_name="pm-test", instrument="NSE:RELIANCE",
        position_type="long", quantity=10,
        entry_signal_candle_ts=ts, entry_fill_ts=ts,
        intended_entry_price=100.0, entry_price=100.0,
        stop_loss_price=1.0, target_price=999.0,
    )
    store.machine_state[("pm-test", "NSE:RELIANCE")] = {
        "state": "holding", "variables": {}, "bars_in_state": 1,
        "last_candle_ts": None,
    }
    summary = run(store, QUIET + [(100, 101, 99, 100), FORMING], doc=doc)
    assert summary.exits == 0
    assert any("partial exit" in note for note in summary.skipped)
    assert len(store.positions) == 1


def test_resuming_into_a_renamed_state_fails_loudly() -> None:
    """A strategy edited between runs must not silently restart."""
    from state_runner import MachineStepper

    machine = parse_machine(machine_doc())
    stepper = MachineStepper(machine, frame(QUIET + [FORMING]))
    with pytest.raises(ValueError) as exc:
        stepper.restore(state="gone", variables={}, bars_in_state=0)
    assert "gone" in str(exc.value)


def test_an_idling_machine_with_a_timeout_is_still_remembered() -> None:
    """The counter only matters when something counts it — but when a
    timeout does, dropping the row would restart the countdown every tick."""
    doc = machine_doc()
    doc["states"][0]["timeout"] = {"bars": 5, "goto": "watching"}
    store = FakeStore()
    run(store, QUIET + [(100, 101, 99, 100), FORMING], doc=doc)
    saved = store.machine_state.get(("pm-test", "NSE:RELIANCE"))
    assert saved is not None, "a counting timeout was forgotten"
    assert saved["bars_in_state"] == 1
