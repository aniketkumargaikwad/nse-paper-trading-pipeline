"""A small, hand-checkable NIFTY option market for the F&O engine's tests.

Six sessions in January 2024; a weekly expiry on Thursday 4 Jan, the next on
11 Jan, the monthly (with its future) on 25 Jan; strikes 21,000-22,000 every
100. Every price is Black-76 at 15% volatility on a flat forward of 21,500,
so the chain behaves like a real one (deltas, put-call parity), and single
prices can be overridden to stage a fill, a stop or a gap. On its expiry day
an option's settlement price is 0, as in NSE's old file. NIFTY closes at
21,560 on 4 Jan: the weekly's final settlement.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from fno import pricing
from fno.contracts import LotSizes
from fno.events import Events
from fno.nse_files import COLUMNS, DTYPES

D1, D2, D3, D4, D5, D6 = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3),
                          date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8))
DAYS = (D1, D2, D3, D4, D5, D6)
E1, E2, MONTHLY = date(2024, 1, 4), date(2024, 1, 11), date(2024, 1, 25)
STRIKES = tuple(float(k) for k in range(21000, 22001, 100))
FORWARD = 21500.0
SIGMA = 0.15
RATE = 0.068            # fno/rates.yaml for 2024, so parity gives back FORWARD
LOT = 50
EXPIRY_SPOT = 21560.0


def model(day: date, expiry: date, strike: float, typ: str) -> float:
    T = (expiry - day).days / 365
    if T <= 0:
        return max(FORWARD - strike, 0.0) if typ == "CE" else max(strike - FORWARD, 0.0)
    return round(float(pricing.black76(FORWARD, strike, T, SIGMA, RATE, typ == "CE")), 2)


def _row(d: date, e: date, instrument: str, strike: float, typ: Any, price: float, settle: float) -> dict:
    return {"trade_date": d, "underlying": "NIFTY", "instrument": instrument, "expiry": e,
            "strike": strike, "option_type": typ, "open": price, "high": price, "low": price,
            "close": price, "settle": settle, "volume_contracts": 1000, "oi_units": 100_000,
            "oi_change_units": 0, "turnover_inr": 0.0, "lot_size": np.nan, "underlying_price": np.nan}


def daily(overrides: dict | None = None) -> pd.DataFrame:
    """Every contract on every day; `overrides` maps (day, expiry, strike, type) to column values."""
    rows = []
    for d in DAYS:
        for e in (E1, E2, MONTHLY):
            if e < d:
                continue
            for k in STRIKES:
                for typ in ("CE", "PE"):
                    p = model(d, e, k, typ)
                    rows.append(_row(d, e, "OPT", k, typ, p, 0.0 if d == e else p))
        rows.append(_row(d, MONTHLY, "FUT", np.nan, None, FORWARD, FORWARD))
    frame = pd.DataFrame(rows, columns=list(COLUMNS))
    for (d, e, k, typ), values in (overrides or {}).items():
        at = ((frame["trade_date"] == d) & (frame["expiry"] == e)
              & (frame["strike"] == k) & (frame["option_type"] == typ))
        assert at.sum() == 1, (d, e, k, typ)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame.astype(DTYPES)


def facts(**columns: list) -> pd.DataFrame:
    """One facts row per session: VIX 14, IV rank 50, no events - unless overridden."""
    base = {"trade_date": list(DAYS), "vix": [14.0] * 6, "iv_rank": [50.0] * 6,
            "event_today": [False] * 6,
            "dte": [float((E1 - d).days if d < E1 else (E2 - d).days) for d in DAYS]}
    base.update(columns)
    return pd.DataFrame(base)


def spot(expiry_close: float = EXPIRY_SPOT) -> pd.DataFrame:
    close = [expiry_close if d == E1 else FORWARD for d in DAYS]
    return pd.DataFrame({"trade_date": list(DAYS), "open": close, "high": close,
                         "low": close, "close": close})


def market(*, overrides: dict | None = None, facts_frame: pd.DataFrame | None = None,
           spot_frame: pd.DataFrame | None = None, events: tuple = (), until: date | None = None):
    from fno.market import MarketData

    return MarketData.from_frames(
        "NIFTY", daily=daily(overrides),
        facts=facts() if facts_frame is None else facts_frame,
        spot=spot() if spot_frame is None else spot_frame,
        lots=LotSizes({"NIFTY": ((date(2016, 1, 1), LOT),)}),
        events=Events(tuple({"date": d, "kind": k} for d, k in events)),
        until=until)
