"""What a tested options version reports to the AI: training numbers only."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.ai.summary import OptionsSummary, index_summary, score  # noqa: E402


def report(underlying="NIFTY", avg=0.5, end=date(2025, 10, 17), skipped=None):
    headline = {"end_value": 510000.0, "avg_month_pct": avg, "median_month_pct": 0.1,
                "months_positive_pct": 55.0, "worst_fall_pct": 9.0, "worst_day_pct": -3.0,
                "worst_day": date(2025, 4, 7), "trades": 200, "trades_per_month": 15.0,
                "win_rate_pct": 60.0, "avg_days_held": 0.0, "total_charges": 30000.0,
                "avg_holding_month_pct": 0.4, "years_beating_holding_pct": 50.0, "edge_t": 0.3}
    basket = SimpleNamespace(years=({"year": "2025", "strategy_pct": 4.0, "holding_pct": 3.0,
                                     "trades": 150, "months": 10},))
    return SimpleNamespace(underlying=underlying, start=date(2024, 10, 3), end=end, headline=headline,
                           basket=basket, splits={"vix at entry": [{"label": "calm (<13)", "trades": 3}]},
                           worst_days=[{"day": date(2025, 4, 7), "pct": -3.0, "rupees": -15000.0}] * 7,
                           shock_days=[], skipped=skipped or {}, notes=["n"])


def test_one_index_keeps_the_numbers_that_decide_it():
    s = index_summary(report(), mode="intraday", why_not="its lead over holding could be luck",
                      sessions=250)
    assert s["underlying"] == "NIFTY" and s["passes"] is False
    assert s["why_not"].startswith("its lead")
    assert s["avg_month_pct"] == 0.5 and s["trades_per_month"] == 15.0 and s["edge_t"] == 0.3
    assert len(s["worst_days"]) == 5
    assert s["years"][0]["year"] == "2025"


def test_a_strategy_skipped_on_most_days_is_flagged():
    s = index_summary(report(skipped={"no minute data": 140}), mode="intraday", why_not=None,
                      sessions=250)
    assert s["skipped_share_pct"] == 56.0
    assert "skipped" in s["warning"]


def test_the_pick_is_the_passing_index_with_the_best_month():
    summary = OptionsSummary("positional", [
        index_summary(report("NIFTY", 0.9), mode="positional", why_not=None, sessions=2400),
        index_summary(report("BANKNIFTY", 1.5), mode="positional", why_not="worst day -12%", sessions=2400),
    ])
    assert summary.pick()["underlying"] == "NIFTY"
    assert score(summary) > score(OptionsSummary("positional", [
        index_summary(report("NIFTY", 3.0), mode="positional", why_not="x", sessions=2400)]))


def test_nothing_passing_means_no_pick():
    summary = OptionsSummary("positional", [
        index_summary(report("NIFTY"), mode="positional", why_not="x", sessions=2400)])
    assert summary.pick() is None


def test_every_date_is_inside_the_training_window():
    summary = OptionsSummary("intraday", [
        index_summary(report(), mode="intraday", why_not=None, sessions=250)])
    text = json.dumps(summary.as_dict(), default=str)
    years = {int(y) for y in __import__("re").findall(r"\b(20\d\d)-\d\d-\d\d\b", text)}
    assert max(years) <= 2025 and "2026" not in text
