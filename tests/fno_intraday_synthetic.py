"""A small, hand-checkable intraday option market for the intraday engine's tests.

NIFTY on Wed 1 Jan 2025 (P) and Thu 2 Jan 2025 (D). D is an expiry day: the
nearest weekly (E1) expires on D, so a "weekly, which 0" strategy trades a
0 DTE contract; the next weekly (E2) is 9 Jan. Strikes 23,300-23,700 every 100,
plus 23,000 and 24,000 for wings. Every option minute is Black-76 at 15% on a
flat forward of 23,500, time measured to 15:30 on the expiry; every candle is
flat (open = high = low = close) and trades 100 contracts (7,500 units at lot
75) - unless overridden. The index sits at 23,500 all day; India VIX at 14.
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from config import IST
from fno import pricing
from fno.contracts import LotSizes
from fno.events import Events
from fno.intraday_market import IntradayMarket
from fno.market import MarketData
from fno.minute import COLUMNS as MINUTE_COLUMNS
from fno.nse_files import COLUMNS, DTYPES

P, D = date(2025, 1, 1), date(2025, 1, 2)
E1, E2 = D, date(2025, 1, 9)
STRIKES = (23000.0, 23300.0, 23400.0, 23500.0, 23600.0, 23700.0, 24000.0)
FORWARD, SIGMA, RATE, LOT = 23500.0, 0.15, 0.058, 75
UNITS = 7500                                   # 100 contracts a minute


def stamp(day: date, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(day, time(h, m))).tz_localize(IST)


def session(day: date) -> list[pd.Timestamp]:
    return [stamp(day, "09:15") + pd.Timedelta(minutes=i) for i in range(375)]


def _years(t: pd.Timestamp, expiry: date) -> float:
    close = pd.Timestamp(datetime.combine(expiry, time(15, 30))).tz_localize(IST)
    return max((close - t).total_seconds(), 60.0) / (365 * 86400)


def model(t: pd.Timestamp, expiry: date, strike: float, typ: str) -> float:
    return round(float(pricing.black76(FORWARD, strike, _years(t, expiry), SIGMA, RATE, typ == "CE")), 2)


def options(expiry: date, overrides: dict | None = None) -> pd.DataFrame:
    """Every minute of every contract; `overrides` maps (day, "HH:MM", strike, type) to values."""
    stamps = pd.DatetimeIndex([t for day in (P, D) if day <= expiry for t in session(day)])
    close = pd.Timestamp(datetime.combine(expiry, time(15, 30))).tz_localize(IST)
    per_minute = np.maximum((close - stamps).total_seconds().to_numpy(), 60.0) / (365 * 86400)
    pairs = [(k, typ) for k in STRIKES for typ in ("CE", "PE")]
    stamps = np.repeat(stamps, len(pairs))
    years = np.repeat(per_minute, len(pairs))
    strikes = [k for k, _ in pairs] * (len(stamps) // len(pairs))
    types = [typ for _, typ in pairs] * (len(stamps) // len(pairs))
    price = np.round(pricing.black76(FORWARD, np.array(strikes), years, SIGMA, RATE,
                                     np.array(types) == "CE"), 2)
    frame = pd.DataFrame({"ts": stamps, "strike": strikes, "option_type": types, "open": price,
                          "high": price, "low": price, "close": price, "volume": UNITS, "oi": 1_000_000})
    frame = frame[list(MINUTE_COLUMNS)]
    for (day, hhmm, k, typ), values in (overrides or {}).items():
        at = (frame["ts"] == stamp(day, hhmm)) & (frame["strike"] == k) & (frame["option_type"] == typ)
        assert at.sum() == 1, (day, hhmm, k, typ)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame


def index(overrides: dict | None = None) -> pd.DataFrame:
    """The index's minutes, flat at 23,500; `overrides` maps (day, "HH:MM") to values."""
    stamps = session(P) + session(D)
    frame = pd.DataFrame({"ts": stamps, "open": FORWARD, "high": FORWARD, "low": FORWARD,
                          "close": FORWARD, "volume": 0.0})
    for (day, hhmm), values in (overrides or {}).items():
        at = frame["ts"] == stamp(day, hhmm)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame


def vix() -> pd.DataFrame:
    stamps = session(P) + session(D)
    return pd.DataFrame({"ts": stamps, "open": 14.0, "high": 14.0, "low": 14.0, "close": 14.0, "volume": 0.0})


def daily_rows() -> pd.DataFrame:
    """NSE's file for P and D: the options of both expiries, at the day's last minute."""
    rows = []
    for day in (P, D):
        last = stamp(day, "15:29")
        for expiry in (E1, E2):
            if expiry < day:
                continue
            for k in STRIKES:
                for typ in ("CE", "PE"):
                    p = model(last, expiry, k, typ)
                    rows.append({"trade_date": day, "underlying": "NIFTY", "instrument": "OPT",
                                 "expiry": expiry, "strike": k, "option_type": typ, "open": p,
                                 "high": p, "low": p, "close": p, "settle": p, "volume_contracts": 1000,
                                 "oi_units": 100_000, "oi_change_units": 0, "turnover_inr": 0.0,
                                 "lot_size": float(LOT), "underlying_price": FORWARD})
    return pd.DataFrame(rows, columns=list(COLUMNS)).astype(DTYPES)


def facts(**columns: list) -> pd.DataFrame:
    base = {"trade_date": [P, D], "vix": [14.0, 14.0], "iv_rank": [50.0, 50.0],
            "event_today": [False, False]}
    base.update(columns)
    return pd.DataFrame(base)


def spot() -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [P, D], "open": FORWARD, "high": FORWARD, "low": FORWARD,
                         "close": FORWARD})


def market(*, option_overrides: dict | None = None, index_overrides: dict | None = None,
           facts_frame: pd.DataFrame | None = None, with_options: bool = True) -> IntradayMarket:
    daily = MarketData.from_frames("NIFTY", daily=daily_rows(),
                                   facts=facts() if facts_frame is None else facts_frame, spot=spot(),
                                   lots=LotSizes({"NIFTY": ((date(2016, 1, 1), LOT),)}), events=Events(()))
    books = {E1: options(E1, option_overrides), E2: options(E2)} if with_options else {}
    return IntradayMarket.from_frames(daily, options=books, index=index(index_overrides), vix=vix())
