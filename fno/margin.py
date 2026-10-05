"""How much margin a position blocks: a SPAN-style estimate (design §4).

NOT the exchange's SPAN. The underlying is scanned +-SCAN (wider when India
VIX is high) at seven points, and volatility +-VOL_SCAN, repricing every leg
with Black-76; the worst loss is the "SPAN" part.

* Only bought options: the premium paid.
* Every sold option covered by a bought one of the same type (a spread, a
  condor): the larger of the scan loss and the maximum loss at expiry -
  brokers block about the maximum loss on a hedged position.
* Otherwise: the scan loss plus EXPOSURE_PCT of the sold notional.

The constants are starting values. Step 5 compares the estimate with the
broker's calculator on the live chain and states the error on every report;
until then every report says the margin is uncalibrated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from fno.pricing import black76

SCAN_FLOOR_PCT = 6.0
SCAN_PER_VIX_POINT = 0.5          # scan, % of the underlying = max(floor, 0.5 x VIX)
VOL_SCAN = 0.04                   # absolute: 15% -> 11% and 19%
EXPOSURE_PCT = 2.0
MOVES = (-1.0, -2 / 3, -1 / 3, 0.0, 1 / 3, 2 / 3, 1.0)
MIN_VOL = 0.01


@dataclass(frozen=True)
class MarginLeg:
    sign: int          # +1 bought, -1 sold
    is_call: bool
    strike: float
    units: int         # lots x lot size
    iv: float
    price: float       # premium per unit at the decision


def scan_pct(vix: float) -> float:
    return SCAN_FLOOR_PCT if vix != vix else max(SCAN_FLOOR_PCT, SCAN_PER_VIX_POINT * vix)


def _payoff(legs: Sequence[MarginLeg], level: float) -> float:
    cost = sum(x.sign * x.price * x.units for x in legs)
    value = sum(x.sign * x.units * (max(level - x.strike, 0.0) if x.is_call else max(x.strike - level, 0.0))
                for x in legs)
    return value - cost


def max_loss(legs: Sequence[MarginLeg]) -> float:
    """The worst loss at expiry; infinite when sold calls outnumber bought ones."""
    if sum(x.sign * x.units for x in legs if x.is_call) < 0:
        return math.inf
    # The payoff is straight between strikes and does not fall above the top
    # one, so its lowest point is at 0 or at a strike.
    worst = min(_payoff(legs, level) for level in (0.0, *sorted({x.strike for x in legs})))
    return max(0.0, -worst)


def _covered(legs: Sequence[MarginLeg]) -> bool:
    calls = sum(x.sign * x.units for x in legs if x.is_call)
    puts = sum(x.sign * x.units for x in legs if not x.is_call)
    return calls >= 0 and puts >= 0


def estimate(legs: Sequence[MarginLeg], *, forward: float, years: float, rate: float, vix: float) -> float:
    if all(x.sign > 0 for x in legs):
        return sum(x.price * x.units for x in legs)
    T = max(years, 1 / 365)
    K = np.array([x.strike for x in legs])
    calls = np.array([x.is_call for x in legs])
    iv = np.array([x.iv for x in legs])
    weight = np.array([x.sign * x.units for x in legs], dtype=float)
    base = float(weight @ black76(forward, K, T, iv, rate, calls))
    scan = scan_pct(vix) / 100
    worst = 0.0
    for move in MOVES:
        for shift in (VOL_SCAN, -VOL_SCAN):
            vol = np.maximum(iv + shift, MIN_VOL)
            value = float(weight @ black76(forward * (1 + move * scan), K, T, vol, rate, calls))
            worst = max(worst, base - value)
    if _covered(legs):
        return max(worst, max_loss(legs))
    sold = sum(x.units for x in legs if x.sign < 0)
    return worst + EXPOSURE_PCT / 100 * forward * sold
