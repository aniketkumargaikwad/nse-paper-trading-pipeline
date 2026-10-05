"""The options account: Rs 5 lakh, whole sets, months and days (design §4, §5).

A SET is one of every leg at its `lots`. Sizing takes as many whole sets as
the allowed share of the balance covers in margin, never more - a balance
that has grown buys more sets, one that has shrunk buys fewer, so monthly
returns compound on the balance (the 22 Sep 2026 fix, research.portfolio).

The balance is cash plus open positions at the day's settlement prices.
Margin is not spent - it is blocked - so it changes the size, not the cash.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import pandas as pd

CAPITAL = 500_000.0


def sets_affordable(equity: float, margin_per_set: float, max_margin_pct: float = 100.0) -> int:
    if margin_per_set <= 0 or equity <= 0:
        return 0
    return int(math.floor(equity * max_margin_pct / 100 / margin_per_set + 1e-9))


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _chain(month_ends: pd.Series, previous: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for month, value in month_ends.items():
        out[month] = round(100 * (float(value) / previous - 1), 4) if previous > 0 else 0.0
        previous = float(value)
    return out


def month_returns(equity: pd.Series, capital: float) -> dict[str, float]:
    """Each month-end balance over the previous one (the first over the capital), in %."""
    s = equity.sort_index()
    return _chain(s.groupby([_month(d) for d in s.index]).last(), capital)


def day_returns(equity: pd.Series, capital: float) -> pd.Series:
    """Each day's balance over the day before (the first over the capital), in %."""
    s = equity.sort_index().astype(float)
    previous = s.shift(1)
    previous.iloc[0] = capital
    return 100 * (s / previous - 1)


def holding_month_returns(closes: Mapping[date, float], start: date, end: date) -> dict[str, float]:
    """Holding the index over the same months, from the last close before `start`."""
    s = pd.Series(dict(closes), dtype=float).sort_index()
    before = s[[d < start for d in s.index]]
    inside = s[[start <= d <= end for d in s.index]]
    if inside.empty:
        return {}
    first = float(before.iloc[-1]) if len(before) else float(inside.iloc[0])
    return _chain(inside.groupby([_month(d) for d in inside.index]).last(), first)
