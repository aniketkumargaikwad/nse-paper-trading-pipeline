"""The margin a position blocks: a SPAN-style estimate (design §4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import pricing  # noqa: E402
from fno.margin import EXPOSURE_PCT, MarginLeg, estimate, max_loss, scan_pct  # noqa: E402

F, T, R = 21500.0, 7 / 365, 0.068


def leg(sign, typ, strike, units=50, iv=0.15):
    price = float(pricing.black76(F, strike, T, iv, R, typ == "CE"))
    return MarginLeg(sign, typ == "CE", strike, units, iv, price)


def margin(legs, vix=14.0):
    return estimate(legs, forward=F, years=T, rate=R, vix=vix)


def test_the_scan_widens_with_vix_above_a_floor():
    assert scan_pct(10.0) == 6.0
    assert scan_pct(30.0) == 15.0
    assert scan_pct(float("nan")) == 6.0


def test_bought_options_block_the_premium_paid():
    legs = [leg(1, "CE", 21500), leg(1, "PE", 21500)]
    assert margin(legs) == pytest.approx(sum(x.price * x.units for x in legs))


def test_a_narrow_credit_spread_blocks_its_maximum_loss():
    legs = [leg(-1, "PE", 21400), leg(1, "PE", 21300)]
    credit = (legs[0].price - legs[1].price) * 50
    assert max_loss(legs) == pytest.approx(100 * 50 - credit)
    assert margin(legs) == pytest.approx(100 * 50 - credit)


def test_an_uncovered_call_can_lose_without_limit():
    assert max_loss([leg(-1, "CE", 21500)]) == float("inf")


def test_a_naked_straddle_blocks_scan_loss_plus_exposure_more_when_vix_is_high():
    legs = [leg(-1, "CE", 21500), leg(-1, "PE", 21500)]
    exposure = EXPOSURE_PCT / 100 * F * 100
    calm, fearful = margin(legs, vix=12.0), margin(legs, vix=30.0)
    premium = sum(x.price for x in legs)
    # +-6% is 1,290 points: one leg is that far in the money, both premiums were collected
    assert calm - exposure == pytest.approx((0.06 * F - premium) * 50, rel=0.05)
    assert fearful > calm
