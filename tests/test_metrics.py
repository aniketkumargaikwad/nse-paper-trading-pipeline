"""Metrics against hand-computed values. Pure — no network, no database.

Every expected number here is worked out independently in the test, not copied
from the implementation's output. A metric test that asserts what the code
happens to produce proves only that the code is deterministic.
"""

from __future__ import annotations

import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_types import SimTrade  # noqa: E402
from metrics import (  # noqa: E402
    MIN_PROFITABLE_SYMBOL_PCT,
    ComboMetrics,
    dispersion_metrics,
    evaluate_kill_rules,
    pooled_metrics,
    risk_metrics,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def trade(net: float, *, exit_day: int = 1, entry_price: float = 100.0,
          quantity: int = 10) -> SimTrade:
    """One completed trade with a chosen net P&L and exit date."""
    ts = datetime(2026, 8, exit_day, 10, 0, tzinfo=IST).astimezone(UTC)
    return SimTrade(
        entry_signal_ts=ts - timedelta(minutes=30),
        entry_fill_ts=ts - timedelta(minutes=15),
        exit_signal_ts=ts,
        exit_fill_ts=ts,
        position_type="long",
        quantity=quantity,
        intended_entry_price=entry_price,
        entry_price=entry_price,
        intended_exit_price=entry_price + net / quantity,
        exit_price=entry_price + net / quantity,
        exit_reason="target",
        gross_pnl=net,
        costs=0.0,
        net_pnl=net,
    )


# ---------------------------------------------------------------------------
# Pooled
# ---------------------------------------------------------------------------


def test_pooled_sums_across_symbols():
    per_symbol = {"A": [trade(100), trade(-40)], "B": [trade(60)]}
    m = pooled_metrics(per_symbol)
    assert m.total_trades == 3
    assert m.winning_trades == 2
    assert m.net_pnl == pytest.approx(120.0)          # 100 - 40 + 60
    assert m.win_rate_pct == pytest.approx(66.67, abs=0.01)
    assert m.profit_factor == pytest.approx(4.0)      # 160 / 40


def test_pooled_orders_trades_chronologically_not_by_symbol():
    """Ordering is load-bearing: drawdown walks the equity curve trade by trade.

    A: day 1 +100, day 4 -300.   B: day 2 +50, day 3 +40.

    Chronological (+100, +50, +40, -300): equity 100, 150, 190, -110.
                  Peak 190, so max drawdown 300.
    Symbol-by-symbol (+100, -300, +50, +40): equity 100, -200, -150, -110.
                  Peak 100 — a different curve, and a losing streak of 1 in a
                  different place.
    """
    per_symbol = {
        "A": [trade(100, exit_day=1), trade(-300, exit_day=4)],
        "B": [trade(50, exit_day=2), trade(40, exit_day=3)],
    }
    m = pooled_metrics(per_symbol)
    assert m.ordered_net_pnls == [100.0, 50.0, 40.0, -300.0]
    assert m.longest_losing_streak == 1
    assert m.max_drawdown_pct == pytest.approx(
        100.0 * 300.0 / m.capital_base, abs=0.01
    )


def test_pooled_with_no_trades_is_zeroed_not_absent():
    m = pooled_metrics({"A": [], "B": []})
    assert m.total_trades == 0
    assert m.net_pnl == 0.0
    assert m.profit_factor is None


# ---------------------------------------------------------------------------
# Dispersion
# ---------------------------------------------------------------------------


def test_dispersion_exposes_a_one_lucky_symbol_strategy():
    """The case the whole dispersion idea exists for.

    Pooled P&L is strongly positive, yet the strategy lost on four of five
    symbols. A headline number alone would call this a success.
    """
    per_symbol = {
        "LUCKY": [trade(1000)],
        "A": [trade(-50)], "B": [trade(-60)],
        "C": [trade(-40)], "D": [trade(-30)],
    }
    assert pooled_metrics(per_symbol).net_pnl > 0      # the misleading headline

    d = dispersion_metrics(per_symbol)
    assert d.symbols_traded == 5
    assert d.symbols_profitable == 1
    assert d.median_symbol_pnl == pytest.approx(-40.0)
    assert d.best_symbol == "LUCKY"
    assert d.best_symbol_pnl == pytest.approx(1000.0)
    assert d.worst_symbol == "B"
    assert d.worst_symbol_pnl == pytest.approx(-60.0)


def test_dispersion_on_a_broadly_profitable_strategy():
    per_symbol = {"A": [trade(30)], "B": [trade(40)], "C": [trade(-10)]}
    d = dispersion_metrics(per_symbol)
    assert d.symbols_profitable == 2
    assert d.median_symbol_pnl == pytest.approx(30.0)


def test_a_symbol_that_traded_nothing_still_counts_as_traded():
    """It was tested and produced no signal. Dropping it would overstate the
    proportion of symbols the edge worked on."""
    per_symbol = {"A": [trade(50)], "SILENT": []}
    d = dispersion_metrics(per_symbol)
    assert d.symbols_traded == 2
    assert d.symbols_profitable == 1
    assert d.median_symbol_pnl == pytest.approx(25.0)   # median of [0, 50]


# ---------------------------------------------------------------------------
# Risk-adjusted, two bases
# ---------------------------------------------------------------------------


def test_zero_variance_does_not_fabricate_a_sharpe():
    per_symbol = {"A": [trade(100, exit_day=1), trade(-40, exit_day=1)],
                  "B": [trade(60, exit_day=2)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=2)
    # Day 1: +60. Day 2: +60. Identical returns, so standard deviation is 0.
    assert r.sharpe_daily is None


def test_sharpe_daily_matches_a_hand_computed_value():
    per_symbol = {"A": [trade(100, exit_day=1), trade(-50, exit_day=2),
                        trade(150, exit_day=3)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    returns = [0.01, -0.005, 0.015]
    mean = sum(returns) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in returns) / (3 - 1))  # sample sd
    assert r.sharpe_daily == pytest.approx(mean / sd * math.sqrt(252), rel=1e-4)


def test_sortino_uses_only_downside_deviation():
    per_symbol = {"A": [trade(100, exit_day=1), trade(-50, exit_day=2),
                        trade(150, exit_day=3)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    returns = [0.01, -0.005, 0.015]
    mean = sum(returns) / 3
    downside = [x for x in returns if x < 0]
    dd = math.sqrt(sum(x ** 2 for x in downside) / len(downside))
    assert r.sortino_daily == pytest.approx(mean / dd * math.sqrt(252), rel=1e-4)
    assert r.sortino_daily > r.sharpe_daily, (
        "with one loser among three, downside deviation is smaller than total "
        "deviation, so Sortino must exceed Sharpe"
    )


def test_expectancy_and_sqn_are_trade_basis_and_need_no_capital():
    per_symbol = {"A": [trade(100), trade(-50), trade(150)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    pnls = [100.0, -50.0, 150.0]
    mean = sum(pnls) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / 2)
    assert r.expectancy_per_trade == pytest.approx(mean)
    assert r.system_quality_number == pytest.approx(
        mean / sd * math.sqrt(3), rel=1e-4
    )


def test_a_single_trading_day_yields_null_not_zero():
    """An absent measurement must never read as a measured zero."""
    per_symbol = {"A": [trade(100, exit_day=1)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=1)
    assert r.sharpe_daily is None
    assert r.sortino_daily is None
    assert r.system_quality_number is None
    assert r.expectancy_per_trade == pytest.approx(100.0)  # still meaningful


def test_no_trades_gives_null_risk_metrics_and_zero_expectancy():
    r = risk_metrics({"A": []}, capital_base=10000.0, trading_days=250)
    assert r.sharpe_daily is None
    assert r.sortino_daily is None
    assert r.expectancy_per_trade == 0.0


def test_sortino_is_null_when_nothing_lost():
    """No downside means no downside deviation — a division, not an infinity."""
    per_symbol = {"A": [trade(100, exit_day=1), trade(50, exit_day=2)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=2)
    assert r.sortino_daily is None


def test_flat_days_in_the_window_count_as_observations():
    """A strategy that traded on 2 of 250 days is not a 2-day strategy.

    Dividing by only the days that traded would flatter it enormously, so the
    window's flat days are part of the return series.
    """
    per_symbol = {"A": [trade(100, exit_day=1), trade(-50, exit_day=2)]}
    narrow = risk_metrics(per_symbol, capital_base=10000.0, trading_days=2)
    wide = risk_metrics(per_symbol, capital_base=10000.0, trading_days=250)
    assert wide.trading_days == 250
    assert abs(wide.sharpe_daily) < abs(narrow.sharpe_daily), (
        "spreading the same P&L over more flat days must lower the Sharpe"
    )


def test_cagr_matches_a_hand_computed_value():
    per_symbol = {"A": [trade(1000, exit_day=1)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=252)
    # 10% total return over exactly one trading year -> 10% annualised.
    assert r.cagr_pct == pytest.approx(10.0, abs=0.01)


# ---------------------------------------------------------------------------
# Kill rules
# ---------------------------------------------------------------------------


def combo(net_pnl=100.0, trades=50, dd=5.0) -> ComboMetrics:
    return ComboMetrics(
        total_trades=trades, winning_trades=trades // 2, net_pnl=net_pnl,
        win_rate_pct=50.0, profit_factor=1.5, max_drawdown_pct=dd,
        longest_losing_streak=3,
    )


def test_symbol_robustness_is_a_proportion_not_a_fixed_count():
    """3 of 50 used to pass. It is a 6% bar and would clear a strategy losing
    money on 47 of 50 stocks — exactly the curve-fitting the rule exists to
    catch."""
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=3, total_symbols=50)
    assert flags["symbol_robustness"]["passed"] is False


@pytest.mark.parametrize(
    "profitable,total,expected",
    [
        (20, 50, True),    # 40% exactly — the boundary passes
        (19, 50, False),   # 38%
        (21, 50, True),    # 42%
        (2, 5, True),      # 40% of a small universe
        (1, 5, False),     # 20%
    ],
)
def test_the_threshold_holds_at_its_boundaries(profitable, total, expected):
    _, flags = evaluate_kill_rules(combo(), profitable, total)
    assert flags["symbol_robustness"]["passed"] is expected


def test_the_flag_reports_both_the_percentage_and_the_counts():
    """The dashboard shows WHY something failed, so the numbers must be there."""
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=10, total_symbols=50)
    rule = flags["symbol_robustness"]
    assert rule["required_pct"] == MIN_PROFITABLE_SYMBOL_PCT
    assert rule["actual_pct"] == pytest.approx(20.0)
    assert rule["actual_profitable_symbols"] == 10
    assert rule["total_symbols"] == 50


def test_a_single_symbol_strategy_must_be_profitable_on_it():
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=1, total_symbols=1)
    assert flags["symbol_robustness"]["passed"] is True
    _, flags = evaluate_kill_rules(combo(net_pnl=-10.0), 0, 1)
    assert flags["symbol_robustness"]["passed"] is False
