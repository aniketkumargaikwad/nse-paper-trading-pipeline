"""The index allocation research day: the judge, the neighbours, a pass that starts paper-trading, main()."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import alloc_day as ad  # noqa: E402
from research import paper_books as pb  # noqa: E402
from research.alloc import BENCHMARK, Plan, PlanError  # noqa: E402

DAY = date(2026, 11, 17)
MM, M30 = "NIFTY MIDCAP150 MOMENTUM 50", "NIFTY200 MOMENTUM 30"


def levels(days=1400, seed=11):
    """Three menu indexes from Jan 2005: enough for the training window and a year of exam."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2005-01-03", periods=days).date
    market = 0.012 * rng.standard_normal(days)
    cols = {BENCHMARK: 0.0004 + market, MM: 0.0009 + 1.1 * market + 0.006 * rng.standard_normal(days),
            M30: 0.0007 + market + 0.005 * rng.standard_normal(days)}
    return pd.DataFrame({k: 100 * np.exp(np.cumsum(v)) for k, v in cols.items()}, index=idx)


def reading(**change):
    base = {"plan": "p", "avg_month_pct": 1.8, "cagr_pct": 22.0, "worst_fall_pct": 30.0, "years_beating_pct": 70.0,
            "alpha_t": 3.1, "alpha_first_half_pct": 6.0, "alpha_second_half_pct": 5.0, "bench_cagr_pct": 12.0,
            "invested_pct": 80.0}
    return {**base, **change}


def test_the_format_example_is_a_plan():
    c = ad.check_proposal(ad.FORMAT, idea="Calm momentum!", day=DAY, version=2)
    assert c.document["name"] == "A-20261117-calm-momentum-v2" == c.plan.name
    assert c.plan.universe == (MM, M30) and c.plan.lookbacks == (6, 12) and c.plan.decide == "monthly"
    assert (c.plan.own_average, c.plan.positive_months, c.plan.target_volatility) == (200, 12, 15.0)


def test_the_dry_run_plan_is_valid():
    assert ad.check_proposal(ad.DRY_YAML, idea="dry", day=DAY, version=1).plan.decide == "weekly"


def test_a_proposal_may_only_use_the_menu_it_was_given():
    with pytest.raises(PlanError, match="not on the menu"):
        ad.check_proposal(ad.FORMAT, idea="i", day=DAY, version=1, menu=(BENCHMARK, MM))
    with pytest.raises(PlanError, match="not valid YAML"):
        ad.check_proposal("a: [1", idea="i", day=DAY, version=1)


def test_a_reading_that_clears_everything_passes():
    assert all(c.ok for c in ad.judge(reading(), []))


@pytest.mark.parametrize("change,name,match", [
    ({"avg_month_pct": 1.4}, "return", "averaged 1.40% a month"),
    ({"worst_fall_pct": 36.0}, "fall", "fell 36.0%"),
    ({"years_beating_pct": 40.0}, "years", "only 40%"),
    ({"alpha_t": 2.5}, "luck", "could be luck"),
    ({"alpha_t": None}, "luck", "could be luck"),
    ({"alpha_second_half_pct": -0.5}, "halves", "missing in one half"),
])
def test_the_judge_says_which_check_failed(change, name, match):
    failed = [c for c in ad.judge(reading(**change), []) if not c.ok]
    assert [c.name for c in failed] == [name]
    assert match in failed[0].detail


def test_a_plan_worse_on_both_counts_than_a_paper_book_fails():
    book = reading(plan="the book", cagr_pct=23.0, worst_fall_pct=29.0)
    failed = [c for c in ad.judge(reading(), [book]) if not c.ok]
    assert [c.name for c in failed] == ["better"] and "the book" in failed[0].detail
    safer = reading(plan="safer book", cagr_pct=23.0, worst_fall_pct=31.0)      # more return, deeper fall
    assert all(c.ok for c in ad.judge(reading(), [safer]))


def test_a_saved_verdict_reads_back_as_it_was_written():
    """numpy's False saved through json.dumps(default=str) became the string "False" - true when read."""
    r = reading(avg_month_pct=np.float64(1.0), worst_fall_pct=np.float64(50.0))
    saved = json.loads(json.dumps(ad.AllocSummary(r, ad.judge(r, []), None).as_dict(), default=str))
    assert saved["passes"] is False and saved["checks"]["return"] is False and saved["checks"]["luck"] is True
    assert ad.near_sound(reading(alpha_t=np.float64(3.0))) is True


