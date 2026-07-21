"""NSE trading-calendar helpers: is now a time the paper engine should run?

Pure functions + one tiny YAML loader, so the gate logic is unit-testable
without touching the network or the clock.
"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import yaml

DEFAULT_HOLIDAYS_FILE = "nse_holidays.yaml"

# The engine's run window, in IST:
# * Nothing useful can happen before the FIRST 15m candle closes at 09:30.
# * The last cron fires at 15:30 IST; the window extends to 15:50 so a
#   delayed GitHub Actions start (cron drift is common) can still process
#   the final 15:15-15:30 candle. Idempotency makes the overlap harmless.
ENGINE_WINDOW_START = time(9, 30)
ENGINE_WINDOW_END = time(15, 50)


class CalendarError(RuntimeError):
    """Raised when the holiday file is missing or malformed."""


def load_holidays(path: str = DEFAULT_HOLIDAYS_FILE) -> frozenset[date]:
    """Load the holiday list, validating shape and types loudly."""
    file = Path(path)
    if not file.exists():
        raise CalendarError(
            f"Holiday file {path!r} not found. It ships with the repo — "
            "restore it (and update it yearly from NSE's holiday circular)."
        )
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise CalendarError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(data, dict) or "holidays" not in data:
        raise CalendarError(f"{path} must have a top-level 'holidays:' mapping of year -> dates.")

    holidays: set[date] = set()
    for year, dates in (data["holidays"] or {}).items():
        if not isinstance(dates, list):
            raise CalendarError(f"{path}: holidays.{year} must be a list of ISO dates.")
        for entry in dates:
            # PyYAML parses bare ISO dates into datetime.date automatically;
            # anything else (quoted string, typo) is rejected loudly.
            if not isinstance(entry, date):
                raise CalendarError(
                    f"{path}: {entry!r} under year {year} is not an ISO date. "
                    "Write dates unquoted as YYYY-MM-DD."
                )
            if entry.year != int(year):
                raise CalendarError(
                    f"{path}: {entry.isoformat()} is listed under year {year} — "
                    "move it to the right year block."
                )
            holidays.add(entry)
    return frozenset(holidays)


def is_trading_day(d: date, holidays: frozenset[date]) -> bool:
    """Mon-Fri and not an NSE holiday. (Muhurat sessions are ignored.)"""
    return d.weekday() < 5 and d not in holidays


def session_gate(now_ist: datetime, holidays: frozenset[date]) -> tuple[bool, str]:
    """Should the engine run right now? Returns (run, human-readable reason).

    The reason string is what lands in the run_audit row on skips, so it is
    written for the user reading the dashboard, not for code.
    """
    if now_ist.tzinfo is None:
        raise ValueError("session_gate needs an aware IST datetime")

    today = now_ist.date()
    if now_ist.weekday() == 5:
        return False, "market closed: Saturday"
    if now_ist.weekday() == 6:
        return False, "market closed: Sunday"
    if today in holidays:
        return False, f"market closed: NSE holiday ({today.isoformat()})"

    t = now_ist.time()
    if t < ENGINE_WINDOW_START:
        return False, (
            f"outside run window: before {ENGINE_WINDOW_START.strftime('%H:%M')} IST "
            "(no candle has closed yet today)"
        )
    if t > ENGINE_WINDOW_END:
        return False, (
            f"outside run window: after {ENGINE_WINDOW_END.strftime('%H:%M')} IST "
            "(market closed)"
        )
    return True, "ok"
