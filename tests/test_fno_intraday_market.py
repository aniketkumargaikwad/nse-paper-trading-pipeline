"""The intraday market: minutes, the previous session, the chain at a minute (design 2026-10-19)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno_intraday_synthetic import D, E1, LOT, P, RATE, market, stamp  # noqa: E402


def test_the_previous_session_and_the_first_minute_day():
    m = market()
    assert m.previous_session(D) == P and m.previous_session(P) is None
    assert m.first_day == P


def test_index_bars_carry_the_warm_up_sessions_before_the_day():
    bars = market().index_bars(D)
    assert len(bars) == 750 and bars.index[0] == stamp(P, "09:15") and bars.index[-1] == stamp(D, "15:29")


def test_the_chain_at_a_minute_has_deltas_even_on_expiry_day():
    snap = market().chain(D, E1, stamp(D, "09:20"), rate=RATE, lot=LOT)
    atm = snap.rows[snap.rows["strike"] == 23500.0].set_index("option_type")
    assert snap.dte == 0 and abs(snap.forward - 23500.0) < 1.0
    assert 0.4 < atm.loc["CE", "delta"] < 0.6 and -0.6 < atm.loc["PE", "delta"] < -0.4
    assert (atm["settle"] > 0).all()                     # far wings are worth 0.00 on expiry morning


def test_the_chain_reads_nothing_after_its_minute():
    planted = {(D, "09:21", 23500.0, "CE"): {"close": 999.0}}
    snap = market(option_overrides=planted).chain(D, E1, stamp(D, "09:20"), rate=RATE, lot=LOT)
    ce = snap.rows[(snap.rows["strike"] == 23500.0) & (snap.rows["option_type"] == "CE")]
    assert float(ce["settle"].iloc[0]) < 100.0
