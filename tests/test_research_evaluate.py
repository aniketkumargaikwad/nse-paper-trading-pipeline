"""The pure parts of the hand-run evaluation command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.evaluate import strategy_document  # noqa: E402


def test_bookkeeping_fields_are_stripped_before_parsing():
    docs = [{"name": "A", "status": "valid", "raw_source": "x", "validation_errors": [], "timeframe": "day"}]
    assert strategy_document(docs, "A") == {"name": "A", "timeframe": "day"}


def test_a_missing_strategy_names_the_available_ones():
    with pytest.raises(KeyError, match="B"):
        strategy_document([{"name": "B", "status": "valid"}], "A")


def test_a_draft_is_refused():
    with pytest.raises(ValueError, match="draft"):
        strategy_document([{"name": "A", "status": "draft"}], "A")


def test_a_document_without_a_status_is_treated_as_valid():
    """Older rows predate the status column; they must still evaluate."""
    assert strategy_document([{"name": "A", "timeframe": "day"}], "A") == {
        "name": "A", "timeframe": "day"
    }
