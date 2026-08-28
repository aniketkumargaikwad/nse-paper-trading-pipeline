"""Tests for candle quality checks. Findings are FLAGGED, never auto-fixed."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_quality import (  # noqa: E402
    QualityFlag,
    check_ohlc_sanity,
    detect_session_gaps,
    detect_suspected_splits,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def frame(rows: list[tuple], start_ist: datetime | None = None, step_min: int = 5) -> pd.DataFrame:
    start = start_ist or datetime(2026, 8, 3, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(len(rows))],
        name="ts",
    )
    arr = np.asarray(rows, dtype=float).reshape(len(rows), 5)
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": arr[:, 4]},
        index=index,
    )


def closes_frame(pairs: list[tuple[datetime, float]]) -> pd.DataFrame:
    """Minimal frame carrying only `close`, for split detection."""
    return pd.DataFrame(
        {"close": [c for _, c in pairs]},
        index=pd.DatetimeIndex([t.astimezone(UTC) for t, _ in pairs], name="ts"),
    )


# --- OHLC sanity ------------------------------------------------------------


def test_valid_candles_produce_no_flags() -> None:
    df = frame([(100, 105, 99, 104, 10), (104, 106, 103, 105, 20)])
    assert check_ohlc_sanity(df) == []


def test_high_below_close_is_flagged() -> None:
    df = frame([(100, 101, 99, 104, 10)])  # high 101 < close 104
    flags = check_ohlc_sanity(df)
    assert len(flags) == 1
    assert flags[0].flag_type == "ohlc_invalid"
    assert "high" in flags[0].detail["reason"]


def test_low_above_open_is_flagged() -> None:
    df = frame([(100, 105, 101, 104, 10)])  # low 101 > open 100
    assert check_ohlc_sanity(df)[0].flag_type == "ohlc_invalid"


def test_negative_volume_and_zero_price_are_flagged() -> None:
    assert check_ohlc_sanity(frame([(100, 105, 99, 104, -5)]))
    assert check_ohlc_sanity(frame([(0, 105, 0, 104, 10)]))


def test_zero_volume_is_allowed() -> None:
    # Illiquid buckets legitimately trade zero shares.
    assert check_ohlc_sanity(frame([(100, 100, 100, 100, 0)])) == []


def test_empty_frame_produces_no_flags() -> None:
    assert check_ohlc_sanity(frame([])) == []


# --- Split detection --------------------------------------------------------


def test_split_sized_gap_absent_from_adjusted_daily_is_flagged() -> None:
    """Intraday shows a 1:5 split as an 80% crash; the adjusted daily series
    does not. That disagreement is the signal."""
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 200.0),
    ])
    adjusted_daily = closes_frame([
        (datetime(2026, 8, 3, tzinfo=IST), 200.0),   # adjusted: no crash
        (datetime(2026, 8, 4, tzinfo=IST), 200.0),
    ])
    flags = detect_suspected_splits(intraday, adjusted_daily)
    assert len(flags) == 1
    assert flags[0].flag_type == "suspected_split"
    assert flags[0].detail["ratio"] == 5.0


def test_genuine_crash_present_in_both_series_is_not_flagged() -> None:
    # A real 80% fall appears in BOTH series, so it is not a split.
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 200.0),
    ])
    adjusted_daily = closes_frame([
        (datetime(2026, 8, 3, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, tzinfo=IST), 200.0),
    ])
    assert detect_suspected_splits(intraday, adjusted_daily) == []


def test_ordinary_overnight_move_is_not_flagged() -> None:
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 1020.0),
    ])
    assert detect_suspected_splits(intraday, pd.DataFrame()) == []


def test_split_flagged_when_no_daily_series_available() -> None:
    """With nothing to corroborate against, a split-sized jump must still be
    surfaced - silence would be the dangerous outcome."""
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 200.0),
    ])
    flags = detect_suspected_splits(intraday, pd.DataFrame())
    assert len(flags) == 1
    assert flags[0].detail["corroborated_by_adjusted_daily"] is False


def test_intraday_moves_within_one_session_are_ignored() -> None:
    """Only OVERNIGHT (session-to-session) moves can be splits."""
    intraday = closes_frame([
        (datetime(2026, 8, 3, 9, 15, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 200.0),  # same day
    ])
    assert detect_suspected_splits(intraday, pd.DataFrame()) == []


def test_single_session_produces_no_split_flags() -> None:
    intraday = closes_frame([(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 1000.0)])
    assert detect_suspected_splits(intraday, pd.DataFrame()) == []


# --- Session gaps -----------------------------------------------------------


def test_missing_expected_trading_day_is_flagged() -> None:
    # Mon 3rd present, Tue 4th absent, Wed 5th present.
    present = frame([(100, 101, 99, 100, 1)] * 3,
                    start_ist=datetime(2026, 8, 3, 9, 15, tzinfo=IST))
    later = frame([(100, 101, 99, 100, 1)] * 3,
                  start_ist=datetime(2026, 8, 5, 9, 15, tzinfo=IST))
    flags = detect_session_gaps(
        pd.concat([present, later]),
        expected_days=[date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)],
    )
    assert len(flags) == 1
    assert flags[0].flag_type == "session_gap"
    assert flags[0].detail["missing_date"] == "2026-08-04"


def test_no_gap_when_all_expected_days_present() -> None:
    df = frame([(100, 101, 99, 100, 1)] * 3)
    assert detect_session_gaps(df, expected_days=[date(2026, 8, 3)]) == []


def test_every_day_missing_when_frame_is_empty() -> None:
    flags = detect_session_gaps(frame([]), expected_days=[date(2026, 8, 3), date(2026, 8, 4)])
    assert len(flags) == 2


# --- Flag serialisation -----------------------------------------------------


def test_quality_flag_is_serialisable() -> None:
    flag = QualityFlag(
        flag_type="ohlc_invalid",
        ts=datetime(2026, 8, 3, 9, 15, tzinfo=UTC),
        detail={"reason": "test"},
    )
    row = flag.to_row(instrument_id=1, timeframe="5m")
    assert row["instrument_id"] == 1
    assert row["timeframe"] == "5m"
    assert row["flag_type"] == "ohlc_invalid"
    assert row["ts"].endswith("+00:00")
    assert row["detail"] == {"reason": "test"}


def test_flag_row_keys_match_the_database_columns() -> None:
    """Guards against drift from the data_quality_flags table shape."""
    row = QualityFlag(flag_type="session_gap",
                      ts=datetime(2026, 8, 3, tzinfo=UTC), detail={}).to_row(1, "5m")
    assert set(row) == {"instrument_id", "timeframe", "flag_type", "ts", "detail"}


# --- Fix verification --------------------------------------------------------


def test_nan_in_any_column_is_flagged() -> None:
    """Python's max/min short-circuit on NaN, so every comparison branch
    would fall through. Postgres will not catch it either: it orders NaN
    above all real numbers, so `check (close > 0)` accepts it."""
    for column in ("open", "high", "low", "close", "volume"):
        df = frame([(100, 105, 99, 104, 10)])
        df.loc[df.index[0], column] = float("nan")
        flags = check_ohlc_sanity(df)
        assert len(flags) == 1, f"NaN in {column} was not flagged"
        assert "NaN" in flags[0].detail["reason"]


def test_infinite_value_is_flagged() -> None:
    df = frame([(100, 105, 99, 104, 10)])
    df.loc[df.index[0], "high"] = float("inf")
    assert len(check_ohlc_sanity(df)) == 1


def test_multiple_bad_rows_all_flagged_in_order() -> None:
    df = frame([
        (100, 101, 99, 104, 10),   # bad: high < close
        (100, 105, 99, 104, 10),   # good
        (100, 105, 101, 104, 10),  # bad: low > open
    ])
    flags = check_ohlc_sanity(df)
    assert len(flags) == 2
    assert flags[0].ts < flags[1].ts


def test_session_gap_timestamp_is_the_session_open_and_stable() -> None:
    """The UNIQUE(instrument_id, timeframe, flag_type, ts) constraint relies
    on this timestamp being identical on every re-detection."""
    df = frame([(100, 101, 99, 100, 1)] * 2)
    first = detect_session_gaps(df, expected_days=[date(2026, 8, 4)])
    second = detect_session_gaps(df, expected_days=[date(2026, 8, 4)])
    assert first[0].ts == second[0].ts
    # 09:15 IST == 03:45 UTC, same calendar day, inside the session.
    assert first[0].to_row(1, "5m")["ts"] == "2026-08-04T03:45:00+00:00"


def test_naive_flag_timestamp_rejected() -> None:
    from data_quality import DataQualityError

    flag = QualityFlag(flag_type="session_gap",
                       ts=datetime(2026, 8, 3, 9, 15), detail={})
    with pytest.raises(DataQualityError, match="timezone-aware"):
        flag.to_row(1, "5m")


def test_nan_session_does_not_disable_split_detection() -> None:
    """A single unusable session must not blind the detector on BOTH sides
    of it; the last good close is carried forward as the anchor."""
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 15, 25, tzinfo=IST), float("nan")),
        (datetime(2026, 8, 5, 9, 15, tzinfo=IST), 200.0),
    ])
    flags = detect_suspected_splits(intraday, pd.DataFrame())
    assert len(flags) == 1
    assert flags[0].detail["ratio"] == 5.0


def test_large_genuine_move_is_corroborated_despite_small_feed_disagreement() -> None:
    """Relative tolerance: a 3% disagreement between the daily close and the
    last intraday close must not false-flag a genuine ratio-10 move."""
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 100.0),
    ])
    adjusted_daily = closes_frame([
        (datetime(2026, 8, 3, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, tzinfo=IST), 103.0),   # 3% apart -> ratio ~9.7
    ])
    assert detect_suspected_splits(intraday, adjusted_daily) == []


# --- "not corroborated" must say WHICH kind ---------------------------------
#
# A jump the adjusted daily series CONTRADICTS is strong evidence of an
# unadjusted corporate action. A jump with no daily series to check against is
# no evidence at all. Both used to be reported as corroborated=false, which
# makes a confirmed defect and an unchecked one look identical in the table.


def test_flag_records_the_adjusted_ratio_when_daily_is_available() -> None:
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 200.0),
    ])
    adjusted_daily = closes_frame([          # a normal ~1% move, not 5x
        (datetime(2026, 8, 3, tzinfo=IST), 400.0),
        (datetime(2026, 8, 4, tzinfo=IST), 395.0),
    ])
    flags = detect_suspected_splits(intraday, adjusted_daily)
    assert len(flags) == 1
    detail = flags[0].detail
    assert detail["adjusted_daily_checked"] is True
    # The adjusted series moved ~1%, nothing like the intraday 5x.
    assert detail["adjusted_ratio"] == pytest.approx(400.0 / 395.0, rel=1e-3)


def test_flag_says_so_when_there_was_no_daily_series_to_check() -> None:
    intraday = closes_frame([
        (datetime(2026, 8, 3, 15, 25, tzinfo=IST), 1000.0),
        (datetime(2026, 8, 4, 9, 15, tzinfo=IST), 200.0),
    ])
    flags = detect_suspected_splits(intraday, pd.DataFrame())
    assert len(flags) == 1
    detail = flags[0].detail
    assert detail["adjusted_daily_checked"] is False
    assert detail["adjusted_ratio"] is None


# ---------------------------------------------------------------------------
# Splits that are already corrected must not be warned about
# ---------------------------------------------------------------------------


def _split_flag(symbol: str, when_ist: str, previous_ist: str = "") -> dict:
    """A confirmed-split flag as the audit writes one."""
    from datetime import datetime as _dt
    return {
        "flag_type": "suspected_split",
        "symbol": symbol,
        "ts": _dt.fromisoformat(f"{when_ist}T09:15:00+05:30").isoformat(),
        "detail": {
            "adjusted_daily_checked": True,
            "adjusted_ratio": 1.0115,
            "ratio": 1.6833,
            "previous_date": previous_ist or None,
            "previous_close": 664.9,
            "close": 395.0,
        },
    }


def _window():
    from datetime import datetime as _dt, timezone as _tz
    return (_dt(2025, 1, 1, tzinfo=_tz.utc), _dt(2026, 1, 1, tzinfo=_tz.utc))


def test_a_confirmed_split_is_warned_about_when_nothing_corrects_it():
    from data_quality import splits_inside_window
    lo, hi = _window()
    found = splits_inside_window(
        [_split_flag("NSE:TMPV", "2025-10-14")], ["NSE:TMPV"], lo, hi)
    assert len(found) == 1


def test_a_corrected_split_is_not_warned_about():
    """The raw candles still hold the break; the backtest never sees it.

    Warning anyway tells the operator to "treat the result as unsound" about
    a result that is sound - and the next warning, the real one, gets
    ignored too.
    """
    from data_quality import splits_inside_window
    lo, hi = _window()
    found = splits_inside_window(
        [_split_flag("NSE:TMPV", "2025-10-14")], ["NSE:TMPV"], lo, hi,
        corrected=lambda symbol, previous_day, day: True,
    )
    assert found == []


def test_the_correction_check_is_asked_about_the_right_days():
    """Asked in IST, and with the PREVIOUS TRADING day, not the day before.

    Two off-by-ones live here. The flag's timestamp is 09:15 IST, which is the
    previous day in UTC. And a correction ends on the last TRADING day before
    a split, so subtracting one calendar day is wrong across every weekend -
    EICHERMOT split on Monday 2020-08-24 with its correction ending Friday the
    21st, and asking about Sunday the 23rd matched nothing and warned about a
    split that was correctly handled.
    """
    from datetime import date as _date
    from data_quality import splits_inside_window
    lo, hi = _window()
    asked: list[tuple] = []

    splits_inside_window(
        [_split_flag("NSE:TMPV", "2025-10-14", "2025-10-13")], ["NSE:TMPV"],
        lo, hi,
        corrected=lambda s_, p_, d_: asked.append((s_, p_, d_)) or False,
    )
    assert asked == [("NSE:TMPV", _date(2025, 10, 13), _date(2025, 10, 14))]


def test_a_split_after_a_weekend_uses_the_friday():
    """The EICHERMOT case, which the first version of this filter got wrong."""
    from datetime import date as _date
    from data_quality import splits_inside_window
    from datetime import datetime as _dt, timezone as _tz
    lo = _dt(2020, 1, 1, tzinfo=_tz.utc)
    hi = _dt(2021, 1, 1, tzinfo=_tz.utc)
    asked: list[tuple] = []

    splits_inside_window(
        [_split_flag("NSE:EICHERMOT", "2020-08-24", "2020-08-21")],
        ["NSE:EICHERMOT"], lo, hi,
        corrected=lambda s_, p_, d_: asked.append((p_, d_)) or False,
    )
    assert asked == [(_date(2020, 8, 21), _date(2020, 8, 24))], (
        "must ask about the Friday, not the Sunday")


def test_a_flag_without_a_previous_date_still_works():
    """Older flags predate the field; fall back rather than crash."""
    from data_quality import splits_inside_window
    lo, hi = _window()
    assert splits_inside_window(
        [_split_flag("NSE:TMPV", "2025-10-14")], ["NSE:TMPV"], lo, hi,
        corrected=lambda s_, p_, d_: True,
    ) == []


def test_omitting_the_check_keeps_warning_about_everything():
    """A caller that cannot check corrections must still get the warning."""
    from data_quality import splits_inside_window
    lo, hi = _window()
    assert len(splits_inside_window(
        [_split_flag("NSE:TMPV", "2025-10-14")], ["NSE:TMPV"], lo, hi)) == 1
