"""The option chain as it stood on a day (design §2.5).

Only rows with trade_date == the day are ever read, so nothing later can
reach a snapshot. IV and Greeks are computed from settlement prices (always
present; NSE's theoretical price for an untraded contract) so the surface
is complete. Whether a contract TRADED is its volume - fills (step 2b) use
traded prices only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from fno import pricing, store


@dataclass(frozen=True)
class ChainSnapshot:
    underlying: str
    day: date
    expiry: date
    dte: int                  # calendar days to expiry
    forward: float
    spot: float               # NaN when unknown
    rate: float
    rows: pd.DataFrame        # options of this expiry, with iv/delta/gamma/vega/theta


def expiries(day_rows: pd.DataFrame, day: date) -> list[date]:
    exp = day_rows.loc[(day_rows["trade_date"] == day) & (day_rows["instrument"] == "OPT"), "expiry"]
    return sorted(e for e in set(exp) if e >= day)


def forward(day_rows: pd.DataFrame, expiry: date, day: date, rate: float) -> float:
    rows = day_rows[(day_rows["trade_date"] == day) & (day_rows["expiry"] == expiry)]
    fut = rows[(rows["instrument"] == "FUT") & (rows["settle"] > 0)]
    if not fut.empty:
        return float(fut["settle"].iloc[0])
    opts = rows[rows["instrument"] == "OPT"]
    calls = opts[opts["option_type"] == "CE"].set_index("strike")["settle"]
    puts = opts[opts["option_type"] == "PE"].set_index("strike")["settle"]
    both = pd.concat([calls.rename("c"), puts.rename("p")], axis=1).dropna()
    both = both[(both["c"] > 0) & (both["p"] > 0)]
    if both.empty:
        return float("nan")
    k = (both["c"] - both["p"]).abs().idxmin()
    T = (expiry - day).days / 365
    return float(k + np.exp(rate * T) * (both.loc[k, "c"] - both.loc[k, "p"]))


def snapshot(day_rows: pd.DataFrame, underlying: str, day: date, expiry: date,
             *, spot: float, rate: float) -> ChainSnapshot:
    rows = day_rows[(day_rows["trade_date"] == day) & (day_rows["underlying"] == underlying)
                    & (day_rows["instrument"] == "OPT") & (day_rows["expiry"] == expiry)].copy()
    F = forward(day_rows[day_rows["underlying"] == underlying], expiry, day, rate)
    dte = (expiry - day).days
    T = np.full(len(rows), dte / 365)
    is_call = (rows["option_type"] == "CE").to_numpy()
    Fs = np.full(len(rows), F)
    K = rows["strike"].to_numpy(dtype=float)
    iv = pricing.implied_vol(rows["settle"].to_numpy(dtype=float), Fs, K, T, rate, is_call)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = pricing.greeks(Fs, K, T, iv, rate, is_call)
    rows["iv"] = iv
    for name, values in g.items():
        rows[name] = np.where(np.isfinite(iv), values, np.nan)
    rows = rows.sort_values(["strike", "option_type"]).reset_index(drop=True)
    return ChainSnapshot(underlying, day, expiry, dte, F, float(spot), rate, rows)


def chain_at(root: Path, underlying: str, day: date, expiry: date | None = None,
             *, spot: float, rate: float) -> ChainSnapshot:
    """The chain for `expiry` (default: nearest with a day to run) as it stood on `day`."""
    day_rows = store.read_daily(root, underlying, day, day)
    if expiry is None:
        later = [e for e in expiries(day_rows, day) if e > day]
        if not later:
            raise LookupError(f"{underlying} {day}: no expiry with a day to run")
        expiry = later[0]
    return snapshot(day_rows, underlying, day, expiry, spot=spot, rate=rate)