def test_every_failure_is_reported_not_only_the_first():
    s = ad.AllocSummary(reading(avg_month_pct=1.0, worst_fall_pct=50.0), ad.judge(
        reading(avg_month_pct=1.0, worst_fall_pct=50.0), []), None)
    assert not s.passes and s.why_not.startswith("averaged 1.00%")
    assert len(s.as_dict()["failed"]) == 2


def test_neighbours_move_each_number_about_a_fifth():
    assert ad.neighbours(Plan("p", (MM,), 1, rank_by="none")) == []           # nothing to fit, nothing to nudge
    plan = Plan("p", (MM, M30), 1, lookbacks=(6, 12), own_average=200, target_volatility=15.0)
    others = ad.neighbours(plan)
    assert {o.own_average for o in others} == {160, 200, 240}
    assert {o.target_volatility for o in others} >= {12.8, 17.2}
    assert {o.lookbacks for o in others} >= {(5, 11), (7, 13)}
    assert {o.hold for o in others} == {1, 2}
    assert all(sum(getattr(o, f) != getattr(plan, f) for f in ("own_average", "target_volatility",
               "volatility_sessions", "lookbacks", "hold")) == 1 for o in others)   # one change at a time


def test_closest_shows_the_gap_from_both_sides():
    rows = [{"title": "safe", "avg_month_pct": 1.3, "worst_fall_pct": 28.0, "sound": True},
            {"title": "safer but poorer", "avg_month_pct": 1.1, "worst_fall_pct": 20.0, "sound": True},
            {"title": "rich", "avg_month_pct": 1.7, "worst_fall_pct": 39.0, "sound": True},
            {"title": "richer but deeper", "avg_month_pct": 1.9, "worst_fall_pct": 60.0, "sound": True},
            {"title": "lucky", "avg_month_pct": 3.0, "worst_fall_pct": 10.0, "sound": False}]
    board = ad.closest(rows)
    assert board["within_fall"]["title"] == "safe" and board["at_target"]["title"] == "rich"
    assert ad.closest([]) == {"within_fall": None, "at_target": None}


def test_the_closest_block_says_what_a_plan_with_both_numbers_still_fails():
    """15 Nov 2026: one plan sat in both rows, the note said "a pass needs one plan in both rows"."""
    both = {"title": "Near miss", "avg_month_pct": 1.5, "worst_fall_pct": 33.3, "sound": True,
            "failed": ["its edge could be luck: t = 2.56 (needs 2.6)"]}
    [block] = ad.closest_blocks(ad.closest([both]))
    assert block.rows == (("both numbers", "1.50%/mo, fall 33%"),)
    assert "BOTH of your numbers" in block.note and "not a pass yet because: its edge could be luck" in block.note
    apart = ad.closest([{**both, "avg_month_pct": 1.3, "title": "Safe"},
                        {**both, "worst_fall_pct": 40.0, "title": "Rich"}])
    [block] = ad.closest_blocks(apart)
    assert [r[0] for r in block.rows] == ["fall <= 35%", ">= 1.5%/mo"] and "A pass needs one plan with both" in block.note
    assert ad.closest_blocks(ad.closest([])) == []


def test_a_plan_already_tested_is_refused_instead_of_costing_a_version():
    lab = ad.Lab(levels())

    class Repeats:
        """Sends the dry-run plan every time, as the AI did on 15 Nov 2026."""
        def __init__(self):
            self.errors = []

        def propose(self, *, error=None, **_):
            self.errors.append(error)
            return {**ad.DryBrain().propose(), "title": f"Try {len(self.errors)}"}

        def review(self, **_):
            return {"why_failed": "", "why_worked": "", "decision": "next_version", "lessons": "L"}

    brain = Repeats()
    outcome, _, _ = ad.run_day(brain=brain, lab=lab, day=DAY, max_versions=2, budget_seconds=600,
                               echo=lambda *a, **k: None)
    assert [v.valid for v in outcome.versions] == [True, False]          # tested once; the repeat never ran
    assert "already tested" in outcome.versions[1].error
    assert any(e and "already tested" in e for e in brain.errors)       # and the AI was told why

    # ...across days too: the ledger's plans and the reference plans count as tested.
    row = ad.version_rows(DAY, [outcome.versions[0]])[0]
    seen = ad.tested_before([row], lab.menu)
    assert ad.same_plan(outcome.versions[0].checked.plan) in seen and len(seen) == len(ad.REFERENCE) + 1
    outcome, _, _ = ad.run_day(brain=Repeats(), lab=lab, day=DAY, max_versions=1, budget_seconds=600, seen=seen,
                               echo=lambda *a, **k: None)
    assert [v.valid for v in outcome.versions] == [False]


