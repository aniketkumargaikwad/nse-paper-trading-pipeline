"""Which days the fill script asks NSE for, and when 'no file' is final."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fno_daily  # noqa: E402


def test_days_already_in_the_ledger_are_not_asked_for_again():
    ledger = {date(2026, 9, 29): "ok", date(2026, 9, 30): "no_session"}
    days = fno_daily.plan_days(date(2026, 9, 28), date(2026, 10, 1), ledger)
    assert days == [date(2026, 9, 28), date(2026, 10, 1)]


def test_every_calendar_day_is_tried_because_saturdays_can_be_sessions():
    days = fno_daily.plan_days(date(2024, 1, 19), date(2024, 1, 21), {})
    assert days == [date(2024, 1, 19), date(2024, 1, 20), date(2024, 1, 21)]


def test_a_missing_recent_file_is_not_yet_proof_of_no_session():
    """NSE publishes in the evening, sometimes late: only after three days
    does a 404 become 'no session' in the ledger."""
    today = date(2026, 10, 3)
    assert not fno_daily.settled(date(2026, 10, 1), today)
    assert fno_daily.settled(date(2026, 9, 29), today)
