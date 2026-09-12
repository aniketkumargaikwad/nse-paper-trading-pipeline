"""The simulator's single fee seam charges overnight trades delivery rates.

_make_trade is called by both the v2 simulator and the v3 state machine, so
this one seam covers every strategy format.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import _make_trade, build_cost_model  # noqa: E402
from config import Settings  # noqa: E402
from costs import CostModel, DeliveryCostModel, FlatCostModel, HoldingCostModel  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def make(entry_ts, exit_ts, model):
    return _make_trade(
        entry_signal_ts=entry_ts, entry_fill_ts=entry_ts,
        exit_signal_ts=exit_ts, exit_fill_ts=exit_ts,
        position_type="long", quantity=100,
        intended_entry=1000.0, entry_price=1000.0,
        intended_exit=1010.0, exit_price=1010.0,
        exit_reason="signal", cost_per_trade_inr=30.0, cost_model=model,
    )


def test_overnight_trade_pays_delivery_charges():
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), HoldingCostModel())
    assert t.costs == pytest.approx(DeliveryCostModel().round_trip(1000.0, 1010.0, 100))
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.costs, abs=1e-3)


def test_same_day_trade_pays_intraday_charges():
    t = make(ist(2026, 8, 3, 10, 0), ist(2026, 8, 3, 14, 0), HoldingCostModel())
    assert t.costs == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_the_plain_itemised_model_is_unchanged_even_overnight():
    """Existing stored results used CostModel; they must stay reproducible."""
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), CostModel())
    assert t.costs == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_no_model_still_charges_the_flat_amount():
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), None)
    assert t.costs == 30.0


def settings(cost_model: str) -> Settings:
    return Settings(supabase_url="", supabase_service_role_key="", cost_model=cost_model)


def test_cost_model_holding_selects_the_holding_model():
    assert isinstance(build_cost_model(settings("holding")), HoldingCostModel)


def test_existing_cost_model_choices_are_unchanged():
    itemised = build_cost_model(settings("itemised"))
    assert isinstance(itemised, CostModel) and not isinstance(itemised, HoldingCostModel)
    assert isinstance(build_cost_model(settings("flat")), FlatCostModel)