def test_the_lab_measures_the_reference_plans_it_has_data_for():
    lab = ad.Lab(levels())
    assert lab.menu == (BENCHMARK, M30, MM) or set(lab.menu) == {BENCHMARK, MM, M30}
    assert lab.train[0] == ad.TRAIN_FROM and (lab.exam[1] - lab.exam[0]).days <= 365
    names = [r["plan"] for r in lab.reference()]
    assert "Hold NIFTY 50" in names and not any("factor indexes" in n for n in names)   # those need 8 indexes
    assert {m["index"] for m in lab.menu_table()} == set(lab.menu)


def test_every_allowed_setting_can_be_computed_on_the_first_training_day():
    """A plan must be in position from day one, not waiting a month for one more day of history."""
    from research.alloc import _months_back
    first_level = ad.HISTORY_FROM
    assert _months_back(ad.TRAIN_FROM, 12) >= first_level          # the longest look-back
    sessions = pd.bdate_range(first_level, ad.TRAIN_FROM)           # more sessions than NSE really had
    assert len(sessions) - 12 >= 250                                # the longest average, holidays allowed for
    lab = ad.Lab(levels())
    from research.alloc import run
    r = run(lab.levels, Plan("p", (MM,), 1, rank_by="none"), start=lab.train[0], end=lab.train[1])
    assert r.exposure.iloc[0] == pytest.approx(1.0)


def test_an_index_that_starts_late_is_not_offered():
    f = levels()
    f.loc[f.index < date(2007, 1, 1), M30] = np.nan
    assert M30 not in ad.Lab(f).menu


def version(lab, yaml_text=ad.DRY_YAML, title="T"):
    checked = ad.check_proposal(yaml_text, idea=title, day=DAY, version=1, menu=lab.menu)
    return SimpleNamespace(idea_no=1, version_no=1, title=title, description="", hypothesis="", change_note="",
                           valid=True, error=None, checked=checked, summary=lab.test(checked),
                           review={"decision": "stop", "lessons": "A lesson."})


def easy(monkeypatch):
    """Lower every bar so the dry-run plan passes: the PATH after a pass is what is under test."""
    for name, value in (("MIN_MONTH_PCT", -100.0), ("MAX_FALL_PCT", 100.0), ("MIN_YEARS_BEATING_PCT", 0.0),
                        ("MIN_T", -100.0), ("NEIGHBOUR_T", -100.0), ("NEIGHBOUR_FALL_PCT", 100.0),
                        ("EXAM_BEHIND_PTS", 1000.0)):
        monkeypatch.setattr(ad, name, value)
    monkeypatch.setattr(ad, "judge", lambda r, inc: [c for c in JUDGE(r, []) if c.name != "halves"])


JUDGE = ad.judge


def test_a_day_on_which_nothing_passes_opens_no_exam():
    lab = ad.Lab(levels())
    outcome, final, exam = ad.run_day(brain=ad.DryBrain(), lab=lab, day=DAY, max_versions=3, budget_seconds=600,
                                      echo=lambda *a, **k: None)
    assert len(outcome.versions) == 1 and final is not None and exam is None
    subject, blocks = ad.message(DAY, outcome, final, exam, board=ad.closest([]), dry_run=True)
    assert "NOTHING PASSED" in blocks[0].note and subject.startswith("Index allocation research 2026-11-17")
    assert blocks[1].rows[-1] == ("result", "FAILS") and blocks[1].note.startswith("Why: ")


