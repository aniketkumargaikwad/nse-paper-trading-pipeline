"""Momentum rotation (design 2026-10-20): ranking, fills, costs, the trend filter."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.rotation import Variant, month_ends, run  # noqa: E402


def market(days=600, drift=None, seed=0):
    """Business days from 2020; stock i grows at drift[i] a day."""
    idx = pd.bdate_range("2020-01-01", periods=days).date
    drift = drift or {"A": 0.002, "B": 0.001, "C": 0.0, "D": -0.001}
    data = {s: 100 * np.exp(r * np.arange(days)) for s, r in drift.items()}
    data["^NSEI"] = 100 * np.exp(0.0005 * np.arange(days))
    return pd.DataFrame(data, index=idx)


def test_month_ends_are_the_last_session_of_each_month():
    idx = pd.bdate_range("2021-01-25", "2021-03-03").date
    assert month_ends(idx) == [date(2021, 1, 29), date(2021, 2, 26)]


def test_the_strongest_stocks_are_held_and_filled_the_next_session():
    closes = market()
    r = run(closes, Variant("t", lookback=6, skip=0, top=2, trend=False), start=date(2020, 9, 1),
            end=date(2022, 4, 1), cost_pct=0.0)
    first = r.rebalances[0]
    assert set(first.holdings) == {"A", "B"}
    assert first.filled > first.decided          # never the decision day's own price


def test_costs_are_charged_on_what_is_traded():
    closes = market()
    v = Variant("t", lookback=6, skip=0, top=2, trend=False)
    free = run(closes, v, start=date(2020, 9, 1), end=date(2022, 4, 1), cost_pct=0.0)
    paid = run(closes, v, start=date(2020, 9, 1), end=date(2022, 4, 1), cost_pct=0.2)
    assert paid.equity.iloc[-1] < free.equity.iloc[-1]
    # the first buy of the whole account costs 0.2% of it
    assert paid.rebalances[0].cost == pytest.approx(0.002 * paid.capital, rel=1e-6)


def test_a_decision_never_sees_a_later_price():
    closes = market()
    v = Variant("t", lookback=6, skip=0, top=2, trend=False)
    base = run(closes, v, start=date(2020, 9, 1), end=date(2022, 4, 1), cost_pct=0.0)
    planted = closes.copy()
    later = [d for d in planted.index if d > base.rebalances[0].decided]
    planted.loc[later, "D"] *= 50                  # D soars only AFTER the first decision
    seen = run(planted, v, start=date(2020, 9, 1), end=date(2022, 4, 1), cost_pct=0.0)
    assert seen.rebalances[0].holdings == base.rebalances[0].holdings


def test_the_trend_filter_goes_to_cash_when_the_index_is_below_its_average():
    closes = market()
    closes["^NSEI"] = 100 * np.exp(-0.001 * np.arange(len(closes)))     # a falling index
    r = run(closes, Variant("t", lookback=6, skip=0, top=2, trend=True), start=date(2020, 12, 1),
            end=date(2022, 4, 1), cost_pct=0.0)
    assert all(not rb.holdings for rb in r.rebalances)
    assert r.equity.iloc[-1] == pytest.approx(r.capital)


def test_a_stock_without_a_full_lookback_is_not_ranked():
    closes = market()
    closes.loc[closes.index[:300], "A"] = np.nan     # A lists late
    r = run(closes, Variant("t", lookback=12, skip=1, top=2, trend=False), start=date(2021, 1, 1),
            end=date(2021, 6, 1), cost_pct=0.0)
    assert "A" not in r.rebalances[0].holdings


def test_stats_read_months_and_years_against_the_benchmark():
    from research.rotation import month_returns, stats
    closes = market()
    r = run(closes, Variant("t", lookback=6, skip=0, top=1, trend=False), start=date(2020, 9, 1),
            end=date(2022, 4, 1), cost_pct=0.0)
    m = month_returns(r.equity)
    assert m.index[0] == "2020-09" and len(m) == 20
    s = stats(r, closes["^NSEI"])
    assert s["cagr_pct"] > s["bench_cagr_pct"] and s["years_beating_pct"] == 100
    assert s["worst_fall_pct"] == pytest.approx(0.0, abs=0.01)


def test_missing_index_days_do_not_blank_the_trend_filter():
    closes = market(days=700)
    gaps = closes.index[::7]
    closes.loc[gaps, "^NSEI"] = np.nan                  # the index misses some sessions
    r = run(closes, Variant("t", lookback=6, skip=0, top=2, trend=True), start=date(2021, 1, 1),
            end=date(2022, 6, 1), cost_pct=0.0)
    assert all(rb.holdings for rb in r.rebalances)      # a rising index: always invested
