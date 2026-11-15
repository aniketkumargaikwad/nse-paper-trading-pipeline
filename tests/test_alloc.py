"""Index allocation (research/alloc.py): point in time, cash for what the filters reject, honest charges."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import alloc  # noqa: E402
from research.alloc import BENCHMARK, Plan, PlanError  # noqa: E402

UP, DOWN = "NIFTY200 MOMENTUM 30", "NIFTY IT"


def levels(days=900):
    """NIFTY 50 and UP rise steadily; DOWN rises for a year, then falls."""
    idx = pd.bdate_range("2005-01-03", periods=days).date
    n = np.arange(days)
    return pd.DataFrame({
        BENCHMARK: 100 * 1.0004 ** n,
        UP: 100 * 1.0008 ** n,
        DOWN: np.where(n < 260, 100 * 1.001 ** n, 100 * 1.001 ** 260 * 0.999 ** (n - 260)),
    }, index=idx)


def series(frame):
    return {c: frame[c].dropna() for c in frame.columns}


def day_at(frame, i):
    return frame.index[i]


def test_week_ends_leaves_the_running_week_out():
    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6, 9, 10)]
    assert alloc.week_ends(days) == [date(2026, 11, 6)]


def test_ranking_picks_the_stronger_index_and_none_keeps_the_listed_order():
    f = levels()
    d = day_at(f, 700)                      # DOWN has been falling for over a year
    assert alloc.target(series(f), d, Plan("p", (DOWN, UP), 1)) == {UP: 1.0}
    assert alloc.target(series(f), d, Plan("p", (DOWN, UP), 1, rank_by="none")) == {DOWN: 1.0}


def test_an_index_its_filter_rejects_leaves_its_share_in_cash():
    f = levels()
    d = day_at(f, 700)
    both = Plan("p", (DOWN, UP), 2, own_average=200)
    assert alloc.target(series(f), d, both) == {UP: 0.5}          # DOWN is below its own average
    cash_test = Plan("p", (DOWN, UP), 2, positive_months=12)
    assert alloc.target(series(f), d, cash_test) == {UP: 0.5}     # DOWN did not beat cash over 12 months


def test_the_market_filter_sends_everything_to_cash():
    f = levels()
    plan = Plan("p", (UP,), 1, rank_by="none", lookbacks=(6,), market_index=DOWN, market_average=200)
    assert alloc.target(series(f), day_at(f, 250), plan) == {UP: 1.0}
    assert alloc.target(series(f), day_at(f, 700), plan) == {}


def test_a_volatility_target_invests_less_in_a_wilder_index():
    f = levels()
    rng = np.random.default_rng(1)
    f[UP] = 100 * np.exp(np.cumsum(0.0008 + 0.03 * rng.standard_normal(len(f))))     # about 48% a year
    w = alloc.target(series(f), day_at(f, 700), Plan("p", (UP,), 1, rank_by="none", target_volatility=12.0))
    assert 0.15 < w[UP] < 0.4


def test_a_decision_reads_nothing_after_its_day():
    f = levels()
    d = day_at(f, 500)
    later = f.copy()
    later.loc[later.index > d, [UP, DOWN, BENCHMARK]] *= 3.0
    plan = Plan("p", (DOWN, UP), 1, lookbacks=(3, 6), own_average=100, market_index=BENCHMARK, market_average=200,
                target_volatility=15.0)
    assert alloc.target(series(f), d, plan) == alloc.target(series(later), d, plan)


def test_an_index_without_a_full_lookback_of_history_is_not_held():
    f = levels()
    f.loc[f.index < day_at(f, 300), UP] = np.nan
    assert alloc.target(series(f), day_at(f, 400), Plan("p", (UP,), 1)) == {}      # 12 months not yet there
    assert alloc.target(series(f), day_at(f, 700), Plan("p", (UP,), 1)) == {UP: 1.0}


def test_holding_one_index_is_the_index_less_the_fee_and_the_first_purchase():
    f = levels()
    start, end = day_at(f, 300), day_at(f, 899)
    r = alloc.run(f, Plan("p", (UP,), 1, rank_by="none"), start=start, end=end, capital=100_000.0)
    assert [s.filled for s in r.switches] == [start]              # in position from the first close, never traded again
    years = (end - start).days / 365.25
    expect = 100_000 * (1 - alloc.COST_PCT / 100) * f[UP][end] / f[UP][start] * (1 - alloc.FUND_FEE_PCT / 100) ** years
    assert r.equity.iloc[-1] == pytest.approx(expect, rel=1e-9)
    assert r.exposure.iloc[-1] == pytest.approx(1.0)


def test_cash_earns_the_cash_yield():
    f = levels()
    start, end = day_at(f, 600), day_at(f, 899)                   # DOWN is below its average throughout
    r = alloc.run(f, Plan("p", (UP,), 1, rank_by="none", market_index=DOWN, market_average=200),
                  start=start, end=end, capital=100_000.0)
    assert r.switches == []
    assert r.equity.iloc[-1] == pytest.approx(100_000 * 1.06 ** ((end - start).days / 365.25), rel=1e-9)


def test_a_switch_is_decided_at_a_month_end_and_filled_the_next_session():
    f = levels()
    plan = Plan("p", (DOWN,), 1, rank_by="none", own_average=100)
    r = alloc.run(f, plan, start=day_at(f, 200), end=day_at(f, 600))
    out = next(s for s in r.switches if not s.weights)            # the move to cash once DOWN breaks its average
    sessions = list(f.index)
    assert sessions[sessions.index(out.decided) + 1] == out.filled
    assert out.decided.month != out.filled.month                  # a month's last session
    assert out.cost == pytest.approx(alloc.COST_PCT / 100 * out.traded)


def test_weekly_decisions_do_not_trade_small_drift():
    f = levels()
    plan = Plan("p", (UP, BENCHMARK), 2, rank_by="none", decide="weekly")
    r = alloc.run(f, plan, start=day_at(f, 300), end=day_at(f, 899))
    assert len(r.switches) == 1                                   # bought once; the weights only drift


def test_holding_the_benchmark_has_no_edge_only_the_fee():
    f = levels()
    rng = np.random.default_rng(5)
    f[BENCHMARK] = 100 * np.exp(np.cumsum(0.0005 + 0.01 * rng.standard_normal(len(f))))
    r = alloc.run(f, Plan("p", (BENCHMARK,), 1, rank_by="none"), start=day_at(f, 300), end=day_at(f, 899))
    s = alloc.stats(r, f[BENCHMARK])
    assert s["beta"] == pytest.approx(1.0, abs=0.01)
    assert -1.2 < s["alpha_pct"] < -0.4                           # the 0.75% fee and the first purchase
    assert s["cagr_pct"] < s["bench_cagr_pct"] and s["years_beating_pct"] == 0


def test_alpha_does_not_punish_time_in_cash():
    rng = np.random.default_rng(7)
    b = pd.Series(0.01 + 0.05 * rng.standard_normal(240))
    rf = 0.005
    half_in = 0.5 * b + 0.5 * rf                                  # half the market, half cash: no skill
    alpha, t, beta = alloc._alpha(half_in, b, rf)
    assert beta == pytest.approx(0.5) and abs(alpha) < 1e-9
    skilled = half_in + 0.004
    alpha, t, beta = alloc._alpha(skilled, b, rf)
    assert alpha == pytest.approx(4.8) and beta == pytest.approx(0.5)


FORMAT_DOC = """\
version: alloc/1
universe: [NIFTY MIDCAP150 MOMENTUM 50, nifty200 momentum 30]
hold: 1
rank: {by: return, lookback_months: [6, 12], skip_months: 0}
weighting: equal
filters: {above_own_average: 200, beats_cash_months: 12}
market_filter: {index: NIFTY 50, average: 200}
volatility_target: {percent: 15, sessions: 60}
decide: weekly
"""


def test_a_plan_document_parses():
    p = alloc.parse(yaml.safe_load(FORMAT_DOC), name="n")
    assert p.universe == ("NIFTY MIDCAP150 MOMENTUM 50", "NIFTY200 MOMENTUM 30")      # names matched ignoring case
    assert (p.hold, p.rank_by, p.lookbacks, p.skip_months, p.weighting) == (1, "return", (6, 12), 0, "equal")
    assert (p.own_average, p.positive_months, p.market_index, p.market_average) == (200, 12, "NIFTY 50", 200)
    assert (p.target_volatility, p.volatility_sessions, p.decide) == (15.0, 60, "weekly")


def test_the_smallest_plan_is_one_index_held():
    p = alloc.parse({"version": "alloc/1", "universe": ["NIFTY 50"]}, name="n")
    assert (p.hold, p.lookbacks, p.market_index, p.target_volatility, p.decide) == (1, (12,), None, None, "monthly")


@pytest.mark.parametrize("old,new,match", [
    ("version: alloc/1", "version: alloc/2", "version must be"),
    ("nifty200 momentum 30", "NIFTY BITCOIN 10", "not on the menu"),
    ("nifty200 momentum 30", "NIFTY MIDCAP150 MOMENTUM 50", "listed twice"),
    ("hold: 1", "hold: 3", "hold must be"),
    ("by: return", "by: sharpe", "rank.by"),
    ("[6, 12]", "[6, 18]", "lookback_months"),
    ("skip_months: 0", "skip_months: 2, extra: 1", "unknown key"),
    ("[6, 12], skip_months: 0", "[1], skip_months: 1", "less than the shortest"),
    ("weighting: equal", "weighting: cap", "weighting must be"),
    ("above_own_average: 200", "above_own_average: 10", "above_own_average"),
    ("index: NIFTY 50", "index: SENSEX", "market_filter.index"),
    ("percent: 15", "percent: 50", "volatility_target.percent"),
    ("decide: weekly", "decide: daily", "decide must be"),
    ("decide: weekly", "decide: weekly\nstop_loss: 5", "unknown key"),
])
def test_refusals_say_what_to_change(old, new, match):
    text = FORMAT_DOC.replace(old, new, 1)
    assert text != FORMAT_DOC
    with pytest.raises(PlanError, match=match):
        alloc.parse(yaml.safe_load(text), name="n")


def test_no_menu_index_starts_late_enough_to_sit_out_2008():
    """The menu is every index with levels from April 2005 - checked against the list, not the network."""
    late = {"NIFTY50 VALUE 20", "NIFTY DIVIDEND OPPORTUNITIES 50", "NIFTY REALTY", "NIFTY INDIA CONSUMPTION",
            "NIFTY CPSE"}
    assert not late & set(alloc.MENU) and len(set(alloc.MENU)) == len(alloc.MENU)
