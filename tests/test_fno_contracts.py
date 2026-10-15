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


from datetime import date  # noqa: E402

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from fno.contracts import LotSizeError, LotSizes, change_points, estimate_lot_sizes  # noqa: E402


def futures_day(day, lot, *, published=float("nan"), close=10_000.0, contracts=1_000, skew=1.01):
    """One busy future and one quiet one for NIFTY on `day`."""
    turnover = contracts * lot * close * skew          # average price a little off the close
    return pd.DataFrame({
        "trade_date": [day, day], "underlying": ["NIFTY", "NIFTY"], "instrument": ["FUT", "FUT"],
        "expiry": [date(2099, 1, 1), date(2099, 2, 1)], "close": [close, close],
        "volume_contracts": [contracts, 3], "turnover_inr": [turnover, 3 * 999 * close],
        "lot_size": [published, published],
    })


def test_the_lot_is_read_from_the_busiest_future_and_rounded():
    est = estimate_lot_sizes(futures_day(date(2016, 1, 4), 75))
    row = est.iloc[0]
    assert row["lot_size"] == 75 and row["source"] == "derived"


def test_a_published_lot_wins_over_the_estimate():
    est = estimate_lot_sizes(futures_day(date(2026, 9, 30), 65, published=65.0, skew=1.04))
    assert est.iloc[0]["lot_size"] == 65 and est.iloc[0]["source"] == "nse"


def test_a_change_counts_only_after_three_agreeing_sessions():
    days = [date(2021, 6, d) for d in (1, 2, 3, 4, 7, 8, 9, 10)]
    lots = [75, 75, 75, 50, 75, 50, 50, 50]           # one stray 50, then a real change
    est = pd.DataFrame({"underlying": "NIFTY", "trade_date": days, "lot_size": lots})
    assert change_points(est) == {"NIFTY": [(date(2021, 6, 1), 75), (date(2021, 6, 8), 50)]}


def test_the_table_answers_for_a_date_and_refuses_before_its_start():
    table = LotSizes({"NIFTY": ((date(2016, 1, 1), 75), (date(2021, 6, 7), 50))})
    assert table.on("NIFTY", date(2021, 6, 4)) == 75
    assert table.on("NIFTY", date(2021, 6, 7)) == 50
    with pytest.raises(LotSizeError):
        table.on("NIFTY", date(2015, 12, 31))
    with pytest.raises(LotSizeError):
        table.on("SENSEX", date(2021, 6, 7))


def test_the_table_survives_a_round_trip_through_yaml():
    table = LotSizes({"NIFTY": ((date(2016, 1, 1), 75), (date(2021, 6, 7), 50))})
    assert LotSizes.from_yaml(table.to_yaml()) == table
