"""Which expiry and which strikes a strategy's legs land on, on one day's chain."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain  # noqa: E402
from fno.strategy import Leg  # noqa: E402
from fno.strikes import NoStrike, pick_expiry, pick_strikes  # noqa: E402
from fno_synthetic import D1, D4, E1, E2, FORWARD, MONTHLY, RATE, daily, model  # noqa: E402


def snap(rows=None):
    return chain.snapshot(daily() if rows is None else rows, "NIFTY", D1, E1, spot=FORWARD, rate=RATE)


def leg(typ, rule, value, side="sell"):
    return Leg(side, typ, rule, value, 1)


def test_weekly_counts_from_the_nearest_expiry_with_a_day_to_run():
    rows = daily()
    assert pick_expiry(rows, D1, "weekly", 0) == E1
    assert pick_expiry(rows, D1, "weekly", 1) == E2
    assert pick_expiry(rows, D4, "weekly", 0) == E2      # E1 expires on D4: no day left


def test_monthly_means_an_expiry_that_has_a_future():
    rows = daily()
    assert pick_expiry(rows, D1, "monthly", 0) == MONTHLY
    assert pick_expiry(rows, D1, "monthly", 1) is None


def test_atm_counts_strikes_out_of_the_money_on_each_side():
    got = pick_strikes(snap(), [leg("CE", "atm", 0), leg("PE", "atm", 0),
                                leg("CE", "atm", 2), leg("PE", "atm", 2), leg("CE", "atm", -1)])
    assert got == [21500, 21500, 21700, 21300, 21400]


def test_atm_beyond_the_listed_strikes_is_refused():
    with pytest.raises(NoStrike, match="beyond"):
        pick_strikes(snap(), [leg("CE", "atm", 9)])


def test_otm_pct_is_measured_from_the_forward():
    assert pick_strikes(snap(), [leg("CE", "otm_pct", 1.0), leg("PE", "otm_pct", 1.0)]) == [21700, 21300]


def test_delta_picks_the_strike_with_the_nearest_delta():
    s = snap()
    got = pick_strikes(s, [leg("CE", "delta", 0.2), leg("PE", "delta", -0.2)])
    for strike, typ, target in zip(got, ("CE", "PE"), (0.2, -0.2)):
        side = s.rows[s.rows["option_type"] == typ]
        assert strike == side.loc[(side["delta"] - target).abs().idxmin(), "strike"]
    assert got[0] > FORWARD > got[1]


def test_premium_picks_the_nearest_settlement_price():
    assert pick_strikes(snap(), [leg("CE", "premium", model(D1, E1, 21700.0, "CE") + 0.4)]) == [21700]


def test_max_oi_and_a_wing_from_another_leg():
    rows = daily({(D1, E1, 21800.0, "CE"): {"oi_units": 999_999}})
    got = pick_strikes(snap(rows), [leg("CE", "max_oi", True), leg("CE", "from_leg", (0, 200.0), "buy")])
    assert got == [21800, 22000]
