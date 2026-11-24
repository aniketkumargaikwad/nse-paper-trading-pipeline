"""Make sure every scheduled job actually ran: start the ones GitHub skipped, retry a failure once.

    python scripts/watchdog.py            # on GitHub (.github/workflows/watchdog.yml), every 2 hours
    python scripts/watchdog.py --dry-run  # say what it would do, do nothing

WHY THIS EXISTS
---------------
GitHub's schedule is best-effort. Between 17 and 24 Nov 2026 jobs started 5 to
11 hours late (Friday's 20:47 IST paper-books run began at 03:44 IST on
Saturday), and GitHub documents that a scheduled run can be dropped outright
and that a public repository's schedules are switched off after 60 days
without activity. Each of those means a day without its Telegram message, and
nothing said so.

So, for every job and every day it was due in the last two days, once a job is
GRACE_HOURS past its scheduled time:
  - no run at all for that day     -> start it now, and tell the alerts bot
  - its run failed, first attempt  -> re-run it once, and tell the alerts bot
  - its run failed again           -> nothing more (the job's own alert step already said so)
  - the job's schedule is disabled -> switch it back on, and tell the alerts bot

Standard library only (like scripts/alert_failure.py), and it never fails its
own job for a problem it found - only for one it could not check.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

IST = timezone(timedelta(hours=5, minutes=30))
GRACE_HOURS = 16          # paper-books ran 11 h late on 23 Nov 2026; never treat that as missing
LOOKBACK_DAYS = 2
FAILED = ("failure", "timed_out", "startup_failure")


@dataclass(frozen=True)
class Job:
    file: str
    name: str                       # how the owner knows it
    weekdays: tuple[int, ...]       # UTC weekdays it is scheduled on, Monday = 0
    hour: int                       # UTC
    minute: int


# Must match the `cron:` lines in .github/workflows/ (tests/test_watchdog.py checks).
JOBS = (
    Job("research.yml", "index research", (1, 3, 5), 3, 33),
    Job("fno-research.yml", "options research", (0, 2, 4), 3, 33),
    Job("fno-daily.yml", "options data", (0, 1, 2, 3, 4), 13, 37),
    Job("momentum-paper.yml", "momentum paper account", (0, 1, 2, 3, 4), 13, 52),
    Job("paper-books.yml", "paper books (and the Friday weekly message)", (0, 1, 2, 3, 4), 15, 17),
    Job("recover-prices.yml", "weekly price recovery", (6,), 20, 17),
)


def due_times(job: Job, now: datetime) -> list[datetime]:
    """Scheduled times in the look-back window that are at least GRACE_HOURS old, oldest first."""
    out = []
    for back in range(LOOKBACK_DAYS + 1, -1, -1):
        day = (now - timedelta(days=back)).date()
        at = datetime(day.year, day.month, day.day, job.hour, job.minute, tzinfo=timezone.utc)
        if at.weekday() in job.weekdays and timedelta(hours=GRACE_HOURS) <= now - at <= timedelta(
                days=LOOKBACK_DAYS):
            out.append(at)
    return out


def next_time(job: Job, after: datetime) -> datetime:
    t = after + timedelta(days=1)
    while t.weekday() not in job.weekdays:
        t += timedelta(days=1)
    return t


def _when(t: datetime) -> str:
    return t.astimezone(IST).strftime("%a %d %b")


def check(job: Job, runs: list[dict], now: datetime) -> list[tuple[str, Any, str]]:
    """What to do for one job: (action, run id or None, words for the alert)."""
    actions = []
    for at in due_times(job, now):
        until = min(next_time(job, at), now)
        mine = [r for r in runs if r.get("event") in ("schedule", "workflow_dispatch")
                and at - timedelta(minutes=10) <= _time(r["created_at"]) < until]
        if not mine:
            actions.append(("dispatch", None, f"The {job.name} job did not start on {_when(at)} "
                                              "(GitHub skipped it). Started it now."))
            continue
        latest = max(mine, key=lambda r: _time(r["created_at"]))
        if (latest.get("status") == "completed" and latest.get("conclusion") in FAILED
                and int(latest.get("run_attempt") or 1) == 1):
            actions.append(("rerun", latest["id"], f"The {job.name} job failed on {_when(at)}. "
                                                   "Running it once more."))
    return actions


def _time(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def github(token: str, repo: str) -> Callable[..., Any]:
    def call(method: str, path: str, body: dict | None = None) -> Any:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repo}{path}", method=method,
            data=json.dumps(body).encode("utf-8") if body is not None else None,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=30) as reply:
            raw = reply.read()
            return json.loads(raw) if raw else None
    return call


def run(api: Callable[..., Any], now: datetime, *, ref: str = "main", dry_run: bool = False,
        log: Callable[..., None] = print) -> list[str]:
    """Check every job; act; return the lines worth telling the owner."""
    said = []
    for job in JOBS:
        try:
            info = api("GET", f"/actions/workflows/{job.file}")
            if info.get("state") != "active":
                said.append(f"The {job.name} job's schedule was switched off by GitHub "
                            f"({info.get('state')}). Switched it back on.")
                if not dry_run:
                    api("PUT", f"/actions/workflows/{job.file}/enable")
            runs = api("GET", f"/actions/workflows/{job.file}/runs?per_page=40").get("workflow_runs", [])
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            log(f"{job.file}: could not check ({type(exc).__name__}: {exc})")
            raise
        todo = check(job, runs, now)
        if not todo:
            log(f"{job.file}: fine")
        for action, run_id, words in todo:
            log(f"{job.file}: {action} - {words}")
            said.append(words)
            if dry_run:
                continue
            if action == "dispatch":
                api("POST", f"/actions/workflows/{job.file}/dispatches", {"ref": ref})
            else:
                api("POST", f"/actions/runs/{run_id}/rerun-failed-jobs")
    return said


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    token, repo = os.environ.get("GITHUB_TOKEN", ""), os.environ.get("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("GITHUB_TOKEN and GITHUB_REPOSITORY are needed", file=sys.stderr)
        return 1
    said = run(github(token, repo), datetime.now(timezone.utc), dry_run=args.dry_run)
    if said and not args.dry_run:
        from alert_failure import send
        send("\U0001f6e1 Watchdog\n" + "\n".join(f"- {s}" for s in said), dict(os.environ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
