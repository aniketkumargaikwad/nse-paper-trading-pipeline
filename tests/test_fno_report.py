"""What each options result shows (design §5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import report  # noqa: E402
from fno.engine import run  # noqa: E402
from fno_synthetic import D1, D6, DAYS, FORWARD, market  # noqa: E402
from test_fno_engine import FILL, straddle  # noqa: E402

HOLDING = {date(2023, 12, 29): FORWARD, **{d: FORWARD for d in DAYS}}


def result():
    return run(straddle(), market(overrides=FILL), start=D1, end=D6)


def test_the_headline_and_the_split_by_market_condition():
    r = report.build(result(), HOLDING)
    assert r.headline["trades"] == 2
    vix = {row["label"]: row for row in r.splits["vix at entry"]}
    assert vix["normal (13-18)"]["trades"] == 2
    assert [row["label"] for row in r.splits["event day"]] == ["other days"]
    assert {row["label"] for row in r.splits["days to expiry at entry"]} == {"0-3 days", "4-7 days"}
    assert any("closing prices" in note for note in r.notes)


def test_worst_days_and_named_shock_days():
    res = result()
    days = [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)]
    res.equity = pd.Series([500_000.0, 460_000.0, 470_000.0], index=days)
    res.start, res.end = days[0], days[-1]
    r = report.build(res, {date(2024, 5, 31): 22_000.0, **{d: 22_000.0 for d in days}})
    assert r.headline["worst_day_pct"] == pytest.approx(-8.0)
    assert r.headline["worst_day"] == date(2024, 6, 4)
    assert r.worst_days[0] == {"day": date(2024, 6, 4), "pct": pytest.approx(-8.0), "rupees": -40_000.0}
    assert [s["day"] for s in r.shock_days] == [date(2024, 6, 4)]


def test_the_text_names_the_strategy_and_the_caveats():
    text = report.to_text(report.build(result(), HOLDING))
    assert "test-straddle" in text and "Ten worst days" in text and "closing prices" in text
