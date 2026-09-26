"""The fixed rule that picks one TIMEFRAME's account from the training results.

The rule's whole job is to refuse to be flattered. Most of these tests are
about what it declines to pick.

Numbers to keep in mind: the account has ten Rs 1 lakh slots, so one stock
earning 1% of its sleeve in a month is 0.1% of the account.
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
    accounts_by_timeframe,
    best_score,
    disqualified,
    pick_timeframe,
)
from research.segment import TARGET_MONTHLY_MIN  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import ist, trade  # noqa: E402


def month_key(i: int) -> str:
    year, month = divmod(i, 12)
    return f"{2015 + year}-{month + 1:02d}"


def sleeve(symbol, timeframe, *, months=MIN_MONTHS, pct=1.0, per_month=10,
           hold=0.0, dips=(), price=100.0):
    """A stock whose sleeve makes `pct` a month over `per_month` same-day trades.

    `hold` is the stock's own monthly move; `dips` names months (0-based)
    that return -25% instead, to test the fall limit.
    """
    trades = []
    closes = []
    level = 100.0
    for i in range(months):
        year, month = divmod(i, 12)
        year += 2015
        month += 1
        month_pct = -25.0 if i in dips else pct
        each = month_pct / per_month
        for k in range(per_month):
            day = 1 + k % 20
            trades.append(trade(entry=ist(year, month, day, 10), exit_=ist(year, month, day, 14),
                                entry_price=price, exit_price=price * (1 + each / 100),
                                quantity=int(100_000 / price)))
        first = level
        level = level * (1 + hold / 100)
        closes.append((month_key(i), first, level))
    return ComboResult(symbol, timeframe, False, tuple(trades), month_closes=tuple(closes))


def ten(timeframe="day", **kwargs):
    """Ten stocks with the same shape: one full account's worth of sleeves."""
    return [sleeve(f"S{i}", timeframe, price=100.0 + i, **kwargs) for i in range(10)]


def test_an_account_with_too_little_history_is_not_picked():
    assert pick_timeframe(ten(months=MIN_MONTHS - 1)) is None
    assert pick_timeframe(ten(months=MIN_MONTHS)) is not None


def test_an_account_that_trades_too_rarely_and_earns_too_little_is_not_picked():
    slow = [sleeve("A", "day", per_month=int(MIN_TRADES_PER_MONTH) - 1)]
    assert "too slow" in disqualified(accounts_by_timeframe(slow)["day"])
    assert pick_timeframe(slow) is None


def test_the_trade_floor_is_soft_when_the_target_month_is_met():
    """The owner's rule: hit the monthly minimum and any number of trades is fine."""
    rare_but_rich = [sleeve("A", "day", per_month=2, pct=10 * TARGET_MONTHLY_MIN)]
    assert disqualified(accounts_by_timeframe(rare_but_rich)["day"]) is None
    assert pick_timeframe(rare_but_rich) is not None


def test_the_trade_floor_counts_the_whole_account_not_one_stock():
    """Two stocks at five a month is ten a month for the account."""
    two = [sleeve("A", "day", per_month=5), sleeve("B", "day", per_month=5, price=101.0)]
    assert pick_timeframe(two) is not None


def test_an_account_that_fell_too_far_is_not_picked():
    fell = ten(pct=1.0, dips=(6, 7))                  # every sleeve -25% twice in a row
    reason = disqualified(accounts_by_timeframe(fell)["day"])
    assert reason is not None and f"limit {MAX_DIP_PCT:.0f}%" in reason


def test_an_account_that_lost_to_holding_while_invested_is_not_picked():
    """Same-day trades keep the money at work about 5% of the time, so holding
    is scaled to that 5%: +40% a month of holding is a +2% bar, which +1% does
    not clear."""
    beta = ten(pct=1.0, hold=40.0)
    assert "did not beat holding while invested" in disqualified(accounts_by_timeframe(beta)["day"])
    assert pick_timeframe(beta) is None


def test_clearing_the_scaled_bar_is_not_enough_if_holding_won_every_year():
    """+1% a month clears the scaled +0.1% bar, and until 26 Sep 2026 that was a
    pass. But holding made +2% every month: the owner's account, whose idle
    cash earns nothing, would have done better holding in every year."""
    edge = ten(pct=1.0, hold=2.0)
    assert "beat holding in only 0 of 2 years" in disqualified(accounts_by_timeframe(edge)["day"])
    assert pick_timeframe(edge) is None


def test_an_account_that_beat_holding_but_lost_money_is_not_picked():
    losing = ten(pct=-0.5, hold=-30.0)
    assert "lost money" in disqualified(accounts_by_timeframe(losing)["day"])


