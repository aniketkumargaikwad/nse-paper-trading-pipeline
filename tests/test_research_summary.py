"""What Opus is shown after a version is tested: training years only."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.summary import TrainingSummary, build_summary  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

LOCKED_WORDS = ("locked", "lakh", "hold_end", "verdict", "beat_holding")


def winner(symbol, timeframe, n=3, pct=5.0, hold=2.0):
    trades = tuple(
        trade(entry=ist(2024, 1, 2 + i, 10), exit_=ist(2024, 1, 2 + i, 14),
              entry_price=100.0, exit_price=100.0 * (1 + pct / 100))
        for i in range(n)
    )
    return ComboResult(symbol, timeframe, False, trades, hold_return_pct=hold)


def loser(symbol, timeframe, n=2, pct=-4.0, hold=1.0):
    return winner(symbol, timeframe, n=n, pct=pct, hold=hold)


def test_totals_count_every_tested_combination():
    got = build_summary([winner("A", "day"), loser("B", "day")], FREE, window_days_for=lambda r: 365)
    assert got.combos_tested == 2
    assert got.total_trades == 5


def test_fees_are_reported_beside_the_gross():
    got = build_summary([winner("A", "day")], FREE, window_days_for=lambda r: 365)
    assert got.net_pnl == pytest.approx(got.gross_pnl - got.fees_paid, abs=0.01)


def test_per_timeframe_shows_where_the_edge_was():
    got = build_summary(
        [winner("A", "day"), loser("B", "60m")], FREE, window_days_for=lambda r: 365
    )
    frames = {row["timeframe"]: row for row in got.per_timeframe}
    assert frames["day"]["net_pnl"] > 0 and frames["60m"]["net_pnl"] < 0
    assert frames["day"]["symbols_profitable_pct"] == 100.0


def test_holding_is_reported_per_timeframe():
    """So Opus can tell an edge from a rising market."""
    got = build_summary([winner("A", "day", hold=7.5)], FREE, window_days_for=lambda r: 365)
    assert got.per_timeframe[0]["avg_hold_return_pct"] == pytest.approx(7.5)


def test_top_and_bottom_are_capped_and_ordered():
    combos = [winner(f"S{i}", "day", pct=float(i)) for i in range(1, 21)]
    got = build_summary(combos, FREE, window_days_for=lambda r: 365)
    assert len(got.top) == 15 and len(got.bottom) == 15
    assert got.top[0]["net_pnl"] >= got.top[-1]["net_pnl"]
    assert got.bottom[0]["net_pnl"] <= got.bottom[-1]["net_pnl"]


def test_skips_are_counted_by_reason():
    combos = [
        winner("A", "day"),
        ComboResult("B", "day", False, (), "no candles in window"),
        ComboResult("C", "day", False, (), "no candles in window"),
    ]
    got = build_summary(combos, FREE, window_days_for=lambda r: 365)
    assert got.skipped == {"no candles in window": 2}
    assert got.combos_tested == 1


def test_a_summary_with_nothing_tested_is_still_a_summary():
    got = build_summary([ComboResult("A", "day", False, (), "no candles in window")],
                        FREE, window_days_for=lambda r: 365)
    assert got.combos_tested == 0 and got.total_trades == 0 and got.top == []


def test_the_summary_carries_no_locked_year_field():
    """The guarantee of design 2.4: what Opus sees cannot contain the exam."""
    fields = {f.name for f in dataclasses.fields(TrainingSummary)}
    assert not any(word in name for name in fields for word in LOCKED_WORDS)
    blob = json.dumps(build_summary([winner("A", "day")], FREE,
                                    window_days_for=lambda r: 365).as_dict()).lower()
    assert not any(word in blob for word in LOCKED_WORDS)
