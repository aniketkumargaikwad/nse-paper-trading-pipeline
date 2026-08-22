"""Has the paper engine actually been running?

WHY THIS EXISTS
---------------
The engine had never opened a position, and nothing anywhere said so. The
System page counted 17 runs and reported them cheerfully — but 15 were
backtests, and the two paper runs were both on a Saturday, where skipping is
the correct behaviour. Every individual signal was green. The conclusion
"paper trading is not running" had to be assembled by hand from a table.

A scheduled job that silently never runs is indistinguishable from a strategy
that never finds a signal, which is the same failure this codebase keeps
designing against: absence presenting as a normal result.

So this answers the question directly, in one verdict, from the audit rows.

Pure module: no I/O, no clock reads, no database. The caller supplies the
rows, the moment and the calendar.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from market_calendar import is_trading_day

# How many trading sessions may pass with no paper run before the engine is
# called stale. One session is normal slack — a run can be a few minutes late,
# and a session can end before a cron fires. Two means yesterday was missed
# entirely, which is worth saying out loud.
STALE_AFTER_SESSIONS = 2


@dataclass(frozen=True)
class EngineHealth:
    """One verdict about the paper engine, written for a person."""

    level: str              # 'never' | 'never_traded' | 'stale' | 'ok'
    headline: str
    detail: str
    last_run: datetime | None = None
    total_runs: int = 0
    trading_day_runs: int = 0

    @property
    def is_healthy(self) -> bool:
        return self.level == "ok"


def trading_sessions_between(
    start: date, end: date, holidays: frozenset[date], known_years: frozenset[int]
) -> int:
    """Trading sessions strictly after `start`, up to and including `end`.

    Years the calendar does not cover are counted as trading days rather than
    skipped. Overcounting makes the engine look MORE stale than it is, which
    is the safe direction: the alternative is quietly under-reporting a gap
    because we could not tell whether it was a holiday.
    """
    if end <= start:
        return 0
    sessions = 0
    day = start + timedelta(days=1)
    while day <= end:
        if day.year not in known_years:
            if day.weekday() < 5:
                sessions += 1
        elif is_trading_day(day, holidays):
            sessions += 1
        day += timedelta(days=1)
    return sessions


def assess(
    paper_runs: Sequence[Any],
    *,
    now_ist: datetime,
    holidays: frozenset[date],
    known_years: frozenset[int],
) -> EngineHealth:
    """Judge the paper engine from its own audit rows.

    `paper_runs` is a sequence of (started_ist, status) pairs, newest order
    irrelevant. Only paper rows belong here — counting backtests was how the
    page came to report seventeen healthy runs of an engine that had never
    traded.
    """
    runs = [(when, status) for when, status in paper_runs if when is not None]

    if not runs:
        return EngineHealth(
            level="never",
            headline="The paper engine has never run",
            detail=(
                "Nothing has ever invoked it. Strategies can be backtested, "
                "but no simulated position will ever open until the scheduled "
                "job exists and runs — see docs/DEPLOYING.md, Step 5."
            ),
        )

    runs.sort(key=lambda r: r[0])
    last_run = runs[-1][0]

    on_trading_days = [
        when for when, _ in runs
        if when.date().year not in known_years and when.weekday() < 5
        or (when.date().year in known_years and is_trading_day(when.date(), holidays))
    ]

    if not on_trading_days:
        return EngineHealth(
            level="never_traded",
            headline="The paper engine has never run on a trading day",
            detail=(
                f"It has been invoked {len(runs)} time(s), all outside market "
                "hours — weekends or NSE holidays — where skipping is the "
                "correct behaviour. So it has never actually evaluated a "
                "strategy. A skipped run is healthy; only skipped runs is not."
            ),
            last_run=last_run,
            total_runs=len(runs),
            trading_day_runs=0,
        )

    missed = trading_sessions_between(
        on_trading_days[-1].date(), now_ist.date(), holidays, known_years
    )
    if missed >= STALE_AFTER_SESSIONS:
        return EngineHealth(
            level="stale",
            headline=f"The paper engine has not run for {missed} trading sessions",
            detail=(
                f"Its last run on a trading day was "
                f"{on_trading_days[-1]:%Y-%m-%d %H:%M} IST. If the schedule is "
                "still meant to be on, check the cron service is deployed and "
                "its variables are set."
            ),
            last_run=last_run,
            total_runs=len(runs),
            trading_day_runs=len(on_trading_days),
        )

    return EngineHealth(
        level="ok",
        headline="The paper engine is running",
        detail=(
            f"Last trading-day run {on_trading_days[-1]:%Y-%m-%d %H:%M} IST, "
            f"{len(on_trading_days)} of {len(runs)} runs on trading days."
        ),
        last_run=last_run,
        total_runs=len(runs),
        trading_day_runs=len(on_trading_days),
    )


def paper_runs_from_rows(rows: Iterable[Any]) -> list[tuple[datetime, str]]:
    """Pull (started_ist, status) pairs for PAPER runs out of audit rows.

    Accepts anything with `run_type`, `run_started_at` and `status` keys —
    a DataFrame's row dicts or plain mappings.
    """
    out: list[tuple[datetime, str]] = []
    for row in rows:
        if row.get("run_type") != "paper":
            continue
        started = row.get("run_started_at")
        if started is None:
            continue
        out.append((started, str(row.get("status", ""))))
    return out