def test_the_best_average_month_wins_among_the_qualified():
    results = ten("day", pct=1.0) + ten("60m", pct=2.0) + ten("5m", pct=1.5)
    assert pick_timeframe(results).timeframe == "60m"


def test_a_bigger_month_that_failed_a_gate_loses_to_a_smaller_one_that_passed():
    results = ten("day", pct=1.0) + ten("60m", pct=5.0, hold=200.0)
    assert pick_timeframe(results).timeframe == "day"


def test_a_tie_on_the_month_goes_to_the_bigger_lead_over_holding():
    results = ten("day", pct=1.0, hold=0.5) + ten("60m", pct=1.0, hold=-0.5)
    assert pick_timeframe(results).timeframe == "60m"


def test_the_pick_carries_both_readings():
    got = pick_timeframe(ten(pct=1.0))
    assert got.account.is_account and got.account.avg_month_pct == pytest.approx(1.0, abs=0.01)
    assert not got.basket.is_account and got.basket.avg_month_pct == pytest.approx(1.0, abs=0.01)
    assert got.account.stocks == 10 and got.account.month_count == MIN_MONTHS


def test_a_versions_score_is_its_picks_average_month_or_nothing():
    assert best_score(ten(pct=1.5)) == pytest.approx(1.5, abs=0.02)
    assert best_score(ten(pct=1.0, hold=40.0)) == float("-inf")


def test_nothing_tested_picks_nothing():
    assert pick_timeframe([]) is None
    assert pick_timeframe([ComboResult("A", "day", False, (), "no candles in window")]) is None


# ---- Consistency and luck (2026-09-26) ----
#
# Of 322 accounts tested 16-25 Sep 2026, three passed the old gate. All three
# earned their lead in the 2020-24 boom and lost it in every flat stretch -
# 2017-19, early 2025 - and all three then failed the flat locked year. The
# figures that said so (years beating holding, the luck check) were computed
# and shown, but the gate never read them.

from research.portfolio import summarise  # noqa: E402


def account_of(rows, *, slot_use_pct=30.0):
    """An account reading built straight from (year, strategy_pct, holding_pct) months."""
    months = [{"month": f"{y}-{m:02d}", "strategy_pct": s, "holding_pct": h,
               "trades": 12, "wins": 6, "stocks": 200}
              for y, s, h in rows for m in range(1, 13)]
    return summarise("day", 200, months, stock_months=len(months) * 200,
                     trading_months=len(months) * 60, slot_use_pct=slot_use_pct)


def test_a_lead_earned_in_one_boom_is_not_picked():
    """The shape of the 19 Sep pick: well ahead in 2020-21, behind in every other year."""
    boom = [(y, 8.0, 2.0) for y in (2020, 2021)]
    flat = [(y, -0.5, 1.0) for y in (2017, 2018, 2019, 2022, 2023, 2024, 2025)]
    reading = account_of(sorted(boom + flat))
    assert reading.avg_excess_active_pct > 0          # the old gate let this through
    assert reading.years_beating_holding_pct == pytest.approx(100 * 2 / 9, abs=0.01)
    why = disqualified(reading)
    assert why is not None and "2 of 9 years" in why


def test_a_lead_that_could_be_luck_is_not_picked():
    """Ahead of holding in every year, but by a whisker against wild months."""
    months = []
    for y in range(2017, 2026):
        for m in range(1, 13):
            months.append({"month": f"{y}-{m:02d}", "strategy_pct": 6.0 if m % 2 else -3.8,
                           "holding_pct": 1.0, "trades": 12, "wins": 6, "stocks": 200})
    reading = summarise("day", 200, months, stock_months=len(months) * 200,
                        trading_months=len(months) * 60, slot_use_pct=30.0)
    assert reading.years_beating_holding_pct == 100.0
    assert reading.edge_t < 2
    why = disqualified(reading)
    assert why is not None and "could be luck" in why


def test_a_steady_significant_lead_is_still_picked():
    months = []
    for y in range(2017, 2026):
        for m in range(1, 13):
            months.append({"month": f"{y}-{m:02d}", "strategy_pct": 2.5 if m % 2 else 1.5,
                           "holding_pct": 1.0, "trades": 12, "wins": 6, "stocks": 200})
    reading = summarise("day", 200, months, stock_months=len(months) * 200,
                        trading_months=len(months) * 60, slot_use_pct=30.0)
    assert reading.edge_t >= 2 and reading.years_beating_holding_pct == 100.0
    assert disqualified(reading) is None


def test_the_same_lead_every_month_is_certain_not_unknown():
    """Zero spread leaves edge_t empty (a divide by zero), not doubtful."""
    reading = account_of([(y, 2.0, 1.0) for y in range(2017, 2026)])
    assert reading.edge_t is None
    assert disqualified(reading) is None
