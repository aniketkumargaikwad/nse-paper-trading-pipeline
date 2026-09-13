"""The day's wiring that can be checked without a sweep."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.run_day import write_run_id  # noqa: E402


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
