"""Budget, RBI policy and election-result days."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.events import Events  # noqa: E402

EVENTS = Events.load()


def test_known_budget_and_election_days_are_events():
    assert EVENTS.on(date(2020, 2, 1)) == ["budget"]
    assert "election_result" in EVENTS.on(date(2024, 6, 4))


def test_an_ordinary_day_has_no_event():
    assert EVENTS.on(date(2024, 6, 5)) == []


def test_every_rbi_date_has_a_source():
    rbi = [r for r in EVENTS.rows if r["kind"] == "rbi_policy"]
    assert len(rbi) >= 40        # ~6 a year since Oct 2016, plus the unscheduled ones
    for row in rbi:
        assert row.get("source"), row


def test_the_unscheduled_rbi_decisions_are_there():
    for d in (date(2020, 3, 27), date(2020, 5, 22), date(2022, 5, 4)):
        assert "rbi_policy" in EVENTS.on(d), d


def test_events_between_two_dates():
    assert date(2019, 7, 5) in EVENTS.between(date(2019, 7, 1), date(2019, 7, 31))
