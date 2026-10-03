"""Which F&O contracts are researched, and their lot sizes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.contracts import ALWAYS_LISTED, INDEX_UNDERLYINGS, MINUTE_UNDERLYINGS  # noqa: E402


def test_minute_underlyings_are_a_subset_of_the_daily_ones():
    assert set(MINUTE_UNDERLYINGS) <= set(INDEX_UNDERLYINGS)
    assert MINUTE_UNDERLYINGS == ("NIFTY", "BANKNIFTY")


def test_only_contracts_listed_since_before_2016_are_expected_every_day():
    assert ALWAYS_LISTED == ("NIFTY", "BANKNIFTY")
