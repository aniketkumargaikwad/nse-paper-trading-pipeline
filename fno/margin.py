"""How much margin a position blocks: a SPAN-style estimate (design §4).

NOT the exchange's SPAN. The underlying is scanned +-SCAN (wider when India
VIX is high) at seven points, and volatility +-VOL_SCAN, repricing every leg
with Black-76; the worst loss is the "SPAN" part.

* Only bought options: the premium paid.
* Every sold option covered by a bought one of the same type (a spread, a
  condor): the larger of the scan loss and the maximum loss at expiry -
  brokers block about the maximum loss on a hedged position.
* Otherwise: the scan loss plus the exposure margin (NSE's "extreme loss
  margin") on the sold notional: 2%, or 3% on an option more than 10% out of
  the money.
* From 20 Nov 2024 SEBI adds 2% on short index options on their expiry day
  (circular SEBI/HO/MRD/TPD-1/P/CIR/2024/132), sold legs of hedged positions
  included.

Checked 5 Oct 2026: NSE's index futures SPAN runs about 6-7% of notional,
which the scan floor matches; a NIFTY short straddle comes to about
Rs 0.8-1.0 lakh per lot of 50 in 2023 by these rules. Brokers' forums quote
Rs 1.3-1.4 lakh (undated, may include a broker's own add-on).

The constants are starting values. Step 5 compares the estimate with the
broker's calculator on the live chain and states the error on every report;
until then every report says the margin is uncalibrated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import numpy as np

from fno.pricing import black76

SCAN_FLOOR_PCT = 6.0
SCAN_PER_VIX_POINT = 0.5          # scan, % of the underlying = max(floor, 0.5 x VIX)
VOL_SCAN = 0.04                   # absolute: 15% -> 11% and 19%
EXPOSURE_PCT = 2.0
DEEP_OTM_PCT = 10.0               # out of the money by more than this ...
DEEP_OTM_EXPOSURE_PCT = 3.0       # ... pays this instead
EXPIRY_DAY_EXPOSURE_PCT = 2.0
EXPIRY_DAY_FROM = date(2024, 11, 20)
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


def scan_loss(legs: Sequence[MarginLeg], *, forward: float, years: float, rate: float,
              vix: float) -> float:
    """The worst loss over the price and volatility scan: the SPAN-like part."""
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
    return worst


def _exposure_pct(leg: MarginLeg, forward: float) -> float:
    out_of_money = (leg.strike / forward - 1 if leg.is_call else 1 - leg.strike / forward) * 100
    return DEEP_OTM_EXPOSURE_PCT if out_of_money > DEEP_OTM_PCT else EXPOSURE_PCT


def estimate(legs: Sequence[MarginLeg], *, forward: float, years: float, rate: float, vix: float,
             fill_day: date | None = None, expiry: date | None = None) -> float:
    """Margin for the position; `fill_day` == `expiry` adds the expiry-day charge."""
    if all(x.sign > 0 for x in legs):
        return sum(x.price * x.units for x in legs)
    sold = [x for x in legs if x.sign < 0]
    expiry_day = (fill_day is not None and fill_day == expiry and fill_day >= EXPIRY_DAY_FROM)
    extra = (EXPIRY_DAY_EXPOSURE_PCT / 100 * forward * sum(x.units for x in sold)) if expiry_day else 0.0
    worst = scan_loss(legs, forward=forward, years=years, rate=rate, vix=vix)
    if _covered(legs):
        return max(worst, max_loss(legs)) + extra
    exposure = sum(_exposure_pct(x, forward) / 100 * forward * x.units for x in sold)
    return worst + exposure + extra
