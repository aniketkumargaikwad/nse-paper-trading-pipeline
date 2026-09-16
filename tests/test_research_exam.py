"""The exam: the locked year opened once, for one timeframe's account."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import HoldingCostModel  # noqa: E402
from parquet_candle_backend import ParquetCandleBackend  # noqa: E402
from research.evaluate import basket_label, locked_year  # noqa: E402
from research.prices import FrozenPriceReader  # noqa: E402
from research.windows import ResearchWindows  # noqa: E402
from strategy_schema import parse_strategy_dict  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
DATA_END = date(2026, 7, 31)


def daily_frame(start: date, days: int, *, step: float) -> pd.DataFrame:
    """A stock that drifts `step` a day, weekdays only, with a dip in the middle."""
    rows = []
    price = 100.0
    day = start
    while len(rows) < days:
        if day.weekday() < 5:
            price = price * (1 + step)
            rows.append((datetime(day.year, day.month, day.day, 0, 0, tzinfo=IST).astimezone(UTC),
                         price))
        day += timedelta(days=1)
    idx = pd.DatetimeIndex([r[0] for r in rows])
    closes = [r[1] for r in rows]
    return pd.DataFrame(
        {"open": [c * 0.995 for c in closes], "high": [c * 1.01 for c in closes],
         "low": [c * 0.99 for c in closes], "close": closes, "volume": [1000.0] * len(closes)},
        index=idx,
    )


def strategy():
    """Enter on any close above the previous close, exit the next bar: it trades constantly."""
    return parse_strategy_dict({
        "name": "exam-test", "enabled": False, "position_type": "long", "timeframe": "day",
        "instruments": ["NSE:A"],
        "entry": {"all": [{"indicator": "close", "operator": ">",
                           "compare_to": {"indicator": "close", "offset": 1}}]},
        "exit": {"any": [{"indicator": "close", "operator": ">", "value": 0}]},
        "risk": {"stop_loss": {"type": "percent", "value": 20},
                 "target": {"type": "percent", "value": 50}},
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 1,
    })


@pytest.fixture
def reader(tmp_path):
    backend = ParquetCandleBackend(None, str(tmp_path))
    start = date(2024, 1, 1)
    backend.write_candles(1, "day", daily_frame(start, 700, step=0.002))
    backend.write_candles(2, "day", daily_frame(start, 700, step=-0.001))
    return FrozenPriceReader(str(tmp_path), {"NSE:A": 1, "NSE:B": 2}, {}, frozenset())


def test_the_exam_reads_the_account_over_the_locked_year_only(reader):
    windows = ResearchWindows(DATA_END)
    exam = locked_year(strategy(), "day", ["NSE:A", "NSE:B"], reader, windows,
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1)
    assert exam is not None
    assert exam.account.is_account and exam.account.slots == 10
    assert exam.account.stocks == 2 and exam.basket.stocks == 2
    months = [m["month"] for m in exam.account.months]
    assert months[0] == "2025-08" and months[-1] == "2026-07"
    assert all(t.entry_fill_ts >= windows.locked_from_utc for t in exam.trades)
    assert exam.lakh.trades == exam.account.trades == len(exam.trades)
    assert exam.lakh.start_value == 100_000.0
    assert exam.lakh.end_value == pytest.approx(exam.account.end_value())


def test_the_daily_path_ends_where_the_account_months_add_up(reader):
    windows = ResearchWindows(DATA_END)
    exam = locked_year(strategy(), "day", ["NSE:A", "NSE:B"], reader, windows,
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1)
    assert exam.equity, "the chart needs daily rows"
    assert exam.equity[-1]["lakh_balance"] == pytest.approx(exam.lakh.end_value, abs=1.0)
    assert exam.equity[-1]["hold_balance"] is not None


def test_holding_is_measured_from_the_locked_years_first_close(reader):
    windows = ResearchWindows(DATA_END)
    exam = locked_year(strategy(), "day", ["NSE:A", "NSE:B"], reader, windows,
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1)
    # A drifts +0.2% a day, B -0.1%: about 250 trading days in the year.
    assert 100_000 < exam.hold_end_value < 200_000


def test_a_stock_whose_candles_stop_early_is_named(reader, tmp_path):
    backend = ParquetCandleBackend(None, str(tmp_path))
    backend.write_candles(3, "day", daily_frame(date(2024, 1, 1), 500, step=0.001))
    short_reader = FrozenPriceReader(str(tmp_path), {"NSE:A": 1, "NSE:C": 3}, {}, frozenset())
    exam = locked_year(strategy(), "day", ["NSE:A", "NSE:C"], short_reader, ResearchWindows(DATA_END),
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1)
    assert exam.stocks_short == ["NSE:C"]


def test_no_locked_data_means_no_exam(reader):
    windows = ResearchWindows(date(2030, 1, 1))
    assert locked_year(strategy(), "day", ["NSE:A"], reader, windows,
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1) is None


def test_the_traded_on_label_says_slots_and_stocks(reader):
    exam = locked_year(strategy(), "day", ["NSE:A", "NSE:B"], reader, ResearchWindows(DATA_END),
                       slippage_pct=0.0, cost_model=HoldingCostModel(), workers=1)
    assert basket_label(exam.account) == "10-slot account over 2 NIFTY200 stocks"
