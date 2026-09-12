"""The research page's pure helpers (no Streamlit runtime needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_pages.research_page import GRID_COLUMNS, grid_frame, verdict_label  # noqa: E402


def runs_frame(**over):
    row = {
        "id": "run-1", "started_at": "2026-09-12T01:00:00+00:00",
        "final_strategy_name": "N200-PULLBACK-DAY", "pick_symbol": "NSE:360ONE",
        "pick_timeframe": "day", "trades_per_month": 0.5, "win_rate_pct": 66.67,
        "lakh_end_value": 107461.0, "hold_end_value": 107622.0, "worst_dip_pct": 7.1,
        "verdict_passed": False, "beat_holding": False, "combos_profitable": 4,
        "combos_tested": 18, "versions_tried": 1, "status": "completed",
    }
    row.update(over)
    return pd.DataFrame([row])


def test_grid_has_the_columns_the_design_asks_for():
    assert list(grid_frame(runs_frame(), {}).columns) == GRID_COLUMNS
    assert len(GRID_COLUMNS) == 15


def test_grid_reads_the_description_from_the_strategy():
    got = grid_frame(runs_frame(), {"N200-PULLBACK-DAY": "Buys a shallow dip in an uptrend"})
    assert got["Description"].iloc[0] == "Buys a shallow dip in an uptrend"


def test_a_strategy_without_a_description_shows_a_dash():
    assert grid_frame(runs_frame(), {})["Description"].iloc[0] == "—"


def test_broad_or_lucky_shows_the_share_of_profitable_combinations():
    assert grid_frame(runs_frame(), {})["Broad or lucky"].iloc[0] == "4 of 18"


def test_money_is_grouped_the_indian_way():
    got = grid_frame(runs_frame(), {})
    assert got["₹1 lakh → became"].iloc[0] == "₹1,07,461"
    assert got["Just holding → became"].iloc[0] == "₹1,07,622"


def test_a_loss_keeps_its_sign():
    assert grid_frame(runs_frame(lakh_end_value=72980.0), {})["₹1 lakh → became"].iloc[0] == "₹72,980"


def test_a_run_with_no_pick_shows_dashes_not_zeros():
    got = grid_frame(runs_frame(
        pick_symbol=None, pick_timeframe=None, lakh_end_value=None,
        hold_end_value=None, worst_dip_pct=None, win_rate_pct=None,
        trades_per_month=None, beat_holding=None,
    ), {})
    assert got["₹1 lakh → became"].iloc[0] == "—"
    assert got["Best stock/index"].iloc[0] == "—"
    assert got["Success ratio"].iloc[0] == "—"
    assert got["Beat holding"].iloc[0] == "—"


def test_verdict_label_is_readable():
    assert verdict_label(True) == "✅ Passed"
    assert verdict_label(False) == "❌ Failed"
    assert verdict_label(None) == "—"


def test_newest_run_comes_first():
    two = pd.concat([
        runs_frame(id="old", started_at="2026-09-10T01:00:00+00:00", pick_symbol="NSE:OLD"),
        runs_frame(id="new", started_at="2026-09-12T01:00:00+00:00", pick_symbol="NSE:NEW"),
    ], ignore_index=True)
    got = grid_frame(two, {})
    assert got["Best stock/index"].iloc[0] == "NSE:NEW"
    assert got["Date"].iloc[0] == "12 Sep 2026"


def test_grid_of_no_runs_is_empty_but_shaped():
    got = grid_frame(pd.DataFrame(), {})
    assert got.empty and list(got.columns) == GRID_COLUMNS
