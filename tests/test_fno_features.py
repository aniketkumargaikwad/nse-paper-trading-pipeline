"""Market facts from one day's chain (design §2.4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain, features  # noqa: E402
from test_fno_chain import DAY, EXP, F_TRUE, R, SIGMA, synthetic_day  # noqa: E402


@pytest.fixture
def snap():
    return chain.snapshot(synthetic_day(), "NIFTY", DAY, EXP, spot=21900.0, rate=R)


def test_atm_is_the_strike_nearest_the_forward(snap):
    facts = features.from_snapshot(snap)
    assert facts["atm_strike"] == 22000.0
    assert facts["atm_iv"] == pytest.approx(SIGMA, abs=1e-4)


def test_expected_move_is_the_straddle_over_the_forward(snap):
    facts = features.from_snapshot(snap)
    atm = snap.rows[snap.rows["strike"] == 22000.0]
    assert facts["straddle"] == pytest.approx(atm["settle"].sum())
    assert facts["expected_move_pct"] == pytest.approx(100 * atm["settle"].sum() / F_TRUE)


def test_pcr_divides_put_open_interest_by_call(snap):
    facts = features.from_snapshot(snap)
    puts = snap.rows.loc[snap.rows["option_type"] == "PE", "oi_units"].sum()
    calls = snap.rows.loc[snap.rows["option_type"] == "CE", "oi_units"].sum()
    assert facts["pcr_oi"] == pytest.approx(puts / calls)


def test_max_pain_is_the_strike_that_pays_option_holders_least():
    """All open interest on 22000 calls and 21000 puts: settling between
    them pays nothing, and the lowest such strike is 21000."""
    rows = synthetic_day()
    rows["oi_units"] = 0
    rows.loc[(rows["strike"] == 22000.0) & (rows["option_type"] == "CE"), "oi_units"] = 1000
    rows.loc[(rows["strike"] == 21000.0) & (rows["option_type"] == "PE"), "oi_units"] = 1000
    snap = chain.snapshot(rows, "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    assert features.from_snapshot(snap)["max_pain"] == 21000.0


def test_flat_volatility_has_no_skew(snap):
    assert features.from_snapshot(snap)["skew_25d"] == pytest.approx(0.0, abs=1e-3)


def test_basis_and_days_to_expiry(snap):
    facts = features.from_snapshot(snap)
    assert facts["dte"] == 28
    assert facts["basis_pct"] == pytest.approx(100 * (F_TRUE / 21900.0 - 1))


def test_iv_rank_places_today_within_the_past_year():
    ivs = pd.Series([0.10] * 100 + [0.20] * 100 + [0.15], dtype=float)
    rank, pctile = features.iv_rank(ivs, window=252)
    assert rank.iloc[-1] == pytest.approx(50.0)
    assert pctile.iloc[-1] == pytest.approx(100 * 100 / 200)
