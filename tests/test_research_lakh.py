"""What Rs 1 lakh becomes: compounding, fees on the real balance, holding."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel, HoldingCostModel  # noqa: E402
from research.lakh import LakhResult, compound, just_holding, passed  # noqa: E402
from research_helpers import FREE, ist, same_day, trade  # noqa: E402

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")


def test_no_trades_leaves_the_money_where_it_was():
    got = compound([], FREE, window_days=365)
    assert (got.end_value, got.trades, got.worst_dip_pct, got.cagr_pct) == (100_000.0, 0, 0.0, 0.0)


def test_gains_build_on_each_other():
    got = compound([same_day(2, 10), same_day(3, 10)], FREE, window_days=365)
    assert got.end_value == pytest.approx(121_000.0)


def test_worst_dip_is_measured_from_the_running_high():
    got = compound([same_day(2, 10), same_day(3, -20), same_day(6, 25)], FREE, window_days=365)
    assert got.worst_dip_pct == pytest.approx(20.0)
    assert got.end_value == pytest.approx(110_000.0)


def test_trades_are_applied_in_entry_order_whatever_order_they_arrive():
    a = compound([same_day(3, -20), same_day(2, 10)], FREE, window_days=365)
    b = compound([same_day(2, 10), same_day(3, -20)], FREE, window_days=365)
    assert a == b


def test_overnight_fees_are_recomputed_on_the_current_balance():
    stt_only = HoldingCostModel(
        intraday=FREE.intraday,
        delivery=DeliveryCostModel(stt=0.001, exchange_txn=0.0, sebi_fees=0.0,
                                   stamp_duty_buy=0.0, gst_rate=0.0, dp_charge_per_sell=0.0),
    )
    flat_overnight = [
        trade(entry=ist(2025, 1, 2, 15), exit_=ist(2025, 1, 3, 10), exit_price=100.0),
        trade(entry=ist(2025, 1, 6, 15), exit_=ist(2025, 1, 7, 10), exit_price=100.0),
    ]
    # fee 1 = 100,000 x 2 legs x 0.1% = 200 ; fee 2 = 99,800 x 2 x 0.1% = 199.6
    assert compound(flat_overnight, stt_only, window_days=365).end_value == pytest.approx(99_600.4)


def test_same_day_trades_use_intraday_fees():
    stt_sell_only = HoldingCostModel(
        intraday=CostModel(brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn=0.0,
                           sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0),
        delivery=FREE.delivery,
    )
    got = compound([same_day(2, 0)], stt_sell_only, window_days=365)
    assert got.end_value == pytest.approx(100_000.0 - 100_000.0 * 0.00025)


def test_a_short_trade_compounds_on_its_own_gross_return():
    short = trade(entry=ist(2025, 1, 2, 10), exit_=ist(2025, 1, 2, 14),
                  entry_price=100.0, exit_price=90.0, position_type="short")
    assert compound([short], FREE, window_days=365).end_value == pytest.approx(110_000.0)


def test_cagr_over_one_year_is_about_the_total_return():
    got = compound([same_day(2, 10)], FREE, window_days=365)
    assert got.cagr_pct == pytest.approx(10.0, abs=0.05)


def test_winning_trades_are_counted():
    got = compound([same_day(2, 10), same_day(3, -5), same_day(6, 1)], FREE, window_days=365)
    assert (got.trades, got.winning_trades) == (3, 2)


def day_candles(closes):
    start = datetime(2025, 8, 28, 0, 0, tzinfo=IST)
    idx = pd.DatetimeIndex([(start + timedelta(days=i)).astimezone(UTC) for i in range(len(closes))])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": [float(c) for c in closes], "volume": [0.0] * len(closes)}, index=idx)


def test_just_holding_buys_the_first_close_and_sells_the_last():
    assert just_holding(day_candles([100, 105, 120]), FREE) == pytest.approx(120_000.0)


def test_just_holding_pays_delivery_fees_once():
    stt_only = HoldingCostModel(intraday=FREE.intraday, delivery=DeliveryCostModel(
        stt=0.001, exchange_txn=0.0, sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0,
        dp_charge_per_sell=0.0))
    # 1,000 units: fees = 1,000 x (100 + 120) x 0.1% = 220
    assert just_holding(day_candles([100, 120]), stt_only) == pytest.approx(119_780.0)


def test_just_holding_needs_two_candles():
    assert just_holding(day_candles([100]), FREE) is None


def result(end, trades, dip):
    return LakhResult(start_value=100_000.0, end_value=end, trades=trades,
                      winning_trades=0, worst_dip_pct=dip, cagr_pct=None)


def test_the_verdict_needs_profit_ten_trades_and_a_dip_no_deeper_than_20():
    assert passed(result(100_001.0, 10, 20.0))
    assert not passed(result(100_000.0, 10, 5.0))
    assert not passed(result(120_000.0, 9, 5.0))
    assert not passed(result(120_000.0, 30, 20.01))
