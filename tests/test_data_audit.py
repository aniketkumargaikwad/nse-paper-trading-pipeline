"""The retro-scan over candles that were stored before the checks ran.

Wiring detection into ingestion only protects candles fetched from now on.
3.1M candles were already stored while `detect_suspected_splits` and
`detect_session_gaps` existed but were called by nothing except their own
tests. This audit is what makes the existing history trustworthy — or tells
you, honestly, that it is not.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from audit_data_quality import (  # noqa: E402
    expected_trading_days,
    scan_instrument,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def sessions(closes: dict[date, float]) -> pd.DataFrame:
    """One 5m candle per session, at the given closes."""
    stamps, values = [], []
    for day, close in sorted(closes.items()):
        stamps.append(datetime(day.year, day.month, day.day, 15, 25,
                               tzinfo=IST).astimezone(UTC))
        values.append(close)
    return pd.DataFrame(
        {"open": values, "high": values, "low": values,
         "close": values, "volume": [1000.0] * len(values)},
        index=pd.DatetimeIndex(stamps, name="ts"),
    )


# --- expected trading days --------------------------------------------------


def test_weekends_are_never_expected() -> None:
    # 2026-08-22 is a Saturday, 2026-08-23 a Sunday.
    days = expected_trading_days(
        date(2026, 8, 21), date(2026, 8, 24),
        holidays=frozenset(), known_years=frozenset({2026}),
    )
    assert days == [date(2026, 8, 21), date(2026, 8, 24)]


def test_listed_holidays_are_not_expected() -> None:
    days = expected_trading_days(
        date(2026, 8, 20), date(2026, 8, 21),
        holidays=frozenset({date(2026, 8, 21)}),
        known_years=frozenset({2026}),
    )
    assert days == [date(2026, 8, 20)]


def test_uncovered_years_are_skipped_entirely() -> None:
    """The load-bearing one.

    The holiday file covers 2026 only, while stored candles reach back to
    2024. Scanning 2024 without its holiday list would report Diwali as a
    missing session — hundreds of false alarms that would bury any real gap
    and teach you to ignore the report.
    """
    days = expected_trading_days(
        date(2025, 8, 18), date(2025, 8, 22),
        holidays=frozenset(), known_years=frozenset({2026}),
    )
    assert days == []


def test_a_range_spanning_two_years_keeps_only_the_covered_one() -> None:
    days = expected_trading_days(
        date(2025, 12, 30), date(2026, 1, 2),
        holidays=frozenset(), known_years=frozenset({2026}),
    )
    assert days == [date(2026, 1, 1), date(2026, 1, 2)]


# --- scanning ---------------------------------------------------------------


def test_scan_reports_an_uncorroborated_split() -> None:
    intraday = sessions({date(2026, 8, 3): 1000.0, date(2026, 8, 4): 200.0})
    daily = sessions({date(2026, 8, 3): 1000.0, date(2026, 8, 4): 1010.0})
    flags = scan_instrument(intraday, daily, expected_days=[])
    assert [f.flag_type for f in flags] == ["suspected_split"]


def test_scan_reports_a_missing_session() -> None:
    intraday = sessions({date(2026, 8, 3): 100.0})
    flags = scan_instrument(
        intraday, pd.DataFrame(),
        expected_days=[date(2026, 8, 3), date(2026, 8, 4)],
    )
    assert [f.flag_type for f in flags] == ["session_gap"]
    assert flags[0].detail["missing_date"] == "2026-08-04"


def test_clean_history_produces_no_flags() -> None:
    intraday = sessions({date(2026, 8, 3): 100.0, date(2026, 8, 4): 101.0})
    daily = sessions({date(2026, 8, 3): 100.0, date(2026, 8, 4): 101.0})
    flags = scan_instrument(
        intraday, daily, expected_days=[date(2026, 8, 3), date(2026, 8, 4)]
    )
    assert flags == []


def test_empty_history_reports_gaps_not_splits() -> None:
    flags = scan_instrument(
        pd.DataFrame(), pd.DataFrame(), expected_days=[date(2026, 8, 3)]
    )
    assert [f.flag_type for f in flags] == ["session_gap"]


def test_flags_are_serialisable_as_rows() -> None:
    """A flag the audit cannot write is a finding it silently loses."""
    intraday = sessions({date(2026, 8, 3): 1000.0, date(2026, 8, 4): 200.0})
    flags = scan_instrument(intraday, pd.DataFrame(), expected_days=[])
    row = flags[0].to_row(instrument_id=7, timeframe="5m")
    assert row["instrument_id"] == 7
    assert row["timeframe"] == "5m"
    assert row["flag_type"] == "suspected_split"
