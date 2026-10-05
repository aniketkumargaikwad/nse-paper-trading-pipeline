"""What each options result shows (design §5).

The month-by-month reading is the one every stock result is judged by
(research.portfolio.summarise) - the same average and median month, worst
fall, years beating holding and luck test - with holding the INDEX on the
same capital as the comparison. On top of it: the worst single day, every
trade split by the market it was opened in, the ten worst days and the named
shock days, and the notes that say what daily data cannot see.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fno import account
from fno.engine import BacktestResult, Trade
from research.portfolio import Basket, luck_label, summarise

SHOCK_DAYS = {
    date(2020, 3, 12): "Covid crash",
    date(2020, 3, 23): "Covid low",
    date(2022, 2, 24): "Russia invades Ukraine",
    date(2024, 6, 4): "General-election result",
}


def _vix_band(v: float) -> str:
    if v != v:
        return "unknown"
    return "calm (<13)" if v < 13 else "normal (13-18)" if v <= 18 else "fearful (>18)"


def _dte_band(d: int) -> str:
    return "0-3 days" if d <= 3 else "4-7 days" if d <= 7 else "8-15 days" if d <= 15 else "16+ days"


def _iv_band(r: float) -> str:
    if r != r:
        return "unknown"
    return "low third" if r < 100 / 3 else "middle third" if r < 200 / 3 else "top third"


SPLITS: tuple[tuple[str, Callable[[dict[str, Any]], str]], ...] = (
    ("vix at entry", lambda f: _vix_band(f["vix"])),
    ("trend at entry", lambda f: f["trend"]),
    ("days to expiry at entry", lambda f: _dte_band(f["dte"])),
    ("iv rank at entry", lambda f: _iv_band(f["iv_rank"])),
    ("event day", lambda f: "event days" if f["event_today"] else "other days"),
)


@dataclass(frozen=True)
class Report:
    name: str
    underlying: str
    start: date
    end: date
    capital: float
    headline: dict[str, Any]
    basket: Basket
    splits: dict[str, list[dict[str, Any]]]
    worst_days: list[dict[str, Any]]
    shock_days: list[dict[str, Any]]
    skipped: dict[str, int]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "underlying": self.underlying, "start": self.start,
                "end": self.end, "capital": self.capital, "headline": self.headline,
                "basket": self.basket.as_dict(), "splits": self.splits,
                "worst_days": self.worst_days, "shock_days": self.shock_days,
                "skipped": self.skipped, "notes": self.notes}


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _months_open(trade: Trade, end: date) -> set[str]:
    out, day, last = set(), trade.entered.replace(day=1), trade.exited or end
    while day <= last:
        out.add(_month(day))
        day = (day + timedelta(days=32)).replace(day=1)
    return out


def _split(trades: list[Trade], label: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        groups.setdefault(label(t.facts), []).append(t)
    rows = []
    for name, group in sorted(groups.items()):
        pnl = [t.pnl for t in group]
        rows.append({"label": name, "trades": len(group),
                     "win_rate_pct": round(100 * sum(p > 0 for p in pnl) / len(group), 2),
                     "avg_pnl": round(statistics.mean(pnl), 2), "total_pnl": round(sum(pnl), 2),
                     "avg_return_pct": round(statistics.mean(100 * t.pnl / t.equity_at_entry
                                                             for t in group), 4)})
    return rows


def build(result: BacktestResult, holding_closes: Mapping[date, float]) -> Report:
    if result.equity.empty:
        raise ValueError(f"{result.strategy}: no sessions between {result.start} and {result.end}")
    equity = result.equity.sort_index()
    months = account.month_returns(equity, result.capital)
    holding = account.holding_month_returns(holding_closes, result.start, result.end)
    opened = Counter(_month(t.entered) for t in result.trades)
    won = Counter(_month(t.entered) for t in result.trades if t.pnl > 0)
    rows = [{"month": m, "strategy_pct": pct, "holding_pct": holding.get(m, 0.0),
             "trades": opened.get(m, 0), "wins": won.get(m, 0), "stocks": 1}
            for m, pct in months.items()]
    active = set().union(*(_months_open(t, result.end) for t in result.trades)) & set(months)
    basket = summarise("day", 1, rows, stock_months=len(rows), trading_months=len(active))

    days = account.day_returns(equity, result.capital)
    previous = equity.shift(1)
    previous.iloc[0] = result.capital
    n = len(result.trades)
    headline = {
        "end_value": round(float(equity.iloc[-1]), 2),
        "avg_month_pct": basket.avg_month_pct,
        "median_month_pct": basket.median_month_pct,
        "months_positive_pct": basket.months_positive_pct,
        "worst_fall_pct": basket.worst_dip_pct,
        "worst_day_pct": round(float(days.min()), 4),
        "worst_day": days.idxmin(),
        "trades": n,
        "trades_per_month": basket.trades_per_month,
        "win_rate_pct": round(100 * sum(t.pnl > 0 for t in result.trades) / n, 2) if n else 0.0,
        "avg_days_held": round(statistics.mean(t.days_held(result.end) for t in result.trades), 2) if n else 0.0,
        "total_charges": round(sum(t.charges for t in result.trades), 2),
        "avg_holding_month_pct": basket.avg_holding_month_pct,
        "years_beating_holding_pct": basket.years_beating_holding_pct,
        "edge_t": basket.edge_t,
    }
    worst = [{"day": d, "pct": round(float(p), 4), "rupees": round(float(equity[d] - previous[d]), 2)}
             for d, p in days.nsmallest(10).items()]
    shocks = [{"day": d, "name": name, "pct": round(float(days[d]), 4)}
              for d, name in SHOCK_DAYS.items() if d in days.index]
    return Report(result.strategy, result.underlying, result.start, result.end, result.capital,
                  headline, basket, {title: _split(result.trades, label) for title, label in SPLITS},
                  worst, shocks, dict(Counter(s.kind for s in result.skips)), list(result.notes))


def _rs(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"Rs {x:,.0f}"


def to_text(r: Report) -> str:
    h = r.headline
    lines = [
        f"# {r.name} - {r.underlying}, {r.start} to {r.end}", "",
        f"{_rs(r.capital)} became {_rs(h['end_value'])}.",
        f"Average month {h['avg_month_pct']:+.2f}% (median {h['median_month_pct']:+.2f}%), "
        f"{h['months_positive_pct']:.0f}% of months up, worst fall {h['worst_fall_pct']:.1f}%, "
        f"worst day {h['worst_day_pct']:+.2f}% on {h['worst_day']}.",
        f"{h['trades']} trades ({h['trades_per_month']:.1f} a month), {h['win_rate_pct']:.0f}% won, "
        f"held {h['avg_days_held']:.1f} days on average, {_rs(h['total_charges'])} in charges.",
        f"Holding the index: {h['avg_holding_month_pct']:+.2f}% a month. Beat it in "
        f"{h['years_beating_holding_pct']:.0f}% of years; luck test: {luck_label(h['edge_t'])}.",
    ]
    for title, rows in r.splits.items():
        lines += ["", f"## By {title}", "", "| | trades | won | avg P&L | total P&L |",
                  "|---|---:|---:|---:|---:|"]
        lines += [f"| {row['label']} | {row['trades']} | {row['win_rate_pct']:.0f}% | "
                  f"{_rs(row['avg_pnl'])} | {_rs(row['total_pnl'])} |" for row in rows]
    lines += ["", "## Ten worst days", ""]
    lines += [f"- {d['day']}: {d['pct']:+.2f}% ({_rs(d['rupees'])})" for d in r.worst_days]
    if r.shock_days:
        lines += ["", "## Named shock days", ""]
        lines += [f"- {s['day']} {s['name']}: {s['pct']:+.2f}%" for s in r.shock_days]
    if r.skipped:
        lines += ["", "## Entries skipped", ""] + [f"- {k}: {n}" for k, n in sorted(r.skipped.items())]
    lines += ["", "## Read with care", ""] + [f"- {note}" for note in r.notes]
    return "\n".join(lines) + "\n"
