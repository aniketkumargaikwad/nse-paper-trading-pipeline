"""The stock rotation research day: the format, the judge, the exam only on a pass, the records."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import rotation_day as rd  # noqa: E402

DAY = date(2026, 10, 22)


def test_the_format_example_parses_into_a_variant():
    c = rd.check_proposal(rd.FORMAT, idea="Calm momentum!", day=DAY, version=2)
    v = c.variant
    assert c.document["name"] == "M-20261022-calm-momentum-v2" == v.name
    assert (v.lookback, v.skip, v.top, v.trend, v.trend_sessions) == (12, 1, 20, True, 200)
    assert (v.above_sma, v.max_volatility, v.weighting, v.score) == (200, 45.0, "equal", "return")


def test_no_market_filter_is_allowed():
    v = rd.check_proposal(rd.FORMAT.replace("market_filter: 200", "market_filter: none"),
                          idea="i", day=DAY, version=1).variant
    assert v.trend is False


@pytest.mark.parametrize("old,new,match", [
    ("holdings: 20", "holdings: 3", "holdings"),
    ("lookback_months: 12", "lookback_months: 1", "skip_months must be less"),
    ("score: return ", "score: sharpe ", "score must be"),
    ("version: rotation/1", "version: rotation/2", "version must be"),
    ("weighting: equal", "weighting: cap", "weighting must be"),
    ("  max_volatility_pct: 45", "  max_volatility_pct: 5", "max_volatility_pct"),
    ("holdings: 20", "holdings: 20\nsector_cap: 3", "unknown key"),
    ("market_filter: 200", "market_filter: 20", "market_filter"),
])
def test_refusals_say_what_to_change(old, new, match):
    text = rd.FORMAT.replace(old, new, 1)
    assert text != rd.FORMAT
    with pytest.raises(rd.CheckError, match=match):
        rd.check_proposal(text, idea="i", day=DAY, version=1)


def reading(fall=20.0, years=80.0):
    return {"worst_fall_pct": fall, "years_beating_pct": years}


@pytest.mark.parametrize("s,vs,match", [
    (reading(fall=35.0), {"cagr_lead_pct": 5, "luck_t": 3}, "fell 35.0%"),
    (reading(years=40.0), {"cagr_lead_pct": 5, "luck_t": 3}, "only 40%"),
    (reading(), {"cagr_lead_pct": -1.0, "luck_t": 3}, "no better than"),
    (reading(), {"cagr_lead_pct": 2.0, "luck_t": 1.2}, "could be luck"),
])
def test_the_judge_names_the_first_failure(s, vs, match):
    assert match in rd.disqualified(s, vs)


def test_a_version_that_clears_everything_passes():
    assert rd.disqualified(reading(), {"cagr_lead_pct": 2.0, "luck_t": 2.4}) is None


class FakeLab:
    def __init__(self, passes):
        self.passes, self.calls = passes, []

    def test(self, checked, version_no=0):
        self.calls.append("train")
        why = None if self.passes else "no better than the momentum rule already paper-traded (-1.00% a year)"
        return rd.RotationSummary({"worst_fall_pct": 20.0, "years_beating_pct": 80.0},
                                  {"cagr_lead_pct": 2.0 if self.passes else -1.0, "luck_t": 2.5,
                                   "years_beating_baseline_pct": 70.0}, {}, why)

    def exam_row(self, day, version):
        self.calls.append("exam")
        return {"strategy": {"cagr_pct": 12.0}, "baseline": {"cagr_pct": 9.0}}


@pytest.mark.parametrize("passes", [True, False])
def test_the_exam_opens_once_and_only_on_a_pass(passes):
    lab = FakeLab(passes)
    outcome, final, exam = rd.run_day(brain=rd.DryBrain(), lab=lab, day=DAY, max_versions=5,
                                      budget_seconds=60, echo=lambda *a, **k: None)
    assert lab.calls == (["train", "exam"] if passes else ["train"])
    _, text = rd.message(DAY, outcome, final, exam)
    assert text.splitlines()[1].startswith("Verdict: PASSED" if passes else "Verdict: NOTHING PASSED")


def test_records_feed_the_next_day_and_never_carry_an_exam(tmp_path):
    lab = FakeLab(False)
    outcome, final, exam = rd.run_day(brain=rd.DryBrain(), lab=lab, day=DAY, max_versions=5,
                                      budget_seconds=60, echo=lambda *a, **k: None)
    rd._append(rd._path(rd.LEDGER, tmp_path), rd.version_rows(DAY, outcome.versions))
    rd._append(rd._path(rd.EXAMS, tmp_path), [{"secret_exam_number": 98765.4}])
    assert rd.notes(tmp_path)[0]["lessons"].startswith("Dry run")
    lines = rd.ideas_tried(tmp_path)
    assert len(lines) == 1 and "-1.00%/yr vs the paper rule" in lines[0]
    assert "98765" not in str(rd.read_ledger(tmp_path)) + "".join(lines)
    assert rd.write_note(DAY, outcome.versions, tmp_path).name == "2026-10-22.md"


def test_each_proposal_sees_the_versions_already_tried_today():
    seen = []

    class Claude:
        def ask(self, prompt, schema, instruction=""):
            if "design ONE strategy" in instruction:
                seen.append(prompt)
                return {"title": "t", "description": "d", "hypothesis": "h", "change_note": "",
                        "strategy_yaml": rd.DRY_YAML}
            return {"why_failed": "", "why_worked": "", "lessons": "l", "decision": "new_idea"}

    brain = rd.RotationBrain(Claude(), baseline={"cagr_pct": 1, "worst_fall_pct": 1, "years_beating_pct": 1,
                                                 "months_in_cash_pct": 1, "yearly": {}}, echo=lambda *a: None)
    rd.run_day(brain=brain, lab=FakeLab(False), day=DAY, max_versions=2, budget_seconds=60,
               echo=lambda *a, **k: None)
    assert "today, version 1" not in seen[0]
    assert "today, version 1 (t)" in seen[1] and "return_over_volatility" in seen[1]
