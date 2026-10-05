"""Rs 5 lakh, sized in whole sets by margin, judged by month and by day (design §4, §5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.account import day_returns, holding_month_returns, month_returns, sets_affordable  # noqa: E402


def test_whole_sets_never_more_margin_than_allowed():
    assert sets_affordable(500_000, 150_000) == 3
    assert sets_affordable(450_000, 150_000) == 3          # exactly fits
    assert sets_affordable(500_000, 150_000, 50) == 1
    assert sets_affordable(100_000, 150_000) == 0
    assert sets_affordable(500_000, 0) == 0


def test_months_compound_on_the_balance():
    equity = pd.Series([510_000.0, 520_000.0, 494_000.0, 500_000.0],
                       index=[date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28)])
    assert month_returns(equity, 500_000) == {
        "2024-01": 4.0, "2024-02": -5.0, "2024-03": pytest.approx(1.2146, abs=1e-4)}


def test_the_first_day_is_measured_from_the_capital():
    equity = pd.Series([450_000.0, 495_000.0], index=[date(2024, 1, 1), date(2024, 1, 2)])
    assert list(day_returns(equity, 500_000).round(4)) == [-10.0, 10.0]


def test_holding_starts_from_the_close_before_the_run():
    closes = {date(2023, 12, 29): 100.0, date(2024, 1, 31): 110.0, date(2024, 2, 29): 99.0}
    assert holding_month_returns(closes, date(2024, 1, 1), date(2024, 2, 29)) == {
        "2024-01": 10.0, "2024-02": -10.0}
