"""Paper trading momentum + the cash rule, forward from 20 Oct 2026. NO real money.

The rule (docs/research/2026-10-20-momentum-rotation.md, picked before the
evidence was seen): on each month's last session, if NIFTY 50 closes at or
above its 200-session average, hold NSE's Nifty200 Momentum 30 index for the
next month; otherwise hold cash (6% a year). Switches fill at the next
session's close and pay 0.2%.

The book is recomputed from the start date on every run, from NSE's own
total-return index levels, so nothing in it can be edited after the fact:
the start date, the rule and the costs are fixed here. It is compared with
simply holding the Momentum 30 index and NIFTY 50 from the same day.

In real life the index would be held through a fund that tracks it, which
charges a fee the index does not: the book is slightly flattered by that.

Pure: no I/O.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from research.rotation import TREND_SESSIONS, index_switch

MOMENTUM = "NIFTY200 MOMENTUM 30"
NIFTY50 = "NIFTY 50"
START = date(2026, 10, 20)
CAPITAL = 500_000.0
CASH_YIELD = 0.06
COST_PCT = 0.2


def _hold(series: pd.Series, start: date) -> float:
    s = series.dropna()
    s = s[s.index >= start]
    return round(CAPITAL * float(s.iloc[-1] / s.iloc[0]), 2)


def book(tri: pd.DataFrame, *, start: date = START) -> dict:
    """Where the paper account stands, from NSE total-return levels (one column per index)."""
    tri = tri.sort_index()
    end = tri.index.max()
    if end < start:
        return {"as_of": end.isoformat(), "start": start.isoformat(), "capital": CAPITAL,
                "state": "not started", "value": CAPITAL, "momentum_hold": CAPITAL, "nifty_hold": CAPITAL,
                "nifty_close": None, "nifty_average": None, "signal_now": None, "decisions": [], "equity": []}
    result = index_switch(tri[MOMENTUM], tri[NIFTY50], start=start, end=end,
                          cash_yield=CASH_YIELD, cost_pct=COST_PCT, capital=CAPITAL)
    filled = [r for r in result.rebalances if r.filled <= end]
    mom, n50 = (tri[c].reindex(result.equity.index).ffill() for c in (MOMENTUM, NIFTY50))
    nifty = tri[NIFTY50].dropna()
    average = float(nifty.rolling(TREND_SESSIONS, min_periods=1).mean().iloc[-1])
    return {
        "as_of": end.isoformat(), "start": start.isoformat(), "capital": CAPITAL,
        "state": "invested" if filled and filled[-1].holdings else "cash",
        "value": round(float(result.equity.iloc[-1]), 2),
        "momentum_hold": _hold(tri[MOMENTUM], start), "nifty_hold": _hold(tri[NIFTY50], start),
        "nifty_close": round(float(nifty.iloc[-1]), 2), "nifty_average": round(average, 2),
        "signal_now": "above" if nifty.iloc[-1] >= average else "below",
        "decisions": [{"decided": r.decided.isoformat(), "filled": r.filled.isoformat(),
                       "action": "buy the Momentum 30 index" if r.holdings else "sell to cash",
                       "cost": round(r.cost, 2)} for r in filled],
        "equity": [[d.isoformat(), round(v, 2), round(CAPITAL * float(mom[d] / mom.iloc[0]), 2),
                    round(CAPITAL * float(n50[d] / n50.iloc[0]), 2)] for d, v in result.equity.items()],
    }


def month_turned(tri: pd.DataFrame) -> bool:
    """Is the latest session the first of a new month - the day last month's decision is filled?"""
    days = sorted(tri.dropna(how="all").index)
    return len(days) > 1 and (days[-1].year, days[-1].month) != (days[-2].year, days[-2].month)


def message(b: dict) -> str:
    gain = lambda v: f"Rs {v:,.0f} ({100 * (v / b['capital'] - 1):+.1f}%)"   # noqa: E731
    if b["state"] == "not started":
        return f"Momentum paper account starts {b['start']} with Rs {b['capital']:,.0f} (no real money)."
    lines = [f"Momentum paper account, {b['as_of']} (no real money)",
             f"This month: {'HOLDING the Momentum 30 index' if b['state'] == 'invested' else 'IN CASH'} - "
             f"NIFTY 50 is {b['signal_now']} its 200-day average.",
             f"Paper account: {gain(b['value'])}",
             f"Just holding Momentum 30: {gain(b['momentum_hold'])}",
             f"Just holding NIFTY 50: {gain(b['nifty_hold'])}",
             f"Since {b['start']}, starting from Rs {b['capital']:,.0f}."]
    return "\n".join(lines)