def test_a_pass_is_examined_and_then_paper_traded(tmp_path, monkeypatch):
    easy(monkeypatch)
    lab = ad.Lab(levels())
    outcome, final, exam = ad.run_day(brain=ad.DryBrain(), lab=lab, day=DAY, max_versions=3, budget_seconds=600,
                                      echo=lambda *a, **k: None)
    assert final[1].passes and final[1].nearby["tested"] > 0 and exam["ok"]
    assert exam["strategy"]["start"] == lab.exam[0]
    entry, said = ad.promote(DAY, final[0], exam, root=tmp_path)
    assert entry["start"] == "2026-11-18" and "PAPER-TRADED from 2026-11-18" in said
    stored = json.loads((tmp_path / ad.PROMOTED).read_text(encoding="utf-8"))
    assert [r["key"] for r in stored] == ["alloc-20261117-1"]
    again, said = ad.promote(DAY, final[0], exam, root=tmp_path)
    assert again is None and "already paper-traded" in said
    _, blocks = ad.message(DAY, outcome, final, exam, board=ad.closest([]), promoted="now PAPER-TRADED from X")
    assert "A PLAN PASSED" in blocks[0].note and "PAPER-TRADED" in blocks[0].note

    # ...and the paper books pick it up, replaying the same plan from its start day.
    books = pb.promoted_books(tmp_path / ad.PROMOTED)
    assert [(b.key, b.kind, b.start) for b in books] == [("alloc-20261117-1", "alloc", date(2026, 11, 18))]
    assert books[0].plan == dataclass_without_name(final[0].checked.plan, books[0].plan.name)
    assert pb.indexes_needed(books) == sorted({BENCHMARK, MM})


def dataclass_without_name(plan, name):
    import dataclasses
    return dataclasses.replace(plan, name=name)


def test_the_paper_list_is_capped(tmp_path, monkeypatch):
    easy(monkeypatch)
    monkeypatch.setattr(ad, "MAX_PROMOTED", 1)
    lab = ad.Lab(levels())
    v = version(lab)
    exam = lab.exam_row(DAY, v)
    assert ad.promote(DAY, v, exam, root=tmp_path)[0] is not None
    other = version(lab, ad.DRY_YAML.replace("percent: 15", "percent: 20"), title="Another")
    entry, said = ad.promote(DAY, other, lab.exam_row(DAY, other), root=tmp_path)
    assert entry is None and "NOT paper-traded" in said


def test_a_failed_exam_year_vetoes_the_pass(monkeypatch):
    easy(monkeypatch)
    monkeypatch.setattr(ad, "EXAM_BEHIND_PTS", -1000.0)
    lab = ad.Lab(levels())
    outcome, final, exam = ad.run_day(brain=ad.DryBrain(), lab=lab, day=DAY, max_versions=1, budget_seconds=600,
                                      echo=lambda *a, **k: None)
    assert exam["ok"] is False and "behind NIFTY 50 in the exam year" in exam["why_not"]
    _, blocks = ad.message(DAY, outcome, final, exam, board=ad.closest([]))
    assert "FAILED its exam year" in blocks[0].note and "Not paper-traded" in blocks[0].note


def test_a_promoted_plan_becomes_an_incumbent_the_next_plan_must_beat(tmp_path, monkeypatch):
    lab = ad.Lab(levels())
    doc = ad.check_proposal(ad.DRY_YAML, idea="x", day=DAY, version=1).document
    lab2 = ad.Lab(levels(), [{"title": "Promoted one", "document": doc}])
    assert len(lab2.incumbents()) == len(lab.incumbents()) + 1
    assert lab2.incumbents()[-1]["plan"] == "Promoted one"


def test_records_round_trip(tmp_path):
    lab = ad.Lab(levels())
    v = version(lab)
    ad._append(ad._path(ad.LEDGER, tmp_path), ad.version_rows(DAY, [v]))
    assert ad.notes(tmp_path) == [{"day": "2026-11-17", "idea_title": "T", "lessons": "A lesson."}]
    tried = ad.ideas_tried(tmp_path)
    assert len(tried) == 1 and tried[0].startswith("T: ") and "volatility_target" in tried[0]
    rows = ad.board_rows(lab.reference(), ad.read_ledger(tmp_path), [v])
    assert [r["title"] for r in rows][-2:] == ["T", "T"]
    note = ad.write_note(DAY, [v], tmp_path).read_text(encoding="utf-8")
    assert note.startswith("# Index allocation research notes — 2026-11-17") and "**Lessons:** A lesson." in note


