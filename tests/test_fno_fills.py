"""Half the bid-ask spread on every fill (design §4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.fills import fill_price, half_spread  # noqa: E402


def test_half_a_percent_of_the_premium_or_one_tick():
    assert half_spread(100.0, 1000) == pytest.approx(0.5)
    assert half_spread(4.0, 1000) == pytest.approx(0.05)      # 0.5% would be 2 paise: one tick


def test_doubled_in_a_thin_market():
    assert half_spread(100.0, 99) == pytest.approx(1.0)
    assert half_spread(100.0, 100) == pytest.approx(0.5)


def test_buying_pays_up_selling_receives_less_never_below_zero():
    assert fill_price(100.0, "buy", 1000) == pytest.approx(100.5)
    assert fill_price(100.0, "sell", 1000) == pytest.approx(99.5)
    assert fill_price(0.05, "sell", 0) == 0.0
