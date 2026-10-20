"""The options pass rule (design 2026-10-02 §6; 12-month intraday minimum, 2026-10-19)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import rules  # noqa: E402


def reading(**kw):
    b = dict(month_count=13, avg_month_pct=6.0, trades_per_month=20.0, years_beating_holding_pct=100.0,
             edge_t=2.5, avg_excess_pct=1.0)
    h = dict(worst_day_pct=-4.0, worst_fall_pct=12.0)
    for k, v in kw.items():
        (h if k in h else b)[k] = v
    return SimpleNamespace(headline=h, basket=SimpleNamespace(**b))


def test_a_good_year_passes_as_intraday_but_is_too_short_as_positional():
    assert rules.disqualified(reading(), "intraday") is None
    assert "needs 24" in rules.disqualified(reading(), "positional")


def test_each_rule_says_why():
    assert "worst day" in rules.disqualified(reading(worst_day_pct=-12.0), "intraday")
    assert "fell" in rules.disqualified(reading(worst_fall_pct=35.0), "intraday")
    assert "lost money" in rules.disqualified(reading(avg_month_pct=-0.5), "intraday")
    assert "too slow" in rules.disqualified(reading(trades_per_month=2.0, avg_month_pct=1.0), "intraday")
    assert "years" in rules.disqualified(reading(years_beating_holding_pct=40.0), "intraday")
    assert "luck" in rules.disqualified(reading(edge_t=1.2), "intraday")
