"""The atlas: baseline signals the AI reads before it proposes."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.atlas import STRATEGIES, atlas_line, atlas_lines  # noqa: E402
from research.checker import check_proposal  # noqa: E402
from research.prompts import propose_prompt  # noqa: E402
from research.universe import document_uses_volume  # noqa: E402
from strategy.v3 import StateMachine  # noqa: E402


@pytest.mark.parametrize("name", list(STRATEGIES))
def test_every_baseline_passes_the_same_checker_a_proposal_does(name):
    description, yaml_text = STRATEGIES[name]
    checked = check_proposal(yaml_text, idea=name, day=date(2026, 9, 16), version=1)
    assert isinstance(checked.strategy, StateMachine)
    assert checked.document["enabled"] is False
    assert description and len(description) < 160


def test_short_baselines_square_off_the_same_day():
    for name, (_, yaml_text) in STRATEGIES.items():
        doc = check_proposal(yaml_text, idea=name, day=date(2026, 9, 16), version=1).document
        if doc.get("position_type") == "short" or "side: short" in yaml_text:
            assert doc.get("session", {}).get("square_off"), name


def test_only_the_vwap_baseline_reads_volume():
    readers = [name for name, (_, yaml_text) in STRATEGIES.items()
               if document_uses_volume(check_proposal(yaml_text, idea=name, day=date(2026, 9, 16),
                                                      version=1).document)]
    assert readers == ["VWAP-REVERT-LONG"]


def test_the_baselines_cover_the_families_the_facts_name():
    names = " ".join(STRATEGIES)
    for family in ("RSI2", "DONCHIAN", "BB", "GAP", "VWAP", "SUPERTREND", "MACD", "ROC", "SHORT"):
        assert family in names


def a_row(**over):
    row = {
        "name": "RSI2-DIP-TREND", "description": "buy RSI(2) < 10 above the 200-bar average",
        "training_summary": {
            "combos_tested": 1177, "combos_beating_hold": 210,
            "accounts": [
                {"timeframe": "day", "avg_month_pct": 0.85, "months_positive_pct": 58.0,
                 "luck_check": "could be luck", "qualifies": True, "why_not": None},
                {"timeframe": "60m", "avg_month_pct": -0.3, "months_positive_pct": 45.0,
                 "luck_check": "cannot be told from luck", "qualifies": False,
                 "why_not": "lost money on average: -0.30% a month"},
            ],
        },
    }
    row.update(over)
    return row


def test_an_atlas_line_names_the_rule_its_best_account_and_every_timeframe():
    line = atlas_line(a_row())
    assert line.startswith("RSI2-DIP-TREND (buy RSI(2) < 10")
    assert "best account day: +0.85%/month, 58% months up, could be luck" in line
    assert "per timeframe: day +0.85, 60m -0.30" in line
    assert line.endswith("210 of 1177 beat holding")


def test_an_unpickable_best_says_why():
    row = a_row()
    row["training_summary"]["accounts"][0]["qualifies"] = False
    row["training_summary"]["accounts"][0]["why_not"] = "too slow"
    assert "(not pickable: too slow)" in atlas_line(row)


def test_a_summary_stored_as_text_still_reads():
    import json

    row = a_row()
    row["training_summary"] = json.dumps(row["training_summary"])
    assert "best account day" in atlas_line(row)


class _Client:
    def __init__(self, rows=None, fail=False):
        self._rows, self._fail = rows or [], fail

    def table(self, name):
        assert name == "research_atlas"
        return self

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def execute(self):
        if self._fail:
            raise RuntimeError("relation does not exist")
        return type("R", (), {"data": self._rows})()


def test_atlas_lines_come_from_the_table_and_an_absent_table_is_empty():
    assert atlas_lines(_Client([a_row()]))[0].startswith("RSI2-DIP-TREND")
    assert atlas_lines(_Client(fail=True)) == []


def test_the_propose_prompt_carries_the_atlas_when_there_is_one():
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[],
                          atlas=["RSI2-DIP-TREND (...) — best account day: +0.85%/month"])
    assert "Baseline signals already measured" in text
    assert "RSI2-DIP-TREND" in text
    assert "Baseline signals" not in propose_prompt(formats=["F"], notes=[], ideas_tried=[])


def test_the_workflow_matrix_is_built_from_the_same_list():
    text = (Path(__file__).resolve().parent.parent / ".github" / "workflows" / "atlas.yml").read_text(
        encoding="utf-8")
    assert "from research.atlas import STRATEGIES" in text
    assert "python -m research.atlas --strategy" in text
