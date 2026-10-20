"""Paper trading momentum + the cash rule, forward from a fixed start (no real money)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import momentum_paper as mp  # noqa: E402


def tri(days=700, mom=0.001, nifty=0.0005):
    idx = pd.bdate_range("2025-01-01", periods=days).date
    return pd.DataFrame({mp.MOMENTUM: 100 * np.exp(mom * np.arange(days)),
                         mp.NIFTY50: 100 * np.exp(nifty * np.arange(days))}, index=idx)


def test_before_the_first_month_ends_the_account_waits_in_cash():
    t = tri()
    start = t.index[400]
    end = [d for d in t.index if d.month == start.month and d.year == start.year][-1]
    book = mp.book(t.loc[:end], start=start)
    assert book["state"] == "cash" and book["value"] == pytest.approx(mp.CAPITAL, rel=0.01)
    assert book["decisions"] == []


def test_a_rising_market_is_entered_the_session_after_the_month_ends():
    t = tri()
    book = mp.book(t, start=t.index[400])
    first = book["decisions"][0]
    assert first["action"] == "buy the Momentum 30 index"
    assert date.fromisoformat(first["filled"]) > date.fromisoformat(first["decided"])
    assert book["state"] == "invested"
    assert book["value"] > mp.CAPITAL and book["momentum_hold"] > book["value"]   # it waited a month


def test_the_book_compares_with_holding_both_indexes_from_the_same_day():
    t = tri()
    book = mp.book(t, start=t.index[400])
    days = (t.index[-1] - t.index[400]).days
    assert book["nifty_hold"] == pytest.approx(mp.CAPITAL * t[mp.NIFTY50].iloc[-1] / t[mp.NIFTY50].iloc[400])
    assert book["as_of"] == t.index[-1].isoformat() and len(book["equity"]) > days / 2


def test_a_new_month_is_announced_once_on_its_first_session():
    t = tri()
    first_of_month = next(d for d in t.index[401:] if d.month != t.index[400].month)
    assert mp.month_turned(t.loc[:first_of_month])
    after = t.index[list(t.index).index(first_of_month) + 1]
    assert not mp.month_turned(t.loc[:after])


def test_before_the_start_date_the_book_says_so():
    t = tri()
    b = mp.book(t, start=date(2030, 1, 1))
    assert b["state"] == "not started" and b["value"] == mp.CAPITAL
    assert "starts 2030-01-01" in mp.message(b)


def test_the_curve_carries_both_holdings_from_the_same_rupees():
    t = tri()
    b = mp.book(t, start=t.index[400])
    first, last = b["equity"][0], b["equity"][-1]
    assert first[1:] == [mp.CAPITAL, mp.CAPITAL, mp.CAPITAL]
    assert last[2] == pytest.approx(b["momentum_hold"]) and last[3] == pytest.approx(b["nifty_hold"])
