"""The fixed rule that picks one TIMEFRAME's basket from the training results.

The rule's whole job is to refuse to be flattered. Most of these tests are
about what it declines to pick.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.picker import (  # noqa: E402
    MAX_DIP_PCT,
    MIN_MONTHS,
    MIN_TRADES_PER_MONTH,
    baskets_by_timeframe,
    best_score,
    disqualified,
    pick_timeframe,
)
from research.sweep import ComboResult  # noqa: E402
from research_helpers import ist, trade  # noqa: E402


def month_key(i: int) -> str:
    year, month = divmod(i, 12)
    return f"{2015 + year}-{month + 1:02d}"


def sleeve(symbol, timeframe, *, months=MIN_MONTHS, pct=1.0, per_month=10,
           hold=0.0, dips=()):
    """A stock whose sleeve makes `pct` a month over `per_month` trades, holding `hold`.

    `dips` names months (0-based) that return -25% instead, to test the fall limit.
    """
    trades = []
    closes = []
    price = 100.0
    for i in range(months):
        year, month = divmod(i, 12)
        year += 2015
        month += 1
        month_pct = -25.0 if i in dips else pct
        each = month_pct / per_month
        for k in range(per_month):
            day = 1 + k % 20
            trades.append(trade(entry=ist(year, month, day, 10), exit_=ist(year, month, day, 14),
                                entry_price=100.0, exit_price=100.0 * (1 + each / 100),
                                quantity=1000))
        first = price
        price = price * (1 + hold / 100)
        closes.append((month_key(i), first, price))
    return ComboResult(symbol, timeframe, False, tuple(trades), month_closes=tuple(closes))


def test_a_basket_with_too_little_history_is_not_picked():
    assert pick_timeframe([sleeve("A", "day", months=MIN_MONTHS - 1)]) is None
    assert pick_timeframe([sleeve("A", "day", months=MIN_MONTHS)]) is not None


def test_a_basket_that_trades_too_rarely_is_not_picked():
    slow = sleeve("A", "day", per_month=int(MIN_TRADES_PER_MONTH) - 1)
    assert "too slow" in disqualified(baskets_by_timeframe([slow])["day"])
    assert pick_timeframe([slow]) is None


def test_the_trade_floor_counts_the_whole_basket_not_one_stock():
    """Two stocks at five a month is ten a month across the basket."""
    two = [sleeve("A", "day", per_month=5), sleeve("B", "day", per_month=5)]
    assert pick_timeframe(two) is not None


def test_a_basket_that_fell_too_far_is_not_picked():
    fell = sleeve("A", "day", pct=1.0, dips=(6, 7))              # -25% twice in a row
    reason = disqualified(baskets_by_timeframe([fell])["day"])
    assert reason is not None and f"limit {MAX_DIP_PCT:.0f}%" in reason


def test_a_basket_that_lost_to_holding_is_not_picked():
    beta = sleeve("A", "day", pct=1.0, hold=2.0)
    assert "did not beat holding" in disqualified(baskets_by_timeframe([beta])["day"])
    assert pick_timeframe([beta]) is None


def test_a_basket_that_beat_holding_but_lost_money_is_not_picked():
    losing = sleeve("A", "day", pct=-0.5, hold=-3.0)
    assert "lost money" in disqualified(baskets_by_timeframe([losing])["day"])


def test_the_best_average_month_wins_among_the_qualified():
    results = [sleeve("A", "day", pct=1.0), sleeve("A", "60m", pct=2.0), sleeve("A", "5m", pct=1.5)]
    assert pick_timeframe(results).timeframe == "60m"


def test_a_bigger_month_that_failed_a_gate_loses_to_a_smaller_one_that_passed():
    results = [sleeve("A", "day", pct=1.0), sleeve("A", "60m", pct=5.0, hold=6.0)]
    assert pick_timeframe(results).timeframe == "day"


def test_a_tie_on_the_month_goes_to_the_bigger_lead_over_holding():
    results = [sleeve("A", "day", pct=1.0, hold=0.5), sleeve("A", "60m", pct=1.0, hold=-0.5)]
    assert pick_timeframe(results).timeframe == "60m"


def test_the_pick_carries_its_basket():
    got = pick_timeframe([sleeve("A", "day", pct=1.0)])
    assert got.basket.avg_month_pct == pytest.approx(1.0)
    assert got.basket.stocks == 1 and got.basket.month_count == MIN_MONTHS


def test_a_versions_score_is_its_picks_average_month_or_nothing():
    assert best_score([sleeve("A", "day", pct=1.5)]) == pytest.approx(1.5)
    assert best_score([sleeve("A", "day", pct=1.0, hold=2.0)]) == float("-inf")


def test_nothing_tested_picks_nothing():
    assert pick_timeframe([]) is None
    assert pick_timeframe([ComboResult("A", "day", False, (), "no candles in window")]) is None
