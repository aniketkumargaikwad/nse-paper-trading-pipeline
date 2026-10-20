"""The options pass rule (design 2026-10-02 §6), in the owner's words.

A strategy qualifies only if all hold: enough history (24 months positional,
12 months intraday - the owner's decision of 19 Oct 2026, because real option
minutes begin in Oct 2024), worst fall within 30%, worst single day no worse
than -10%, a positive average month, at least 10 trades a month or the target
month, beating holding the index in at least half its years, and a lead over
holding larger than luck explains (t >= 2).
"""

from __future__ import annotations

from typing import Any

from research.portfolio import LUCK_STRONG_T
from research.segment import TARGET_MONTHLY_MIN

MIN_MONTHS = {"positional": 24, "intraday": 12}
MAX_FALL_PCT = 30.0
WORST_DAY_PCT = -10.0
MIN_TRADES_PER_MONTH = 10.0
MIN_YEARS_BEATING_HOLDING_PCT = 50.0


def disqualified(report: Any, mode: str) -> str | None:
    """Why `report` cannot pass, or None if it does."""
    h, b = report.headline, report.basket
    need = MIN_MONTHS[mode]
    if b.month_count < need:
        return f"only {b.month_count} months of history (needs {need})"
    if h["worst_day_pct"] < WORST_DAY_PCT:
        return f"worst day {h['worst_day_pct']:+.1f}% (limit {WORST_DAY_PCT:.0f}%)"
    if h["worst_fall_pct"] > MAX_FALL_PCT:
        return f"fell {h['worst_fall_pct']:.1f}% from a high (limit {MAX_FALL_PCT:.0f}%)"
    if b.avg_month_pct <= 0:
        return f"lost money on average: {b.avg_month_pct:+.2f}% a month"
    if b.trades_per_month < MIN_TRADES_PER_MONTH and b.avg_month_pct < TARGET_MONTHLY_MIN:
        return (f"too slow: {b.trades_per_month:.1f} trades a month (needs {MIN_TRADES_PER_MONTH:.0f}, "
                f"or {TARGET_MONTHLY_MIN:g}% a month)")
    if b.years_beating_holding_pct < MIN_YEARS_BEATING_HOLDING_PCT:
        return f"beat holding the index in only {b.years_beating_holding_pct:.0f}% of years"
    luck = b.avg_excess_pct > 0 if b.edge_t is None else b.edge_t >= LUCK_STRONG_T
    if not luck:
        t = "n/a" if b.edge_t is None else f"{b.edge_t:+.2f}"
        return f"its lead over holding could be luck: t = {t} (needs {LUCK_STRONG_T:.0f})"
    return None
