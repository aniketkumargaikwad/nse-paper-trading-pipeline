"""NSE F&O charges on a fill, at the rates in force that day (design §4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.costs import FnoCharges, Fill  # noqa: E402

CHARGES = FnoCharges.load()


def test_stt_on_selling_an_option_tracks_each_change():
    assert CHARGES.stt_rate("OPT", date(2023, 3, 31)) == 0.0005
    assert CHARGES.stt_rate("OPT", date(2023, 4, 1)) == 0.000625
    assert CHARGES.stt_rate("OPT", date(2024, 10, 1)) == 0.001
    assert CHARGES.stt_rate("OPT", date(2026, 3, 31)) == 0.001
    assert CHARGES.stt_rate("OPT", date(2026, 4, 1)) == 0.0015


def test_futures_stt_tracks_each_change():
    assert CHARGES.stt_rate("FUT", date(2020, 1, 1)) == 0.0001
    assert CHARGES.stt_rate("FUT", date(2026, 4, 1)) == 0.0005


def test_one_sold_nifty_lot_on_30_sep_2026_worked_by_hand():
    """65 units at Rs 100: turnover 6,500.
    brokerage 20; exchange 6500 x 0.0003553 = 2.30945; SEBI 0.0065;
    STT 6500 x 0.0015 = 9.75; no stamp on a sale;
    GST 18% of (20 + 2.30945 + 0.0065) = 4.016919."""
    got = CHARGES.fill(Fill(date(2026, 9, 30), "OPT", "sell", 100.0, 65))
    assert got == pytest.approx(20 + 2.30945 + 0.0065 + 9.75 + 4.016919, abs=1e-6)


def test_buying_pays_stamp_and_no_stt():
    got = CHARGES.fill(Fill(date(2026, 9, 30), "OPT", "buy", 100.0, 65))
    gst = (20 + 2.30945 + 0.0065) * 0.18
    assert got == pytest.approx(20 + 2.30945 + 0.0065 + 6500 * 0.00003 + gst, abs=1e-6)


def test_gst_was_service_tax_before_july_2017():
    assert CHARGES.gst_rate(date(2017, 6, 30)) == 0.15
    assert CHARGES.gst_rate(date(2017, 7, 1)) == 0.18


def test_exercise_stt_is_on_the_settlement_value():
    assert CHARGES.exercise(date(2026, 3, 26), 10_000.0) == pytest.approx(12.5)
    assert CHARGES.exercise(date(2026, 4, 1), 10_000.0) == pytest.approx(15.0)


def test_a_date_before_the_table_is_refused():
    with pytest.raises(LookupError):
        CHARGES.stt_rate("OPT", date(2015, 12, 31))
