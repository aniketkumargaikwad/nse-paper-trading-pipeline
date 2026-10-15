"""The rate used only to discount option prices (design §2.4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.rates import risk_free  # noqa: E402


def test_each_year_has_a_rate_between_two_and_ten_percent():
    for year in range(2016, 2027):
        assert 0.02 < risk_free(date(year, 6, 1)) < 0.10


def test_the_covid_years_are_lower_than_the_years_around_them():
    assert risk_free(date(2020, 6, 1)) < risk_free(date(2019, 6, 1))
    assert risk_free(date(2021, 6, 1)) < risk_free(date(2023, 6, 1))


def test_a_year_outside_the_table_is_refused():
    with pytest.raises(LookupError):
        risk_free(date(2015, 6, 1))
