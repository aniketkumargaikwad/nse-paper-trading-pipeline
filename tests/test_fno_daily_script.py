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


def test_a_recent_day_nse_answers_with_a_web_page_does_not_fail_the_job(monkeypatch, tmp_path):
    """20 Nov 2026: Friday's run started after midnight IST and asked NSE for Saturday."""
    from fno import nse_files, store
    today = fno_daily.datetime.now(fno_daily.IST).date()
    monkeypatch.setattr(store, "root_dir", lambda: tmp_path)
    monkeypatch.setattr(store, "load_ledger", lambda root: {})

    def blocked(day, session):
        raise nse_files.NseFileError(f"{day}: HTTP 200, 19607 bytes, not a zip")

    monkeypatch.setattr(nse_files, "download", blocked)
    monkeypatch.setattr(fno_daily, "PAUSE_SECONDS", 0)
    monkeypatch.setattr(fno_daily, "RETRIES", 1)
    monkeypatch.setattr(store, "write_days", lambda root, pending: None)
    assert fno_daily.main(["--from", today.isoformat(), "--skip-participants", "--skip-vix"]) == 0
    old = date(2020, 1, 6)
    assert fno_daily.main(["--from", old.isoformat(), "--to", old.isoformat(), "--skip-participants", "--skip-vix"]) == 1