def test_the_prompts_carry_the_landscape_and_never_the_exam():
    lab = ad.Lab(levels())
    v = version(lab)
    text = ad.propose_prompt(notes=[{"day": "2026-11-16", "idea_title": "Old", "lessons": "Learned."}],
                             ideas_tried=["Old: failed"], reference=lab.reference(), menu=lab.menu_table(),
                             board=ad.closest(ad.board_rows(lab.reference(), [])), previous=v.summary,
                             change_hint="slower", error=None)
    for must in ("INDEX ALLOCATION", "Hold NIFTY 50", MM, "Learned.", "Old: failed", "slower", "version: alloc/1",
                 "at least 2.6"):
        assert must in text
    exam = lab.exam_row(DAY, v)
    assert str(exam["strategy"]["end_value"]) not in text and str(lab.exam[1]) not in text
    review = ad.review_prompt(summary=v.summary, version=1, versions_left=0)
    assert 'MUST be "stop"' in review and str(lab.exam[1]) not in review


def test_main_runs_a_whole_dry_day_and_saves_it(tmp_path, monkeypatch, capsys):
    """The WHOLE of main(), as the workflow runs it bar the AI: a crash here cost two weeks in October."""
    levels().to_parquet(tmp_path / "levels.parquet")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ad, "LEVELS", tmp_path / "levels.parquet")
    assert ad.main(["--dry-run", "--skip-download"]) == 0
    out = capsys.readouterr().out
    assert "Index allocation research" in out and "Verdict:" in out and "How the day ended" in out
    rows = ad.read_ledger(tmp_path)
    assert len(rows) == 1 and rows[0]["valid"] and rows[0]["training"]["plan"]["avg_month_pct"] is not None
    assert (tmp_path / ad.DIR / f"{rows[0]['day']}.md").exists()
    assert not (tmp_path / ad.PROMOTED).exists()                   # a dry run never starts a paper book


def test_main_without_saving_writes_nothing(tmp_path, monkeypatch, capsys):
    levels().to_parquet(tmp_path / "levels.parquet")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ad, "LEVELS", tmp_path / "levels.parquet")
    assert ad.main(["--dry-run", "--skip-download", "--no-save"]) == 0
    assert not (tmp_path / "research").exists()


def test_main_with_a_pass_starts_a_paper_book(tmp_path, monkeypatch, capsys):
    easy(monkeypatch)
    levels().to_parquet(tmp_path / "levels.parquet")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ad, "LEVELS", tmp_path / "levels.parquet")
    # A real (non-dry) day with the AI replaced by the fixed plan.
    import research.brain as brain
    monkeypatch.setattr(ad, "AllocBrain", lambda *a, **k: ad.DryBrain())
    monkeypatch.setattr(brain, "Claude", lambda: None)
    assert ad.main(["--skip-download"]) == 0
    out = capsys.readouterr().out
    assert "A PLAN PASSED" in out and "PAPER-TRADED from" in out
    assert len(json.loads((tmp_path / ad.PROMOTED).read_text(encoding="utf-8"))) == 1
    assert len((tmp_path / ad.DIR / ad.EXAMS).read_text(encoding="utf-8").splitlines()) == 1


def test_a_pass_is_added_even_when_other_research_tracks_share_the_file(tmp_path):
    """19 Nov 2026: a rotation row (no plan document) in promoted.json crashed the day after its research."""
    import json
    from types import SimpleNamespace
    from research import alloc_day as ad
    path = tmp_path / ad.PROMOTED
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps([{"kind": "rotation", "key": "r", "variant": {}, "day": "2026-11-10"}]),
                    encoding="utf-8")
    reading = {"avg_month_pct": 1.6, "cagr_pct": 20.0, "bench_cagr_pct": 12.0, "worst_fall_pct": 27.0,
               "alpha_t": 3.2, "start": "2006-04-10", "end": "2025-11-18"}
    version = SimpleNamespace(title="P", checked=SimpleNamespace(document={"version": "alloc/1", "name": "x"}),
                              summary=SimpleNamespace(reading=reading))
    entry, said = ad.promote(DAY, version, None, root=tmp_path)
    assert entry is not None and "PAPER-TRADED" in said
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert [r.get("kind", "alloc") for r in rows] == ["rotation", "alloc"]
    assert [r["key"] for r in ad.read_promoted(tmp_path)] == [entry["key"]]


def test_a_failure_while_paper_trading_a_pass_is_reported_not_raised():
    from types import SimpleNamespace
    from research.blocks import promote_each

    def boom(v):
        raise KeyError("document")

    said = promote_each([SimpleNamespace(idea_no=1, version_no=1)], boom)
    assert said.startswith("v1.1 could NOT be added to paper trading")
