"""Black-76 prices, implied volatility and Greeks, vectorised."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import pricing  # noqa: E402


def test_the_normal_cdf_matches_math_erf_closely():
    xs = np.linspace(-6, 6, 241)
    exact = np.array([0.5 * (1 + math.erf(x / math.sqrt(2))) for x in xs])
    assert np.max(np.abs(pricing.norm_cdf(xs) - exact)) < 2e-7


def test_an_atm_call_at_zero_rate_matches_the_textbook_value():
    """F = K = 100, T = 1, sigma = 20%, r = 0: 100 x (2N(0.1) - 1) = 7.9656."""
    price = pricing.black76(np.array([100.0]), np.array([100.0]), np.array([1.0]),
                            np.array([0.2]), 0.0, np.array([True]))
    # The erf approximation is good to 1.5e-7, i.e. 1.5e-5 on F = 100 (a third
    # of a paisa on NIFTY): the tolerance is the method's stated accuracy.
    assert price[0] == pytest.approx(7.965567, abs=2e-5)


def test_put_call_parity_holds():
    F, K, T, s, r = 22000.0, 22500.0, 30 / 365, 0.14, 0.06
    c = pricing.black76(np.array([F]), np.array([K]), np.array([T]), np.array([s]), r, np.array([True]))[0]
    p = pricing.black76(np.array([F]), np.array([K]), np.array([T]), np.array([s]), r, np.array([False]))[0]
    assert c - p == pytest.approx(math.exp(-r * T) * (F - K), abs=1e-6)


def test_implied_vol_round_trips_across_strikes():
    F = np.full(5, 22000.0)
    K = np.array([20000.0, 21500.0, 22000.0, 22500.0, 24000.0])
    T = np.full(5, 20 / 365)
    sig = np.array([0.22, 0.16, 0.13, 0.12, 0.15])
    call = np.array([False, False, True, True, True])
    price = pricing.black76(F, K, T, sig, 0.065, call)
    assert np.allclose(pricing.implied_vol(price, F, K, T, 0.065, call), sig, atol=1e-5)


def test_a_price_below_intrinsic_has_no_implied_vol():
    iv = pricing.implied_vol(np.array([50.0]), np.array([22000.0]), np.array([21000.0]),
                             np.array([10 / 365]), 0.06, np.array([True]))
    assert np.isnan(iv[0])


def test_zero_time_has_no_implied_vol():
    iv = pricing.implied_vol(np.array([100.0]), np.array([22000.0]), np.array([22000.0]),
                             np.array([0.0]), 0.06, np.array([True]))
    assert np.isnan(iv[0])


def test_deltas_have_the_right_signs_and_sizes():
    F, K, T, s = np.array([22000.0] * 2), np.array([22000.0] * 2), np.array([30 / 365] * 2), np.array([0.13] * 2)
    g = pricing.greeks(F, K, T, s, 0.06, np.array([True, False]))
    assert 0.45 < g["delta"][0] < 0.55 and -0.55 < g["delta"][1] < -0.45
    assert g["gamma"][0] == pytest.approx(g["gamma"][1])
    assert g["vega"][0] > 0 and g["theta"][0] < 0
