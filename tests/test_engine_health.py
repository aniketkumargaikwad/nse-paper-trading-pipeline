"""The check that would have caught "paper trading never ran".

Every individual signal was green: 17 audit rows, no errors, two skips that
were correct. The conclusion — that no strategy had ever been evaluated —
had to be assembled by hand. These tests pin the verdict so it is stated
rather than inferred.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine_health import (  # noqa: E402
    STALE_AFTER_SESSIONS,
    assess,
    paper_runs_from_rows,
    trading_sessions_between,
)

IST = ZoneInfo("Asia/Kolkata")

# 2026: the 15th is a Wednesday, the 18th a Saturday, the 19th a Sunday.
HOLIDAYS = frozenset({date(2026, 7, 17)})
YEARS = frozenset({2026})


def ist(y, m, d, hh=10, mm=0) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def health(runs, now=ist(2026, 7, 22)):
    return assess(runs, now_ist=now, holidays=HOLIDAYS, known_years=YEARS)


# --- counting sessions ------------------------------------------------------


def test_weekends_are_not_sessions() -> None:
    # Fri 17th is a holiday here, so Thu 16 -> Mon 20 is one session.
    assert trading_sessions_between(
        date(2026, 7, 16), date(2026, 7, 20), HOLIDAYS, YEARS
    ) == 1


def test_the_start_day_itself_is_not_counted() -> None:
    assert trading_sessions_between(
        date(2026, 7, 15), date(2026, 7, 15), HOLIDAYS, YEARS
    ) == 0


def test_a_holiday_is_not_a_session() -> None:
    assert trading_sessions_between(
        date(2026, 7, 16), date(2026, 7, 17), HOLIDAYS, YEARS
    ) == 0


def test_an_uncovered_year_counts_weekdays_rather_than_guessing() -> None:
    """Overcounting makes the engine look MORE stale, which is the safe
    direction — the alternative is hiding a real gap behind a maybe-holiday."""
    assert trading_sessions_between(
        date(2024, 7, 15), date(2024, 7, 19), frozenset(), YEARS
    ) == 4


# --- the verdict ------------------------------------------------------------


def test_no_runs_at_all_says_never() -> None:
    verdict = health([])
    assert verdict.level == "never"
    assert not verdict.is_healthy


def test_only_weekend_runs_says_never_traded() -> None:
    """THE case. Two Saturday invocations, both correctly skipped, and no
    strategy ever evaluated — which no count of runs would reveal."""
    verdict = health([
        (ist(2026, 7, 18), "skipped"),
        (ist(2026, 7, 19), "skipped"),
    ])
    assert verdict.level == "never_traded"
    assert verdict.total_runs == 2
    assert verdict.trading_day_runs == 0
    assert "never actually evaluated" in verdict.detail


def test_a_holiday_run_does_not_count_as_a_trading_day() -> None:
    verdict = health([(ist(2026, 7, 17), "skipped")])
    assert verdict.level == "never_traded"


def test_a_recent_trading_day_run_is_healthy() -> None:
    verdict = health(
        [(ist(2026, 7, 22, 10, 30), "ok")], now=ist(2026, 7, 22, 15, 0)
    )
    assert verdict.level == "ok"
    assert verdict.is_healthy
    assert verdict.trading_day_runs == 1


def test_yesterdays_run_is_still_healthy() -> None:
    """One session of slack: a cron can fire late, or a session can end
    before it fires at all."""
    verdict = health([(ist(2026, 7, 21, 14, 0), "ok")], now=ist(2026, 7, 22, 10, 0))
    assert verdict.level == "ok"


def test_a_gap_of_several_sessions_is_stale() -> None:
    verdict = health([(ist(2026, 7, 13, 10, 0), "ok")], now=ist(2026, 7, 22, 10, 0))
    assert verdict.level == "stale"
    assert "trading sessions" in verdict.headline


def test_the_stale_threshold_is_the_documented_one() -> None:
    last = ist(2026, 7, 15, 10, 0)          # Wednesday
    # Thu 16 is a session, Fri 17 a holiday, Sat/Sun none, Mon 20 a session.
    just_under = health([(last, "ok")], now=ist(2026, 7, 16, 10, 0))
    assert just_under.level == "ok"
    over = health([(last, "ok")], now=ist(2026, 7, 20, 10, 0))
    assert over.level == "stale"
    assert STALE_AFTER_SESSIONS == 2


def test_a_skipped_run_on_a_trading_day_still_counts_as_running() -> None:
    """Skipping outside the window is healthy behaviour — what matters is
    that the job woke up at all."""
    verdict = health([(ist(2026, 7, 22, 8, 0), "skipped")], now=ist(2026, 7, 22, 16, 0))
    assert verdict.level == "ok"


# --- reading the audit table ------------------------------------------------


def test_backtest_rows_are_not_counted_as_engine_runs() -> None:
    """The exact confusion that made the page look healthy: 15 backtests and
    2 paper skips reported together as 17 runs."""
    rows = [
        {"run_type": "backtest", "run_started_at": ist(2026, 7, 22), "status": "ok"},
        {"run_type": "backtest", "run_started_at": ist(2026, 7, 21), "status": "ok"},
        {"run_type": "paper", "run_started_at": ist(2026, 7, 18), "status": "skipped"},
    ]
    runs = paper_runs_from_rows(rows)
    assert len(runs) == 1
    assert health(runs).level == "never_traded"


def test_rows_without_a_start_time_are_ignored() -> None:
    rows = [{"run_type": "paper", "run_started_at": None, "status": "ok"}]
    assert paper_runs_from_rows(rows) == []


def test_an_empty_table_reads_as_no_runs() -> None:
    assert paper_runs_from_rows([]) == []
    assert health(paper_runs_from_rows([])).level == "never"
