"""Black-76 prices, implied volatility and Greeks for European index options.

Black-76 prices off the FORWARD, which for NSE index options is the
same-expiry future (or put-call parity when a weekly expiry has none), so
dividends never need estimating. Vectorised over numpy arrays: one day's
whole chain is solved at once.

No SciPy: norm_cdf is Abramowitz-Stegun 7.1.26 for erf, error < 1.5e-7.
Delta is forward delta; vega is per 1.00 of volatility; theta is per
calendar day.
"""

from __future__ import annotations

import numpy as np

_SQRT2 = np.sqrt(2.0)
IV_LOW, IV_HIGH, IV_ITER = 1e-4, 5.0, 80


def _erf(x: np.ndarray) -> np.ndarray:
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
           + t * (-1.453152027 + t * 1.061405429))))
    return sign * (1.0 - poly * np.exp(-a * a))


def norm_cdf(x) -> np.ndarray:
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / _SQRT2))


def norm_pdf(x) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def _d1d2(F, K, T, sigma):
    vol_t = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / vol_t
    return d1, d1 - vol_t


def black76(F, K, T, sigma, r, is_call) -> np.ndarray:
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    disc = np.exp(-r * T)
    d1, d2 = _d1d2(F, K, T, sigma)
    call = disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
    put = disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))
    return np.where(np.asarray(is_call), call, put)


def implied_vol(price, F, K, T, r, is_call) -> np.ndarray:
    """Bisection on [IV_LOW, IV_HIGH]; NaN where no volatility fits the price."""
    price, F, K, T = (np.asarray(a, dtype=float) for a in (price, F, K, T))
    is_call = np.asarray(is_call)
    disc = np.exp(-r * T)
    intrinsic = disc * np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    ok = (T > 0) & (price > intrinsic) & np.isfinite(price) & (F > 0) & (K > 0)
    t_safe = np.where(ok, T, 1.0)
    f_safe = np.where(ok, F, 1.0)
    k_safe = np.where(ok, K, 1.0)
    lo = np.full(price.shape, IV_LOW)
    hi = np.full(price.shape, IV_HIGH)
    ok &= black76(f_safe, k_safe, t_safe, hi, r, is_call) >= price
    for _ in range(IV_ITER):
        mid = 0.5 * (lo + hi)
        above = black76(f_safe, k_safe, t_safe, mid, r, is_call) > price
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    return np.where(ok, 0.5 * (lo + hi), np.nan)


def greeks(F, K, T, sigma, r, is_call) -> dict[str, np.ndarray]:
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    is_call = np.asarray(is_call)
    disc = np.exp(-r * T)
    d1, d2 = _d1d2(F, K, T, sigma)
    pdf = norm_pdf(d1)
    delta = np.where(is_call, disc * norm_cdf(d1), -disc * norm_cdf(-d1))
    gamma = disc * pdf / (F * sigma * np.sqrt(T))
    vega = disc * F * pdf * np.sqrt(T)
    price = black76(F, K, T, sigma, r, is_call)
    theta_year = -disc * F * pdf * sigma / (2.0 * np.sqrt(T)) + r * price
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta_year / 365.0}
