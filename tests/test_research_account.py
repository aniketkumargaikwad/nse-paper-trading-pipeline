"""The account: a fixed number of slots, signals taken as they arrive."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.account import SLOTS, account_weight, build_account, replay  # noqa: E402
from research.portfolio import build_basket  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import ist, trade  # noqa: E402


def t(day, hour_in=10, hour_out=14, pct=1.0, month=1, year=2024, price=100.0):
    return trade(entry=ist(year, month, day, hour_in), exit_=ist(year, month, day, hour_out),
                 entry_price=price, exit_price=price * (1 + pct / 100), quantity=int(100_000 / price))


def sleeve(symbol, trades, months=(("2024-01", 100.0, 100.0),), timeframe="day"):
    return ComboResult(symbol, timeframe, False, tuple(trades), month_closes=tuple(months))


# --- the replay ------------------------------------------------------------------


def test_a_free_slot_takes_the_trade_and_a_full_account_skips_it():
    trades = [t(1, price=100 + i) for i in range(3)]        # three entries at the same instant
    done = replay(trades, slots=2)
    assert len(done.taken) == 2 and done.skipped == 1


def test_an_exit_at_the_same_instant_frees_its_slot_first():
    first = t(1, hour_in=10, hour_out=11)
    second = t(1, hour_in=11, hour_out=12, price=101)
    done = replay([first, second], slots=1)
    assert len(done.taken) == 2 and done.skipped == 0


def test_signals_are_taken_in_time_order_not_symbol_order():
    early = t(1, hour_in=10, hour_out=15, price=150)      # the pricier stock came first
    late = t(1, hour_in=11, hour_out=15, price=100)
    done = replay([late, early], slots=1)
    assert done.taken == (early,) and done.skipped == 1


def test_slot_use_is_time_in_a_position_over_slot_time():
    one = t(1, hour_in=10, hour_out=14)                    # 4 of 4 hours, one of two slots
    assert replay([one], slots=2).slot_use_pct == pytest.approx(50.0)


def test_no_trades_is_an_empty_replay():
    done = replay([], slots=3)
    assert done.taken == () and done.skipped == 0 and done.slot_use_pct == 0.0


# --- the account reading ----------------------------------------------------------


def test_the_account_month_is_on_the_whole_capital_not_per_sleeve():
    """One +1% trade in a two-slot account is +0.5% of the account."""
    account = build_account([sleeve("A", [t(1)]), sleeve("B", [])], timeframe="day", slots=2)
    assert account.avg_month_pct == pytest.approx(0.5)
    assert account.slots == 2 and account.is_account


def test_a_selective_rule_reads_far_higher_on_an_account_than_on_the_basket():
    """Twenty stocks, one trade each at +2%, in different weeks: the basket says
    +2% a month; a two-slot account that took every one says +20%."""
    sleeves = [sleeve(f"S{i}", [t(1 + i, pct=2.0)]) for i in range(20)]
    basket = build_basket(sleeves, timeframe="day")
    account = build_account(sleeves, timeframe="day", slots=2)
    assert basket.avg_month_pct == pytest.approx(2.0)
    assert account.avg_month_pct == pytest.approx(20.0)
    assert account.signals_skipped_pct == 0.0


def test_skipped_signals_are_counted_and_the_account_keeps_what_it_took():
    same_moment = [sleeve(f"S{i}", [t(1, price=100 + i)]) for i in range(5)]
    account = build_account(same_moment, timeframe="day", slots=2)
    assert account.trades == 2
    assert account.signals_skipped_pct == pytest.approx(60.0)


def test_holding_is_the_equal_weight_basket_and_months_follow_the_data():
    a = sleeve("A", [], months=(("2024-01", 100, 110), ("2024-02", 110, 110)))
    b = sleeve("B", [], months=(("2024-01", 100, 90),))
    account = build_account([a, b], timeframe="day", slots=1)
    jan, feb = account.months
    assert jan["holding_pct"] == pytest.approx(0.0) and jan["stocks"] == 2
    assert feb["stocks"] == 1
    assert account.stocks == 2


def test_a_timeframe_with_nothing_tested_has_no_account():
    assert build_account([ComboResult("A", "day", False, (), "skipped")], timeframe="day") is None


def test_the_default_is_ten_slots():
    assert SLOTS == 10


def test_account_weight_is_the_trade_over_the_whole_capital():
    weight = account_weight(slots=10)
    assert weight(t(1, pct=1.0)) == pytest.approx(0.001)        # +1,000 on 10 lakh


def test_years_beating_holding_counts_years_that_beat_holding():
    trades = [t(1, pct=1.0, month=m, year=2023 + (m > 6)) for m in range(1, 13)]
    months = tuple((f"{2023 + (m > 6)}-{m:02d}", 100.0, 100.0) for m in range(1, 13))
    account = build_account([sleeve("A", trades, months)], timeframe="day", slots=1)
    assert account.years_beating_holding_pct == pytest.approx(100.0)
