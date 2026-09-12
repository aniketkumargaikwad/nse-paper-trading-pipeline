"""Delivery (overnight) charges, and the model that chooses per trade.

Structure, not rates: rates drift and are a visible number to update; a charge
on the wrong leg is a silent bias in every result.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel, HoldingCostModel  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def test_delivery_stt_applies_to_both_legs():
    m = DeliveryCostModel()
    no_stt = DeliveryCostModel(stt=0.0)
    qty, entry, exit_ = 100, 1000.0, 1100.0
    diff = m.round_trip(entry, exit_, qty) - no_stt.round_trip(entry, exit_, qty)
    assert diff == pytest.approx((entry + exit_) * qty * m.stt, rel=1e-6)


def test_delivery_stamp_duty_applies_to_the_buy_side_only():
    m = DeliveryCostModel()
    no_stamp = DeliveryCostModel(stamp_duty_buy=0.0)
    qty, entry, exit_ = 100, 1000.0, 1100.0
    diff = m.round_trip(entry, exit_, qty) - no_stamp.round_trip(entry, exit_, qty)
    assert diff == pytest.approx(entry * qty * m.stamp_duty_buy, rel=1e-6)


def test_depository_charge_is_once_per_trade_whatever_the_size():
    m = DeliveryCostModel()
    no_dp = DeliveryCostModel(dp_charge_per_sell=0.0)
    for qty in (10, 10_000):
        diff = m.round_trip(1000.0, 1000.0, qty) - no_dp.round_trip(1000.0, 1000.0, qty)
        assert diff == pytest.approx(m.dp_charge_per_sell)


def test_overnight_costs_more_than_the_same_trade_intraday():
    assert DeliveryCostModel().round_trip(1000.0, 1000.0, 100) > CostModel().round_trip(1000.0, 1000.0, 100)


def test_one_lakh_round_trip_is_about_a_fifth_of_a_percent():
    """Pins the scale measured in docs/research/2026-08-30: 0.21-0.26%."""
    cost = DeliveryCostModel().round_trip(1000.0, 1000.0, 100)
    assert 0.18 < cost / 100_000 * 100 < 0.30


def test_zero_quantity_costs_nothing():
    assert DeliveryCostModel().round_trip(1000.0, 1100.0, 0) == 0.0


def test_same_day_trade_is_charged_intraday():
    got = HoldingCostModel().round_trip(
        1000.0, 1010.0, 100, entry_ts=ist(2026, 8, 3, 9, 30), exit_ts=ist(2026, 8, 3, 15, 20)
    )
    assert got == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_trade_held_overnight_is_charged_delivery():
    got = HoldingCostModel().round_trip(
        1000.0, 1010.0, 100, entry_ts=ist(2026, 8, 3, 15, 0), exit_ts=ist(2026, 8, 4, 10, 0)
    )
    assert got == pytest.approx(DeliveryCostModel().round_trip(1000.0, 1010.0, 100))


def test_the_day_boundary_is_india_midnight_not_utc():
    entry, exit_ = ist(2026, 8, 3, 23, 50), ist(2026, 8, 4, 0, 10)
    assert entry.astimezone(UTC).date() == exit_.astimezone(UTC).date()
    assert HoldingCostModel().is_delivery(entry, exit_)


def test_missing_timestamps_are_refused():
    with pytest.raises(ValueError, match="entry_ts and exit_ts"):
        HoldingCostModel().round_trip(1000.0, 1010.0, 100)


def test_only_the_holding_model_asks_for_timestamps():
    assert HoldingCostModel.prices_by_holding is True
    assert not getattr(CostModel(), "prices_by_holding", False)
    assert not getattr(DeliveryCostModel(), "prices_by_holding", False)
