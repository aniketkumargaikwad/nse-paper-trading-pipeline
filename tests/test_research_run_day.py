"""The day's wiring that can be checked without a sweep."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.run_day import write_fallback, write_run_id  # noqa: E402


def test_the_run_id_is_written_for_the_next_step(tmp_path):
    target = tmp_path / "deep" / "run-id.txt"
    write_run_id(str(target), "abc-123")
    assert target.read_text(encoding="utf-8") == "abc-123"


def test_no_path_means_no_file(tmp_path):
    write_run_id("", "abc-123")
    assert list(tmp_path.iterdir()) == []


def test_a_run_that_stored_nothing_writes_nothing(tmp_path):
    target = tmp_path / "run-id.txt"
    write_run_id(str(target), None)
    assert not target.exists()


# --- a day the database would not take ---------------------------------------


def test_a_refused_write_leaves_the_whole_day_on_disk(tmp_path):
    target = tmp_path / "out" / "research-fallback.json"
    payload = {"run": {"status": "completed"}, "versions": [{"idea_no": 1}], "notes": []}
    write_fallback(str(target), payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload


def test_dates_survive_the_dump(tmp_path):
    """A payload full of dates must not fail to serialise on the way out."""
    target = tmp_path / "research-fallback.json"
    write_fallback(str(target), {"run": {"data_end": date(2026, 7, 31)}})
    assert "2026-07-31" in target.read_text(encoding="utf-8")


def test_no_fallback_path_means_no_file(tmp_path):
    write_fallback("", {"run": {}})
    assert list(tmp_path.iterdir()) == []
