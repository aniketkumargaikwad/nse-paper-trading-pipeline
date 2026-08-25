"""A backtest must say when it spans a known-bad candle.

An unadjusted split is a 50% overnight collapse that never happened. It raises
no error, looks like nothing unusual, and presents to a breakout rule as the
strongest signal in the entire sample. Nine years of 5-minute NIFTY 50 data
contains eight of them.

"4.5% of all candles" is the wrong way to think about it: it is 91% of TMPV's
history and 36% of EICHERMOT's.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_quality import describe_split_warning, splits_inside_window  # noqa: E402

UTC = timezone.utc


def flag(symbol: str, when: str, *, checked: bool = True, ratio: float = 2.0) -> dict:
    return {
        "symbol": symbol,
        "flag_type": "suspected_split",
        "ts": when,
        "detail": {
            "ratio": ratio, "previous_close": 1000.0, "close": 500.0,
            "adjusted_daily_checked": checked,
            "corroborated_by_adjusted_daily": False,
        },
    }


WINDOW_FROM = datetime(2018, 1, 1, tzinfo=UTC)
WINDOW_TO = datetime(2020, 1, 1, tzinfo=UTC)


def find(flags, symbols=("NSE:TCS",)):
    return splits_inside_window(flags, symbols, WINDOW_FROM, WINDOW_TO)


def test_a_split_inside_the_window_is_reported() -> None:
    assert len(find([flag("NSE:TCS", "2018-05-31T04:00:00+00:00")])) == 1


def test_a_split_before_the_window_is_ignored() -> None:
    assert find([flag("NSE:TCS", "2017-06-13T04:00:00+00:00")]) == []


def test_a_split_after_the_window_is_ignored() -> None:
    assert find([flag("NSE:TCS", "2025-10-14T04:00:00+00:00")]) == []


def test_a_split_on_a_symbol_not_being_tested_is_ignored() -> None:
    assert find([flag("NSE:WIPRO", "2018-05-31T04:00:00+00:00")]) == []


def test_an_unchecked_flag_is_not_warned_about() -> None:
    """"Nobody has looked yet" is a different statement from "the adjusted feed
    disagreed", and only the second is grounds for a warning."""
    assert find([flag("NSE:TCS", "2018-05-31T04:00:00+00:00", checked=False)]) == []


def test_other_kinds_of_flag_are_ignored() -> None:
    gap = flag("NSE:TCS", "2018-05-31T04:00:00+00:00")
    gap["flag_type"] = "session_gap"
    assert find([gap]) == []


def test_several_symbols_are_all_reported_and_sorted() -> None:
    found = splits_inside_window(
        [flag("NSE:TCS", "2018-05-31T04:00:00+00:00"),
         flag("NSE:INFY", "2018-09-04T04:00:00+00:00")],
        ("NSE:TCS", "NSE:INFY"), WINDOW_FROM, WINDOW_TO,
    )
    assert [f["symbol"] for f in found] == ["NSE:INFY", "NSE:TCS"]


# --- the message ------------------------------------------------------------


def test_a_clean_window_produces_no_message() -> None:
    assert describe_split_warning([]) == ""


def test_the_message_names_the_symbol_and_the_date() -> None:
    text = describe_split_warning(find([flag("NSE:TCS", "2018-05-31T04:00:00+00:00")]))
    assert "NSE:TCS" in text
    assert "2018-05-31" in text
    assert "1000.0 -> 500.0" in text


def test_the_message_says_what_to_do_about_it() -> None:
    text = describe_split_warning(find([flag("NSE:TCS", "2018-05-31T04:00:00+00:00")]))
    assert "exclude" in text.lower()
