"""The daily note a later run reads - training lessons only."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.journal import (  # noqa: E402
    entries_from_versions,
    journal_markdown,
    note_rows,
    write_journal,
)
from research.loop import VersionAttempt  # noqa: E402

LOCKED_WORDS = ("locked", "lakh", "verdict", "just holding")


def entries():
    return [
        {"idea_title": "dip buyer", "outcome_training": "137 of 1177 profitable",
         "lessons": "shallow dips beat deep ones"},
        {"idea_title": "gap fade", "outcome_training": "lost money before fees",
         "lessons": "gaps close less often than expected"},
    ]


def version(idea_no, title, lessons, valid=True, reviewed=True, summary="s"):
    return VersionAttempt(
        idea_no=idea_no, version_no=1, title=title, description="d", hypothesis="h",
        change_note="", valid=valid, summary=summary,
        review={"lessons": lessons} if reviewed else {},
    )


def test_the_note_lists_every_idea_with_its_lesson():
    text = journal_markdown(date(2026, 9, 12), entries())
    assert "dip buyer" in text and "gap fade" in text
    assert "shallow dips beat deep ones" in text


def test_the_note_is_dated_so_a_later_run_can_order_them():
    assert "2026-09-12" in journal_markdown(date(2026, 9, 12), entries())


def test_the_note_never_carries_the_locked_year():
    text = journal_markdown(date(2026, 9, 12), entries()).lower()
    assert not any(word in text for word in LOCKED_WORDS)


def test_note_rows_are_tagged_with_the_run_and_day():
    rows = note_rows("run-1", date(2026, 9, 12), entries())
    assert all(r["run_id"] == "run-1" and r["day"] == date(2026, 9, 12) for r in rows)
    assert [r["idea_title"] for r in rows] == ["dip buyer", "gap fade"]


def test_a_day_with_no_entries_still_writes_a_note():
    text = journal_markdown(date(2026, 9, 12), [])
    assert "2026-09-12" in text and "no" in text.lower()


def test_the_note_is_written_where_a_later_day_will_find_it(tmp_path):
    path = write_journal(date(2026, 9, 12), entries(), root=tmp_path)
    assert path == tmp_path / "research" / "journal" / "2026-09-12.md"
    assert "dip buyer" in path.read_text(encoding="utf-8")


def test_one_entry_per_idea_not_per_version():
    versions = [
        version(1, "dip buyer", "first try"),
        version(1, "dip buyer", "shallower dips did better"),
        version(2, "gap fade", "gaps rarely closed"),
    ]
    got = entries_from_versions(versions)
    assert [e["idea_title"] for e in got] == ["dip buyer", "gap fade"]
    assert got[0]["lessons"] == "shallower dips did better"


def test_a_version_that_never_ran_leaves_no_entry():
    assert entries_from_versions([version(1, "broken", "", valid=False, reviewed=False)]) == []
