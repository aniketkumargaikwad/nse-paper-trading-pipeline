"""The options research records: what later days learn from, and the exams they never see."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.ai import ledger  # noqa: E402
from fno.ai.summary import OptionsSummary  # noqa: E402

ROW = {"underlying": "NIFTY", "avg_month_pct": 0.4, "trades_per_month": 12.0, "worst_day_pct": -2.0,
       "edge_t": 0.5, "passes": False, "why_not": "its lead over holding could be luck"}


def version(idea_no, version_no, title, lessons="", valid=True, decision="next_version"):
    checked = SimpleNamespace(document={"name": f"O-20261021-x-v{version_no}", "mode": "intraday"})
    return SimpleNamespace(idea_no=idea_no, version_no=version_no, title=title, description="d",
                           hypothesis="h", change_note="", valid=valid,
                           error=None if valid else "bad leg", checked=checked if valid else None,
                           summary=OptionsSummary("intraday", [dict(ROW)]) if valid else None,
                           review={"lessons": lessons, "decision": decision} if valid else {})


def test_versions_round_trip_and_feed_the_next_day(tmp_path):
    day = date(2026, 10, 21)
    rows = ledger.version_rows(day, [version(1, 1, "calm straddle", "too few calm days"),
                                     version(1, 2, "calm straddle", "stop too tight"),
                                     version(2, 1, "broken idea", valid=False)])
    ledger.append_versions(rows, root=tmp_path)
    back = ledger.read_ledger(root=tmp_path)
    assert [r["version_no"] for r in back] == [1, 2, 1]
    assert back[2]["valid"] is False and back[2]["error"] == "bad leg"

    notes = ledger.notes(root=tmp_path)
    assert notes == [{"day": "2026-10-21", "idea_title": "calm straddle", "lessons": "stop too tight"}]
    ideas = ledger.ideas_tried(root=tmp_path)
    assert len(ideas) == 2 and ideas[0].startswith("O-20261021-x-v2")
    assert "+0.40%/month" in ideas[0] and "stop too tight" in ideas[0]


def test_an_exam_is_kept_apart_and_never_read_back_for_the_ai(tmp_path):
    ledger.append_versions(ledger.version_rows(date(2026, 10, 21), [version(1, 1, "idea", "l")]),
                           root=tmp_path)
    ledger.append_exam({"day": "2026-10-21", "strategy": "O-x", "avg_month_pct": 987.65}, root=tmp_path)
    for line in ledger.ideas_tried(root=tmp_path) + [str(n) for n in ledger.notes(root=tmp_path)]:
        assert "987.65" not in line
    assert "987.65" not in "".join(map(str, ledger.read_ledger(root=tmp_path)))
    assert (tmp_path / ledger.DIR / ledger.EXAMS).exists()


def test_the_journal_note_has_training_lessons_only(tmp_path):
    path = ledger.write_note(date(2026, 10, 21), [version(1, 1, "calm straddle", "too few calm days")],
                             root=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name == "2026-10-21.md" and "calm straddle" in text and "too few calm days" in text
    assert "NIFTY +0.40%/month" in text


def test_no_history_is_an_empty_first_day(tmp_path):
    assert ledger.read_ledger(root=tmp_path) == [] and ledger.notes(root=tmp_path) == []
