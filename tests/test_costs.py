"""Indian intraday equity charges. Pure arithmetic, hand-checked.

Rates change; these tests pin the STRUCTURE (which charge applies to which
leg, and that costs scale with turnover) rather than asserting rates that will
drift. A structural error - charging STT on the buy side, say - is a silent
bias in every result; a stale rate is a visible number to update.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, FlatCostModel  # noqa: E402


def test_costs_scale_with_turnover():
    """The whole reason a flat charge is wrong: a bigger trade costs more."""
    m = CostModel()
    small = m.round_trip(100.0, 101.0, 10)      # ~Rs 1,000
    large = m.round_trip(100.0, 101.0, 10_000)  # ~Rs 1,000,000
    assert large > small * 100


def test_stt_applies_to_the_sell_side_only():
    """Charging it on both legs would overstate every trade's cost."""
    m = CostModel()
    no_stt = CostModel(stt_sell=0.0)
    quantity, entry, exit_ = 100, 1000.0, 1100.0
    difference = m.round_trip(entry, exit_, quantity) - no_stt.round_trip(entry, exit_, quantity)
    assert difference == pytest.approx(exit_ * quantity * m.stt_sell, rel=1e-6)


def test_stamp_duty_applies_to_the_buy_side_only():
    m = CostModel()
    no_stamp = CostModel(stamp_duty_buy=0.0)
    quantity, entry, exit_ = 100, 1000.0, 1100.0
    difference = m.round_trip(entry, exit_, quantity) - no_stamp.round_trip(entry, exit_, quantity)
    assert difference == pytest.approx(entry * quantity * m.stamp_duty_buy, rel=1e-6)


def test_each_leg_is_priced_on_its_own_turnover():
    """A losing trade sells for less, so its sell-side charges are smaller.
    Averaging the legs would flatter every loser slightly."""
    m = CostModel()
    winner = m.round_trip(1000.0, 1100.0, 100)
    loser = m.round_trip(1000.0, 900.0, 100)
    assert winner > loser


def test_brokerage_is_the_lower_of_flat_and_percentage():
    m = CostModel(brokerage_per_order=20.0, brokerage_pct=0.0003)
    assert m.brokerage(10_000) == pytest.approx(3.0)    # 0.03% is lower
    assert m.brokerage(1_000_000) == pytest.approx(20.0)  # the cap binds


def test_gst_falls_on_brokerage_and_fees_but_not_on_stt():
    """STT is a tax; GST is not levied on it. Including it would inflate
    every trade."""
    m = CostModel()
    plain = CostModel(gst_rate=0.0)
    quantity, entry, exit_ = 100, 1000.0, 1000.0

    gst_charged = m.round_trip(entry, exit_, quantity) - plain.round_trip(entry, exit_, quantity)
    turnover = (entry + exit_) * quantity
    taxable = (
        m.brokerage(entry * quantity) + m.brokerage(exit_ * quantity)
        + turnover * m.exchange_txn + turnover * m.sebi_fees
    )
    assert gst_charged == pytest.approx(taxable * m.gst_rate, rel=1e-6)


def test_a_zero_quantity_trade_costs_nothing():
    assert CostModel().round_trip(1000.0, 1100.0, 0) == 0.0


def test_the_flat_model_ignores_size_entirely():
    """Retained so results predating itemised costs stay reproducible."""
    flat = FlatCostModel(30.0)
    assert flat.round_trip(100.0, 101.0, 1) == 30.0
    assert flat.round_trip(5000.0, 5100.0, 1000) == 30.0


def test_the_flat_charge_understates_a_realistic_trade():
    """Rs 30 against a Rs 100,000 notional - the platform's own default -
    is roughly a third of the real charge."""
    itemised = CostModel().round_trip(1000.0, 1010.0, 100)   # Rs ~100k notional
    assert itemised > 2.5 * 30.0


def test_both_models_describe_what_they_assumed():
    """A stored result must be able to say which costs produced it."""
    assert "STT" in CostModel().describe()
    assert "flat" in FlatCostModel().describe()
