"""Reproducing a stored backtest.

The point is not "run it again" — it is "run the SAME thing again". Three
pins make that true, and each one, left loose, would silently answer a
different question while still producing two numbers that look comparable:

* the strategy VERSION, not the strategy as it reads today
* the symbol LIST, not the universe as it stands today
* the stored WINDOW, not "the last two years" from now
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rerun import RerunError, compare, describe, load_config, parse_strategy_for  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

V2_DEFINITION = {
    # A real v2 snapshot carries no `version` key — verified against the
    # live strategy_versions table.
    "name": "old-one", "enabled": True, "position_type": "long",
    "timeframe": "60m", "universe": "NIFTY50",
    "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                       "operator": "<", "value": 40}]},
    "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                      "operator": ">", "value": 60}]},
    "risk": {"stop_loss": {"type": "percent", "value": 1.0},
             "target": {"type": "percent", "value": 2.0}},
    "sizing": {"type": "notional", "notional_per_trade": 100000},
}

V3_DEFINITION = {
    "version": 3, "name": "machine-one", "timeframe": "60m",
    "instruments": ["NSE:RELIANCE"], "initial": "flat",
    "states": [
        {"name": "flat", "transitions": [
            {"when": "close > 100", "enter": {"side": "long"}, "goto": "held"}]},
        {"name": "held", "transitions": [
            {"when": "close < 90", "exit": {}, "goto": "flat"}]},
    ],
    "risk": {"stop_loss": {"type": "percent", "value": 1.0},
             "target": {"type": "percent", "value": 2.0}},
    "sizing": {"type": "notional", "notional_per_trade": 100000},
}


def run_row(**overrides) -> dict:
    row = {
        "batch_id": "b1", "strategy_name": "old-one", "timeframe": "60m",
        "start_date": "2025-08-01", "end_date": "2026-08-01",
        "universe_name": "NIFTY50",
        "symbols": ["NSE:RELIANCE", "NSE:TCS"],
        "strategy_version_id": 7,
        "total_trades": 42, "net_pnl": -1234.5,
        "cost_model": "flat Rs30",
    }
    row.update(overrides)
    return row


class FakeStore:
    def __init__(self, rows=None, versions=None):
        self.rows = rows if rows is not None else [run_row()]
        self.versions = versions if versions is not None else {
            7: {"id": 7, "strategy_name": "old-one", "version": 3,
                "definition": V2_DEFINITION, "format_version": 2},
        }

    def backtest_run_rows(self, batch_id):
        return [r for r in self.rows if r["batch_id"] == batch_id]

    def strategy_version_by_id(self, version_id):
        return self.versions.get(version_id)


# --- loading the config -----------------------------------------------------


def test_a_stored_batch_yields_its_config() -> None:
    config = load_config(FakeStore(), "b1")[0]
    assert config.strategy_name == "old-one"
    assert config.timeframe == "60m"
    assert config.symbols == ("NSE:RELIANCE", "NSE:TCS")
    assert config.version_number == 3


def test_the_window_covers_the_whole_ist_trading_days() -> None:
    """Reading the stored DATES as midnight UTC would shift the window by 5.5
    hours and quietly drop a session at each end — manufacturing exactly the
    difference this command exists to detect."""
    config = load_config(FakeStore(), "b1")[0]
    assert config.from_utc.astimezone(IST).date().isoformat() == "2025-08-01"
    assert config.to_utc.astimezone(IST).date().isoformat() == "2026-08-01"
    assert config.from_utc < config.to_utc


def test_the_definition_comes_from_the_version_not_the_strategy() -> None:
    """The whole point. The strategy may have been edited since."""
    config = load_config(FakeStore(), "b1")[0]
    assert config.definition["entry"]["all"][0]["value"] == 40


def test_an_unknown_batch_is_refused_with_a_useful_message() -> None:
    with pytest.raises(RerunError) as exc:
        load_config(FakeStore(), "nope")
    assert "nope" in str(exc.value)


def test_a_batch_from_before_versioning_cannot_be_reproduced() -> None:
    """Honest refusal: without a snapshot, only "re-test with today's rules"
    is possible, and that is a different question."""
    store = FakeStore(rows=[run_row(strategy_version_id=None)])
    with pytest.raises(RerunError) as exc:
        load_config(store, "b1")
    assert "versioning" in str(exc.value)


def test_a_missing_version_row_is_refused() -> None:
    store = FakeStore(rows=[run_row(strategy_version_id=99)])
    with pytest.raises(RerunError):
        load_config(store, "b1")


def test_a_batch_with_no_symbol_list_is_refused() -> None:
    store = FakeStore(rows=[run_row(symbols=[])])
    with pytest.raises(RerunError) as exc:
        load_config(store, "b1")
    assert "symbol" in str(exc.value)


def test_a_multi_strategy_batch_yields_one_config_each() -> None:
    store = FakeStore(
        rows=[run_row(), run_row(strategy_name="machine-one",
                                 strategy_version_id=8, symbols=["NSE:RELIANCE"])],
        versions={
            7: {"id": 7, "version": 3, "definition": V2_DEFINITION, "format_version": 2},
            8: {"id": 8, "version": 1, "definition": V3_DEFINITION, "format_version": 3},
        },
    )
    configs = load_config(store, "b1")
    assert [c.strategy_name for c in configs] == ["old-one", "machine-one"]


# --- rebuilding the strategy ------------------------------------------------


def test_a_v2_snapshot_parses_back_into_a_strategy() -> None:
    config = load_config(FakeStore(), "b1")[0]
    strategy = parse_strategy_for(config)
    assert strategy.name == "old-one"
    assert strategy.timeframe == "60m"


def test_a_v3_snapshot_parses_back_into_a_machine() -> None:
    store = FakeStore(
        rows=[run_row(strategy_name="machine-one", strategy_version_id=8)],
        versions={8: {"id": 8, "version": 1, "definition": V3_DEFINITION}},
    )
    machine = parse_strategy_for(load_config(store, "b1")[0])
    assert [s.name for s in machine.states] == ["flat", "held"]


# --- did it reproduce? ------------------------------------------------------


def test_an_identical_result_reports_a_match() -> None:
    config = load_config(FakeStore(), "b1")[0]
    same, detail = compare(config, {"total_trades": 42, "net_pnl": -1234.5})
    assert same
    assert "reproduced exactly" in detail


def test_a_different_trade_count_is_reported() -> None:
    config = load_config(FakeStore(), "b1")[0]
    same, detail = compare(config, {"total_trades": 41, "net_pnl": -1234.5})
    assert not same
    assert "trades 42 -> 41" in detail


def test_a_different_pnl_is_reported() -> None:
    config = load_config(FakeStore(), "b1")[0]
    same, detail = compare(config, {"total_trades": 42, "net_pnl": -1200.0})
    assert not same
    assert "net" in detail


def test_a_sub_paisa_difference_is_not_a_finding() -> None:
    """Float noise is not a change in the data."""
    config = load_config(FakeStore(), "b1")[0]
    same, _ = compare(config, {"total_trades": 42, "net_pnl": -1234.5000001})
    assert same


def test_an_original_run_with_no_verdict_cannot_disagree() -> None:
    store = FakeStore(rows=[run_row(total_trades=None, net_pnl=None)])
    config = load_config(store, "b1")[0]
    same, detail = compare(config, {"total_trades": 5, "net_pnl": 1.0})
    assert same
    assert "no verdict" in detail


# --- what it says it is doing -----------------------------------------------


def test_the_description_names_the_version_and_the_pinning() -> None:
    line = describe(load_config(FakeStore(), "b1")[0])
    assert "old-one v3" in line
    assert "universe NIFTY50" in line
    assert "pinned to 2 symbol(s)" in line
