"""Writing one run and its children, without a database."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.store import ResearchStoreError, save_run  # noqa: E402


class FakeTable:
    def __init__(self, name, log, fail_on=None, captured=None):
        self.name, self._log, self._fail_on = name, log, fail_on
        self._captured = captured
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        if self._captured is not None:
            self._captured.setdefault(self.name, []).extend(payload)
        return self

    def execute(self):
        if self._fail_on == self.name:
            raise RuntimeError(f"insert into {self.name} failed")
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        self._log.append((self.name, len(rows)))
        return SimpleNamespace(data=[{**r, "id": "run-1"} for r in rows])


class FakeClient:
    def __init__(self, fail_on=None, captured=None):
        self.log, self._fail_on, self._captured = [], fail_on, captured

    def table(self, name):
        return FakeTable(name, self.log, self._fail_on, self._captured)


def payload(combos=3, trades=2, equity=4):
    return {
        "run": {"status": "completed", "started_at": None},
        "combos": [{"symbol": f"S{i}"} for i in range(combos)],
        "locked_trades": [{"side": "long"} for _ in range(trades)],
        "equity": [{"day": i} for i in range(equity)],
    }


def totals(log):
    out: dict[str, int] = {}
    for name, count in log:
        out[name] = out.get(name, 0) + count
    return out


def test_save_run_writes_every_table_and_returns_the_id():
    client = FakeClient()
    run_id = save_run(client, **payload())
    assert run_id == "run-1"
    assert totals(client.log) == {
        "research_runs": 1, "research_combo_results": 3,
        "research_locked_trades": 2, "research_locked_equity": 4,
    }


def test_children_are_tagged_with_the_new_run_id():
    captured: dict[str, list] = {}
    save_run(FakeClient(captured=captured), **payload())
    assert all(row["run_id"] == "run-1" for row in captured["research_combo_results"])
    assert all(row["run_id"] == "run-1" for row in captured["research_locked_equity"])


def test_children_are_written_in_chunks():
    client = FakeClient()
    save_run(client, **payload(combos=1100, trades=0, equity=0))
    combo_writes = [n for name, n in client.log if name == "research_combo_results"]
    assert len(combo_writes) == 3 and sum(combo_writes) == 1100


def test_a_failed_run_insert_raises_with_the_table_named():
    with pytest.raises(ResearchStoreError, match="research_runs"):
        save_run(FakeClient(fail_on="research_runs"), **payload())


def test_a_failed_child_insert_names_the_run_that_was_written():
    with pytest.raises(ResearchStoreError, match="run-1"):
        save_run(FakeClient(fail_on="research_combo_results"), **payload())


def test_empty_children_write_nothing():
    client = FakeClient()
    save_run(client, **payload(combos=0, trades=0, equity=0))
    assert totals(client.log) == {"research_runs": 1}


def test_dates_and_timestamps_are_sent_as_iso_text():
    """PostgREST sends JSON, which cannot carry a datetime object."""
    captured: dict[str, list] = {}
    save_run(
        FakeClient(captured=captured),
        run={
            "status": "completed",
            "started_at": datetime(2026, 9, 12, 1, 0, tzinfo=timezone.utc),
            "data_end": date(2026, 7, 31),
        },
        locked_trades=[{"entry_at": datetime(2025, 8, 4, 4, 30, tzinfo=timezone.utc)}],
        equity=[{"day": date(2025, 8, 1), "lakh_balance": 100_000.0}],
    )
    run = captured["research_runs"][0]
    assert run["started_at"] == "2026-09-12T01:00:00+00:00"
    assert run["data_end"] == "2026-07-31"
    assert captured["research_locked_trades"][0]["entry_at"] == "2025-08-04T04:30:00+00:00"
    assert captured["research_locked_equity"][0]["day"] == "2025-08-01"
    assert captured["research_locked_equity"][0]["lakh_balance"] == 100_000.0


def test_json_conversion_leaves_everything_else_alone():
    captured: dict[str, list] = {}
    save_run(
        FakeClient(captured=captured),
        run={"status": "completed", "started_at": None,
             "warnings": ["frozen prices"], "combos_tested": 1213, "verdict_passed": False},
    )
    run = captured["research_runs"][0]
    assert run["warnings"] == ["frozen prices"]
    assert run["combos_tested"] == 1213
    assert run["verdict_passed"] is False
