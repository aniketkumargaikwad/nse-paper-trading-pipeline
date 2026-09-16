"""The basket: every stock at once, judged month by month."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.month_closes import month_closes  # noqa: E402
from research.portfolio import (  # noqa: E402
    Basket,
    build_basket,
    daily_equity,
    dip_of_equity,
    luck_label,
    restrict_to,
    sleeve_months,
)
from research.sweep import ComboResult  # noqa: E402
from research_helpers import ist, trade  # noqa: E402

UTC = ZoneInfo("UTC")


def a_trade(month: int, pct: float, day: int = 10, year: int = 2024):
    """One same-day trade on 100,000 notional returning `pct` percent."""
    return trade(entry=ist(year, month, day, 10), exit_=ist(year, month, day, 14),
                 entry_price=100.0, exit_price=100.0 * (1 + pct / 100), quantity=1000)


def closes(*months: tuple[str, float, float]):
    return tuple(months)


def sleeve(symbol, trades=(), months=(), timeframe="day", is_index=False, skipped=None):
    return ComboResult(symbol, timeframe, is_index, tuple(trades), skipped, month_closes=closes(*months))


# --- month closes from a frame -------------------------------------------------


def test_month_closes_are_first_and_last_close_per_ist_month():
    idx = pd.DatetimeIndex([
        datetime(2024, 1, 30, 10, tzinfo=ist(2024, 1, 1).tzinfo),
        datetime(2024, 1, 31, 10, tzinfo=ist(2024, 1, 1).tzinfo),
        datetime(2024, 2, 1, 10, tzinfo=ist(2024, 1, 1).tzinfo),
    ]).tz_convert(UTC)
    frame = pd.DataFrame({"close": [100.0, 101.0, 105.0]}, index=idx)
    assert month_closes(frame) == (("2024-01", 100.0, 101.0), ("2024-02", 105.0, 105.0))


def test_an_empty_frame_has_no_months():
    assert month_closes(pd.DataFrame({"close": []}, index=pd.DatetimeIndex([], tz="UTC"))) == ()


# --- one sleeve ----------------------------------------------------------------


def test_a_sleeve_month_is_its_net_pnl_over_the_notional():
    s = sleeve("A", [a_trade(1, 2.0), a_trade(1, -0.5)], [("2024-01", 100.0, 100.0)])
    got = sleeve_months(s)
    assert got["2024-01"].strategy_pct == pytest.approx(1.5)
    assert got["2024-01"].trades == 2 and got["2024-01"].wins == 1


def test_holding_uses_the_first_close_in_the_first_month_then_month_to_month():
    s = sleeve("A", [], [("2024-01", 100.0, 110.0), ("2024-02", 111.0, 99.0)])
    got = sleeve_months(s)
    assert got["2024-01"].holding_pct == pytest.approx(10.0)
    assert got["2024-02"].holding_pct == pytest.approx(-10.0)     # 99 / 110 - 1


def test_a_month_with_data_but_no_trade_is_a_flat_month_not_a_missing_one():
    s = sleeve("A", [a_trade(2, 1.0)], [("2024-01", 100.0, 100.0), ("2024-02", 100.0, 100.0)])
    got = sleeve_months(s)
    assert got["2024-01"].strategy_pct == 0.0 and got["2024-01"].trades == 0


# --- the basket ----------------------------------------------------------------


def test_the_basket_is_the_mean_over_stocks_with_data_that_month():
    a = sleeve("A", [a_trade(1, 2.0)], [("2024-01", 100, 100), ("2024-02", 100, 100)])
    b = sleeve("B", [a_trade(1, 4.0), a_trade(2, 1.0)], [("2024-01", 100, 100), ("2024-02", 100, 100)])
    c = sleeve("C", [a_trade(2, 3.0)], [("2024-02", 100, 100)])        # listed in February
    basket = build_basket([a, b, c], timeframe="day")
    jan, feb = basket.months
    assert jan["stocks"] == 2 and jan["strategy_pct"] == pytest.approx(3.0)
    assert feb["stocks"] == 3 and feb["strategy_pct"] == pytest.approx((0 + 1 + 3) / 3, abs=1e-3)
    assert basket.stocks == 3 and basket.trades == 4


def test_indexes_skips_and_other_timeframes_stay_out_of_the_basket():
    day = sleeve("A", [a_trade(1, 1.0)], [("2024-01", 100, 100)])
    hourly = sleeve("A", [a_trade(1, 9.0)], [("2024-01", 100, 100)], timeframe="60m")
    index = sleeve("NSE:NIFTY", [a_trade(1, 9.0)], [("2024-01", 100, 100)], is_index=True)
    skipped = sleeve("B", [], [], skipped="no candles in window")
    basket = build_basket([day, hourly, index, skipped], timeframe="day")
    assert basket.stocks == 1 and basket.avg_month_pct == pytest.approx(1.0)


def test_a_timeframe_with_nothing_tested_has_no_basket():
    assert build_basket([sleeve("A", [], [], skipped="x")], timeframe="day") is None


def test_the_summary_figures_read_off_the_months():
    trades = [a_trade(1, 2.0), a_trade(2, -1.0), a_trade(3, 3.0)]
    months = [("2024-01", 100, 101), ("2024-02", 101, 101), ("2024-03", 101, 100)]
    basket = build_basket([sleeve("A", trades, months)], timeframe="day")
    assert basket.month_count == 3
    assert basket.avg_month_pct == pytest.approx(4 / 3, abs=1e-3)
    assert basket.median_month_pct == pytest.approx(2.0)
    assert basket.months_positive_pct == pytest.approx(66.67, abs=0.01)
    assert basket.best_month_pct == 3.0 and basket.worst_month_pct == -1.0
    assert basket.trades_per_month == pytest.approx(1.0)
    assert basket.winning_trades == 2
    assert basket.years[0]["strategy_pct"] == pytest.approx(4.0)
    assert basket.years[0]["trades"] == 3


def test_totals_are_linear_sums_of_the_months():
    trades = [a_trade(1, 10.0), a_trade(2, 10.0)]
    basket = build_basket([sleeve("A", trades, [("2024-01", 100, 100), ("2024-02", 100, 100)])],
                          timeframe="day")
    assert basket.total_pct() == pytest.approx(20.0)
    assert basket.end_value(100_000) == pytest.approx(120_000)


def test_excess_is_strategy_minus_holding_month_by_month():
    trades = [a_trade(1, 5.0), a_trade(2, 5.0)]
    months = [("2024-01", 100, 102), ("2024-02", 102, 102)]           # holding +2%, 0%
    basket = build_basket([sleeve("A", trades, months)], timeframe="day")
    assert basket.avg_holding_month_pct == pytest.approx(1.0)
    assert basket.avg_excess_pct == pytest.approx(4.0)
    assert basket.holding_end_value(100_000) == pytest.approx(102_000)


def test_active_excess_scales_holding_by_the_time_at_work():
    """One trade in one of two months: the sleeve was at work half the
    stock-months, so holding counts at half weight."""
    trades = [a_trade(1, 2.0)]
    months = [("2024-01", 100, 104), ("2024-02", 104, 108.16)]            # holding +4%, +4%
    basket = build_basket([sleeve("A", trades, months)], timeframe="day")
    assert basket.avg_excess_pct == pytest.approx(1.0 - 4.0)
    assert basket.avg_excess_active_pct == pytest.approx(1.0 - 4.0 * 0.5)


def test_the_worst_dip_walks_the_monthly_path():
    trades = [a_trade(1, 10.0), a_trade(2, -22.0), a_trade(3, 5.0)]
    months = [(f"2024-0{m}", 100, 100) for m in (1, 2, 3)]
    basket = build_basket([sleeve("A", trades, months)], timeframe="day")
    assert basket.worst_dip_pct == pytest.approx(20.0)          # 110 -> 88


def test_sleeve_use_says_how_much_of_the_basket_actually_traded():
    a = sleeve("A", [a_trade(1, 1.0)], [("2024-01", 100, 100), ("2024-02", 100, 100)])
    b = sleeve("B", [], [("2024-01", 100, 100), ("2024-02", 100, 100)])
    assert build_basket([a, b], timeframe="day").sleeve_use_pct == pytest.approx(25.0)


def test_the_luck_check_needs_three_months_and_some_spread():
    two = build_basket([sleeve("A", [a_trade(1, 1.0), a_trade(2, 1.0)],
                               [("2024-01", 100, 100), ("2024-02", 100, 100)])], timeframe="day")
    assert two.edge_t is None and luck_label(two.edge_t) == "too few months to tell"
    steady = build_basket([sleeve("A", [a_trade(m, 1.0 + m * 0.01) for m in range(1, 13)],
                                  [(f"2024-{m:02d}", 100, 100) for m in range(1, 13)])],
                          timeframe="day")
    assert steady.edge_t > 2 and luck_label(steady.edge_t) == "unlikely to be luck"
    assert luck_label(1.5) == "could be luck" and luck_label(0.2) == "cannot be told from luck"


def test_as_dict_is_compact_unless_the_months_are_asked_for():
    basket = build_basket([sleeve("A", [a_trade(1, 1.0)], [("2024-01", 100, 100)])], timeframe="day")
    assert "month_rows" not in basket.as_dict()
    assert basket.as_dict(with_months=True)["month_rows"][0]["month"] == "2024-01"
    assert basket.as_dict()["luck_check"] == "too few months to tell"


# --- the locked part of a full-window result ----------------------------------


def test_restrict_keeps_trades_by_entry_and_months_from_the_boundary_month():
    full = sleeve("A", [a_trade(7, 1.0, year=2025), a_trade(8, 1.0, day=1, year=2025),
                        a_trade(9, 1.0, year=2025)],
                  [("2025-07", 100, 101), ("2025-08", 102, 103), ("2025-09", 104, 105)])
    part = restrict_to(full, ist(2025, 8, 1, 0).astimezone(UTC))
    assert [t.exit_fill_ts.month for t in part.trades] == [8, 9]
    assert [m[0] for m in part.month_closes] == ["2025-08", "2025-09"]
    assert sleeve_months(part)["2025-08"].holding_pct == pytest.approx(100 * (103 / 102 - 1))


# --- the daily path -------------------------------------------------------------


def _daily_closes(symbol_values: dict[str, list[float]], start=(2025, 8, 1)):
    out = {}
    for symbol, values in symbol_values.items():
        idx = pd.DatetimeIndex([
            (ist(*start, 0) + timedelta(days=i)).astimezone(UTC) for i in range(len(values))
        ])
        out[symbol] = pd.Series(values, index=idx)
    return out


def test_the_daily_path_ends_where_the_months_add_up():
    a = sleeve("A", [a_trade(8, 2.0, day=1, year=2025), a_trade(8, 2.0, day=2, year=2025)],
               [("2025-08", 100, 100)])
    b = sleeve("B", [], [("2025-08", 100, 100)])
    rows = daily_equity([a, b], closes_by_symbol=_daily_closes({"A": [100, 100, 100], "B": [100, 100, 100]}))
    assert rows[-1]["lakh_balance"] == pytest.approx(102_000)         # (2 + 2) / 2 stocks
    basket = build_basket([a, b], timeframe="day")
    assert basket.end_value() == pytest.approx(rows[-1]["lakh_balance"])


def test_holding_on_the_daily_path_is_the_equal_weight_mean():
    a = sleeve("A", [], [("2025-08", 100, 110)])
    rows = daily_equity([a], closes_by_symbol=_daily_closes({"A": [100, 105, 110], "B": [50, 50, 60]}))
    assert rows[-1]["hold_balance"] == pytest.approx(100_000 * (1 + (0.10 + 0.20) / 2))


def test_the_dip_of_a_path_is_measured_from_its_running_high():
    rows = [{"lakh_balance": v} for v in (100_000, 110_000, 99_000, 120_000)]
    assert dip_of_equity(rows) == pytest.approx(10.0)


def test_basket_is_a_plain_value():
    basket = build_basket([sleeve("A", [a_trade(1, 1.0)], [("2024-01", 100, 100)])], timeframe="day")
    assert isinstance(basket, Basket) and basket.timeframe == "day"
