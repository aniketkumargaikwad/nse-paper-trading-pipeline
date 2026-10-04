"""One day's chain with IV and Greeks, never anything later (design §2.5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain, pricing, store  # noqa: E402
from fno.nse_files import COLUMNS  # noqa: E402

DAY = date(2026, 9, 1)
EXP = date(2026, 9, 29)          # 28 days
R = 0.054
F_TRUE = 22000.0
SIGMA = 0.14


def synthetic_day(day=DAY, *, with_future=True, planted=False):
    """A clean chain priced with Black-76 at one volatility."""
    strikes = np.arange(20000, 24050, 100.0)
    T = (EXP - day).days / 365
    rows = []
    for k in strikes:
        for is_call, typ in ((True, "CE"), (False, "PE")):
            px = float(pricing.black76(np.array([F_TRUE]), np.array([k]), np.array([T]),
                                       np.array([SIGMA]), R, np.array([is_call]))[0])
            rows.append({"trade_date": day, "underlying": "NIFTY", "instrument": "OPT", "expiry": EXP,
                         "strike": k, "option_type": typ, "open": px, "high": px, "low": px,
                         "close": px, "settle": px, "volume_contracts": 100,
                         "oi_units": int(1000 + abs(k - F_TRUE)), "oi_change_units": 0,
                         "turnover_inr": 0.0, "lot_size": 65.0, "underlying_price": 21900.0})
    if with_future:
        rows.append({**rows[0], "instrument": "FUT", "strike": np.nan, "option_type": np.nan,
                     "close": F_TRUE, "settle": F_TRUE, "open": F_TRUE, "high": F_TRUE, "low": F_TRUE})
    frame = pd.DataFrame(rows)[list(COLUMNS)]
    if planted:   # a later day whose prices must never leak into DAY
        later = frame.copy()
        later["trade_date"] = date(2026, 9, 2)
        later["settle"] = later["settle"] * 3
        frame = pd.concat([frame, later], ignore_index=True)
    return frame


def test_expiries_listed_on_a_day_are_sorted_and_future_dated():
    rows = synthetic_day()
    assert chain.expiries(rows, DAY) == [EXP]


def test_the_forward_is_the_same_expiry_future_when_there_is_one():
    assert chain.forward(synthetic_day(), EXP, DAY, R) == F_TRUE


def test_without_a_future_the_forward_comes_from_put_call_parity():
    assert chain.forward(synthetic_day(with_future=False), EXP, DAY, R) == pytest.approx(F_TRUE, rel=1e-6)


def test_the_snapshot_recovers_the_volatility_it_was_priced_with():
    snap = chain.snapshot(synthetic_day(), "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    near = snap.rows[(snap.rows["strike"] - F_TRUE).abs() <= 1000]
    assert np.allclose(near["iv"], SIGMA, atol=1e-4)
    assert snap.dte == 28 and snap.forward == F_TRUE and snap.spot == 21900.0
    assert {"delta", "gamma", "vega", "theta"} <= set(snap.rows.columns)


def test_a_later_day_never_reaches_the_snapshot(tmp_path):
    frame = synthetic_day(planted=True)
    store.write_day(tmp_path, frame[frame["trade_date"] == DAY], DAY)
    store.write_day(tmp_path, frame[frame["trade_date"] == date(2026, 9, 2)], date(2026, 9, 2))
    snap = chain.chain_at(tmp_path, "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    assert (snap.rows["trade_date"] == DAY).all()
    assert np.allclose(snap.rows["iv"].dropna(), SIGMA, atol=1e-3)


def test_on_expiry_day_iv_is_undefined():
    day_rows = synthetic_day()
    day_rows["trade_date"] = EXP
    snap = chain.snapshot(day_rows, "NIFTY", EXP, EXP, spot=21900.0, rate=R)
    assert snap.dte == 0 and snap.rows["iv"].isna().all()
