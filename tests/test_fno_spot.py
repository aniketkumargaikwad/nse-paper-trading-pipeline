"""Index daily closes from Yahoo for the underlyings it covers."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import spot  # noqa: E402
from test_fno_vix import yahoo_like  # noqa: E402


def test_only_the_three_yahoo_covers_have_symbols():
    assert spot.YAHOO_SYMBOLS == {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYNXT50": "^NSMIDCP"}


def test_spot_is_stored_and_looked_up_by_date(tmp_path):
    from fno.yahoo import normalise
    spot.write(tmp_path, "NIFTY", normalise(yahoo_like()))
    closes = spot.closes(tmp_path, "NIFTY")
    assert closes[date(2026, 9, 30)] == 14.0
    assert date(2026, 10, 1) not in closes


def test_an_uncovered_underlying_has_no_closes(tmp_path):
    assert spot.closes(tmp_path, "MIDCPNIFTY") == {}
