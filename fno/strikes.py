"""Which expiry and which strikes a strategy's legs land on, on a decision day.

Only the decision day's rows are read - a ChainSnapshot, or that day's file -
so the choice cannot see a later price.

MONTHLY is an expiry that has a future that day (index futures are monthly
only); WEEKLY is every option expiry, so before NIFTY's weeklies began (Feb
2019) and after BANKNIFTY's ended (Nov 2024) "weekly" lands on the monthly.
`which` counts from the nearest expiry with at least a day to run.

STRIKES are always chosen among the strikes listed for that expiry and type,
nearest first; a tie goes to the lower strike.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from fno.chain import ChainSnapshot, expiries


class NoStrike(LookupError):
    """A leg's rule has no strike on that day's chain."""


def expiries_of(day_rows: pd.DataFrame, day: date, cycle: str) -> list[date]:
    listed = [e for e in expiries(day_rows, day) if e > day]
    if cycle == "monthly":
        futures = set(day_rows.loc[(day_rows["trade_date"] == day)
                                   & (day_rows["instrument"] == "FUT"), "expiry"])
        listed = [e for e in listed if e in futures]
    return listed


def pick_expiry(day_rows: pd.DataFrame, day: date, cycle: str, which: int) -> date | None:
    listed = expiries_of(day_rows, day, cycle)
    return listed[which] if which < len(listed) else None


def _nearest(values: pd.Series, target: float):
    return (values - target).abs().idxmin()


def pick_strikes(snap: ChainSnapshot, legs: Sequence) -> list[float]:
    """One strike per leg, in order (a `from_leg` wing reads an earlier choice)."""
    rows = snap.rows[snap.rows["settle"] > 0]
    chosen: list[float] = []
    for i, leg in enumerate(legs):
        side = rows[rows["option_type"] == leg.type].sort_values("strike").reset_index(drop=True)
        if side.empty:
            raise NoStrike(f"leg {i}: no {leg.type} listed for {snap.expiry} on {snap.day}")
        strikes = side["strike"]
        if leg.rule in ("atm", "otm_pct") and not np.isfinite(snap.forward):
            raise NoStrike(f"leg {i}: no forward price for {snap.expiry} on {snap.day}")
        if leg.rule == "atm":
            at = int(_nearest(strikes, snap.forward))
            j = at + leg.value if leg.type == "CE" else at - leg.value
            if not 0 <= j < len(side):
                raise NoStrike(f"leg {i}: {leg.value} strikes from the money is beyond the listed strikes")
            strike = strikes[j]
        elif leg.rule == "otm_pct":
            move = 1 + leg.value / 100 if leg.type == "CE" else 1 - leg.value / 100
            strike = strikes[_nearest(strikes, snap.forward * move)]
        elif leg.rule == "delta":
            with_delta = side[side["delta"].notna()]
            if with_delta.empty:
                raise NoStrike(f"leg {i}: no {leg.type} has a delta (no IV) on {snap.day}")
            strike = with_delta.loc[_nearest(with_delta["delta"], leg.value), "strike"]
        elif leg.rule == "premium":
            strike = side.loc[_nearest(side["settle"], leg.value), "strike"]
        elif leg.rule == "max_oi":
            strike = side.loc[side["oi_units"].idxmax(), "strike"]
        else:                                   # from_leg
            j, offset = leg.value
            strike = strikes[_nearest(strikes, chosen[j] + offset)]
        chosen.append(float(strike))
    # Legs that land on one contract from opposite sides cancel: no position at all,
    # a margin near zero, and sizing that divides by it (BANKNIFTY 28 Nov 2024, an
    # iron fly's wing on its own sold strike when few strikes had traded).
    for i, a in enumerate(legs):
        for j in range(i):
            b = legs[j]
            if a.type == b.type and chosen[i] == chosen[j] and a.side != b.side:
                raise NoStrike(f"legs {j} and {i} land on the same {a.type} {chosen[i]:g} from opposite "
                               f"sides on {snap.day} and cancel")
    return chosen
