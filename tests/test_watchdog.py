"""The watchdog (scripts/watchdog.py): a skipped day is started, a failure re-run once."""

from __future__ import annotations

import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import watchdog as wd  # noqa: E402

PAPER = next(j for j in wd.JOBS if j.file == "paper-books.yml")
RESEARCH = next(j for j in wd.JOBS if j.file == "research.yml")
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=timezone.utc)          # a Tuesday


def run(created, *, event="schedule", status="completed", conclusion="success", attempt=1, id_=1):
    return {"id": id_, "created_at": created, "event": event, "status": status, "conclusion": conclusion,
            "run_attempt": attempt}


def test_the_jobs_match_the_workflow_schedules():
    for job in wd.JOBS:
        text = (ROOT / ".github" / "workflows" / job.file).read_text(encoding="utf-8")
        minute, hour, days = re.search(r'cron: "(\d+) (\d+) \* \* ([\d,\-]+)"', text).groups()
        if "-" in days:
            a, b = map(int, days.split("-"))
            cron_days = set(range(a, b + 1))
        else:
            cron_days = set(map(int, days.split(",")))
        assert (int(minute), int(hour)) == (job.minute, job.hour), job.file
        assert {(d - 1) % 7 for d in cron_days} == set(job.weekdays), job.file    # cron Sunday = 0


def test_a_late_run_is_not_missing():
    """23 Nov 2026: Monday's 15:17 UTC paper-books run started at 02:10 UTC on Tuesday."""
    runs = [run("2026-11-20T22:14:21Z"), run("2026-11-24T02:10:50Z")]   # Friday's and Monday's, both late
    assert wd.check(PAPER, runs, NOW) == []


def test_a_skipped_day_is_started():
    runs = [run("2026-11-20T22:14:21Z")]                                # Monday's never came
    [(action, _, words)] = wd.check(PAPER, runs, NOW)
    assert action == "dispatch" and "Mon 23 Nov" in words


def test_a_failed_run_is_rerun_once_then_left_to_its_own_alert():
    now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)            # Sunday; Saturday's research failed
    first = [run("2026-11-21T11:34:22Z", conclusion="failure", id_=7)]
    [(action, run_id, words)] = wd.check(RESEARCH, first, now)
    assert (action, run_id) == ("rerun", 7) and "Sat 21 Nov" in words
    second = [run("2026-11-21T11:34:22Z", conclusion="failure", attempt=2, id_=7)]
    assert wd.check(RESEARCH, second, now) == []
    running = [run("2026-11-21T11:34:22Z", status="in_progress", conclusion=None, id_=7)]
    assert wd.check(RESEARCH, running, now) == []


def test_too_recent_to_judge_is_left_alone():
    now = datetime(2026, 11, 24, 20, 0, tzinfo=timezone.utc)            # 5 h after today's 15:17
    runs = [run("2026-11-24T02:10:50Z")]                                 # Monday's, late
    assert wd.check(PAPER, runs, now) == []


def test_it_acts_and_re_enables_a_switched_off_schedule():
    calls = []

    def api(method, path, body=None):
        calls.append((method, path))
        if path.endswith("/runs?per_page=40"):
            return {"workflow_runs": []}
        return {"state": "disabled_inactivity" if "paper-books" in path else "active"}

    said = wd.run(api, NOW, log=lambda *a: None)
    assert ("PUT", "/actions/workflows/paper-books.yml/enable") in calls
    assert ("POST", "/actions/workflows/paper-books.yml/dispatches") in calls
    assert any("switched off" in s for s in said)
    assert sum(1 for m, _ in calls if m == "POST") == len([s for s in said if "Started it now" in s])
