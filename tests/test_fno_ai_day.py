"""The options research day, end to end, with a fake engine and a fake AI."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import atlas  # noqa: E402
from fno.ai import day as options_day  # noqa: E402
from research.blocks import as_html, as_text  # noqa: E402

DAY = date(2026, 10, 21)


def fake_report(underlying, *, good, exam=False):
    avg = 6.0 if good else -0.5
    basket = SimpleNamespace(month_count=120, avg_month_pct=avg, trades_per_month=15.0,
                             years_beating_holding_pct=80.0 if good else 0.0, edge_t=3.0 if good else 0.1,
                             avg_excess_pct=1.0, years=())
    headline = {"avg_month_pct": avg, "median_month_pct": avg, "months_positive_pct": 60.0,
                "worst_fall_pct": 8.0, "worst_day_pct": -3.0, "worst_day": date(2020, 3, 23),
                "trades": 1800, "trades_per_month": 15.0, "win_rate_pct": 60.0, "avg_days_held": 2.0,
                "total_charges": 1.0, "end_value": 9e6, "avg_holding_month_pct": 1.0,
                "years_beating_holding_pct": basket.years_beating_holding_pct, "edge_t": basket.edge_t}
    end = date(2026, 10, 16) if exam else date(2025, 10, 17)
    return SimpleNamespace(underlying=underlying, start=date(2016, 1, 1), end=end, headline=headline,
                           basket=basket, splits={}, worst_days=[], shock_days=[], skipped={}, notes=[])


class FakeLab:
    def __init__(self, good=(), broken=()):
        self.good, self.broken, self.calls = set(good), set(broken), []

    def run(self, strategy, *, exam=False):
        self.calls.append(("exam" if exam else "train", strategy.underlying))
        if strategy.underlying in self.broken:
            raise KeyError("no such column")
        return fake_report(strategy.underlying, good=strategy.underlying in self.good, exam=exam), 2400


class FakeBrain:
    def __init__(self, lab, versions=1):
        self.lab, self.left, self.seen = lab, versions, []

    def propose(self, **kw):
        self.seen.append(("propose", len(self.lab.calls)))
        return {"title": "calm strangle", "description": "d", "hypothesis": "h", "change_note": "",
                "strategy_yaml": atlas.document("short-strangle-20d", "NIFTY")}

    def review(self, **kw):
        self.seen.append(("review", len(self.lab.calls)))
        self.left -= 1
        return {"why_failed": "", "why_worked": "", "lessons": "a lesson",
                "decision": "stop" if self.left <= 0 else "next_version", "change_hint": "tighter"}


def run(lab, brain):
    return options_day.run_day(brain=brain, lab=lab, day=DAY, max_versions=5, budget_seconds=3600,
                               echo=lambda *a, **k: None)


def test_a_passing_version_opens_its_own_index_exam_once_after_the_last_ai_call():
    lab = FakeLab(good={"NIFTY"})
    brain = FakeBrain(lab, versions=2)
    outcome, final, pick, exam = run(lab, brain)
    assert pick["underlying"] == "NIFTY"
    assert [c for c in lab.calls if c[0] == "exam"] == [("exam", "NIFTY")]
    assert lab.calls[-1] == ("exam", "NIFTY")
    assert brain.seen[-1][0] == "review" and brain.seen[-1][1] < len(lab.calls)
    assert exam["strategy_name"].startswith("O-20261021-calm-strangle") and exam["beat_holding"]
    subject, blocks = options_day.message(DAY, outcome, final, pick, exam)
    text = as_text(blocks)
    assert text.splitlines()[1].startswith("Verdict: PASSED training on NIFTY")
    assert "<b>" in as_html(blocks) and "<pre>" in as_html(blocks)


def test_nothing_passing_opens_no_exam_and_says_so_first():
    lab = FakeLab()
    outcome, final, pick, exam = run(lab, FakeBrain(lab))
    assert pick is None and exam is None
    assert all(c[0] == "train" for c in lab.calls)
    subject, blocks = options_day.message(DAY, outcome, final, pick, exam)
    text = as_text(blocks)
    assert text.splitlines()[1] == ("Verdict: NOTHING PASSED: no version passes the rule on NIFTY or "
                                    "BANKNIFTY. Not usable.")
    assert "NOTHING PASSED" in subject


def test_an_engine_error_on_one_index_is_a_result_not_a_crash():
    lab = FakeLab(broken={"BANKNIFTY"})
    outcome, final, pick, exam = run(lab, FakeBrain(lab))
    bank = outcome.versions[0].summary.indexes[1]
    assert bank["underlying"] == "BANKNIFTY" and bank["passes"] is False
    assert "could not run it: KeyError" in bank["why_not"]
    _, blocks = options_day.message(DAY, outcome, final, pick, exam)
    text = as_text(blocks)
    assert "BANKNIFTY      could not run" in text and "BANKNIFTY: the engine could not run it" in text


def test_the_dry_brain_proposes_a_strategy_the_checker_accepts():
    lab = FakeLab()
    outcome, final, pick, exam = run(lab, options_day.DryBrain())
    assert outcome.versions[0].valid and outcome.stopped_because == "stop"
