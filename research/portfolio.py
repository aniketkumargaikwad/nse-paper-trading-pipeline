"""The basket: Rs 1 lakh spread equally over every stock, judged month by month.

WHY A BASKET
------------
The loop used to pick ONE stock out of ~1,177 tries - the one with the
largest lead over holding across eight training years - and then judge that
stock alone over the locked year. The best of 1,177 is nearly always a fluke,
and one stock traded once or six times in a year says nothing about
"consistently" or "per month", which is how the owner states the goal.

So a version is judged the way it would actually be run: every stock on one
timeframe at once, each with its own Rs 1 lakh sleeve, and the basket's
return measured per calendar month. That yields hundreds of trades a year
instead of one, and it yields the number the goal is stated in.

TWO READINGS OF THE SAME TRADES
-------------------------------
* The BASKET (here): every sleeve funded, all the time. A clean statistical
  average of what the rule earns per stock, and the benchmark for holding.
  It UNDERSTATES a selective strategy: a rule in the market 5% of the time
  leaves 95% of the money idle.
* The ACCOUNT (research.account): a fixed number of slots, signals taken as
  they arrive, the rest skipped. What a real account with that much money
  would have earned. The pick and the exam use this one.

WHAT THE NUMBERS MEAN
---------------------
* A sleeve's month = that month's net P&L (trades EXITING in it) / Rs 1 lakh.
  Fixed notional, no compounding within a sleeve - the sweep already sizes
  every trade at Rs 1 lakh, so this is exactly what those trades returned.
* Holding's month = last close / previous month's last close - 1, or / the
  first close for the month a symbol's data begins.
* The basket's month = the MEAN over stocks with data in that month. A stock
  joins the basket when its history starts. That still carries the
  survivorship bias every result here carries (today's list applied to the
  past); the holding line carries the same bias, which is why excess over
  holding is the honest comparison.
* A month's PERCENTAGE stays linear, and deliberately so: it is that month's
  net P&L over the money the sweep put at risk, which is what "average month"
  has to mean for the owner's goal to be checkable. avg_month_pct,
  median_month_pct, best/worst_month_pct and the excess figures all read
  those months and are unaffected by anything below.
* The BALANCE those months add up to is COMPOUNDED: each month multiplies the
  running balance rather than adding a slice of the starting one. See
  `equity_path`. A balance is what a real account holds, so it cannot go
  below zero, and everything measured on the balance - the worst dip, the end
  value, the daily path - inherits that.
* `total_pct` / `holding_total_pct` remain the plain sum of the months, which
  is "the average month times the months" and not a balance. They are a
  reading of the months, not a claim about what the money became.

Pure: no I/O, no clock, no database.
"""

from __future__ import annotations

import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from backtest_types import SimTrade
from config import IST
from research.month_closes import month_closes  # noqa: F401 - re-exported for callers
from research.sweep import ComboResult

NOTIONAL = 100_000.0

# The luck check: how many standard errors the average monthly excess over
# holding sits above zero. Reported in words, because the owner reads it.
LUCK_STRONG_T = 2.0
LUCK_WEAK_T = 1.0


def month_of(ts: datetime) -> str:
    """The IST calendar month a moment falls in, as 'YYYY-MM'."""
    return ts.astimezone(IST).strftime("%Y-%m")


@dataclass(frozen=True)
class SleeveMonth:
    strategy_pct: float
    holding_pct: float
    trades: int
    wins: int


def sleeve_months(result: ComboResult, *, notional: float = NOTIONAL) -> dict[str, SleeveMonth]:
    """One stock's months: what its sleeve made, what holding it made."""
    pnl: dict[str, float] = {}
    count: dict[str, int] = {}
    wins: dict[str, int] = {}
    for t in result.trades:
        key = month_of(t.exit_fill_ts)
        pnl[key] = pnl.get(key, 0.0) + t.net_pnl
        count[key] = count.get(key, 0) + 1
        if t.net_pnl > 0:
            wins[key] = wins.get(key, 0) + 1

    out: dict[str, SleeveMonth] = {}
    previous: float | None = None
    for month, first, last in result.month_closes:
        base = previous if previous is not None else first
        holding = 100.0 * (last - base) / base if base else 0.0
        out[month] = SleeveMonth(
            strategy_pct=100.0 * pnl.get(month, 0.0) / notional,
            holding_pct=holding,
            trades=count.get(month, 0),
            wins=wins.get(month, 0),
        )
        previous = last
    return out


@dataclass(frozen=True)
class Basket:
    """One reading of a set of trades, month by month. Also used for an account."""

    timeframe: str
    stocks: int
    months: tuple[dict[str, Any], ...]      # month, strategy_pct, holding_pct, trades, stocks
    trades: int
    winning_trades: int
    avg_month_pct: float
    median_month_pct: float
    months_positive_pct: float
    best_month_pct: float
    worst_month_pct: float
    avg_holding_month_pct: float
    avg_excess_pct: float
    # Excess over holding for the TIME THE MONEY WAS AT WORK: the average
    # month minus holding's average month scaled by how much of the slot- or
    # sleeve-time was actually in a position. A rule that is flat 85% of the
    # time is not beta for those 85% and must not be judged as if it were;
    # idle capital is the owner's problem to see, not a reason to call a
    # real edge "no edge". The 16 Sep 2026 run failed all three ideas on the
    # unscaled gate while Opus itself pointed this out.
    avg_excess_active_pct: float
    worst_dip_pct: float
    trades_per_month: float
    sleeve_use_pct: float
    edge_t: float | None
    years: tuple[dict[str, Any], ...]
    # Share of calendar years the reading beat holding. "Consistently" is a
    # claim about years, not about one average. Named years_positive_pct until
    # 26 Sep 2026, which read as "years that made money" - Opus was told 11%
    # for a strategy that made money in five years of nine.
    years_beating_holding_pct: float = 0.0
    # Account readings only (research.account): how many positions at once,
    # how much of that slot-time was used, and how many signals were skipped
    # because every slot was taken.
    slots: int | None = None
    slot_use_pct: float | None = None
    signals_skipped_pct: float | None = None

    @property
    def month_count(self) -> int:
        return len(self.months)

    @property
    def is_account(self) -> bool:
        return self.slots is not None

    def total_pct(self) -> float:
        """The months added up. Not a balance - see the module docstring."""
        return sum(m["strategy_pct"] for m in self.months)

    def holding_total_pct(self) -> float:
        return sum(m["holding_pct"] for m in self.months)

    def end_value(self, start: float = NOTIONAL) -> float:
        """What `start` became, month compounding on month."""
        return round(equity_path([m["strategy_pct"] for m in self.months], start=start)[-1], 2)

    def holding_end_value(self, start: float = NOTIONAL) -> float:
        """What `start` became just holding - compounded the same way, or the
        comparison would be a strategy's balance against holding's sum."""
        return round(equity_path([m["holding_pct"] for m in self.months], start=start)[-1], 2)

    def as_dict(self, *, with_months: bool = False) -> dict[str, Any]:
        """Compact by default: the months are ~100 rows per timeframe."""
        out = {
            "timeframe": self.timeframe,
            "stocks": self.stocks,
            "months": self.month_count,
            "trades": self.trades,
            "trades_per_month": self.trades_per_month,
            "avg_month_pct": self.avg_month_pct,
            "median_month_pct": self.median_month_pct,
            "months_positive_pct": self.months_positive_pct,
            "best_month_pct": self.best_month_pct,
            "worst_month_pct": self.worst_month_pct,
            "avg_holding_month_pct": self.avg_holding_month_pct,
            "avg_excess_pct": self.avg_excess_pct,
            "avg_excess_active_pct": self.avg_excess_active_pct,
            "worst_dip_pct": self.worst_dip_pct,
            "sleeve_use_pct": self.sleeve_use_pct,
            "years_beating_holding_pct": self.years_beating_holding_pct,
            "edge_t": self.edge_t,
            "luck_check": luck_label(self.edge_t),
            "years": list(self.years),
        }
        if self.is_account:
            out["slots"] = self.slots
            out["slot_use_pct"] = self.slot_use_pct
            out["signals_skipped_pct"] = self.signals_skipped_pct
        if with_months:
            out["month_rows"] = list(self.months)
        return out


def luck_label(t: float | None) -> str:
    if t is None:
        return "too few months to tell"
    if t >= LUCK_STRONG_T:
        return "unlikely to be luck"
    if t >= LUCK_WEAK_T:
        return "could be luck"
    return "cannot be told from luck"


def equity_path(monthly_pct: Sequence[float], *, start: float = NOTIONAL) -> list[float]:
    """The balance after each month, starting at `start`.

    Each month MULTIPLIES the balance. Until 22 Sep 2026 it added a slice of
    the STARTING balance instead:

        path.append(path[-1] + notional * month_pct / 100)

    which is not an account. Money that is down 50% and loses another 10%
    loses 10% of what is left, not 10% of what it began with, so a run of
    losing months drove that path through zero and into negative rupees. The
    worst-dip figure measured on it then reported falls no cash account can
    have: the 21 Sep 2026 run logged "fell 961.4% from a high" on the 5-minute
    account, and the picker refused it for a 30% limit on the strength of that
    number. It was wrong in the flattering direction too - an account that had
    grown for years was divided by a peak that had barely moved, so its falls
    read far smaller than they were.

    The trades themselves are sized at a fixed Rs 1 lakh, so a month's figure
    is a RETURN, and a return is what a real account earns on whatever it
    holds at the time. Applying it to the running balance is the same account
    sized to its own equity. What the sweep does NOT model - sizing that never
    scales down, so ten Rs 1 lakh slots stay ten Rs 1 lakh slots after the
    account has halved - is a separate question, and deliberately left alone
    here: it is a choice about how to trade, not arithmetic.

    Floored at zero. A month below -100% wipes the account out, and nothing
    multiplies it back afterwards, which is exactly what ruin is.
    """
    path = [float(start)]
    for pct in monthly_pct:
        path.append(max(path[-1] * (1 + pct / 100), 0.0))
    return path


def _dip_of(path: Sequence[float]) -> float:
    """Deepest fall from a running high, in percent of that high."""
    worst = 0.0
    peak = float("-inf")
    for value in path:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak * 100)
    return round(worst, 2)


def summarise(
    timeframe: str, stocks: int, months: Sequence[dict[str, Any]], *,
    stock_months: int, trading_months: int, notional: float = NOTIONAL, **extra: Any,
) -> Basket:
    """The figures every reading is judged by, from its month rows.

    Each row carries month, strategy_pct, holding_pct, trades, wins, stocks.
    `stock_months` / `trading_months` give sleeve (or slot) utilisation.
    """
    strategy = [m["strategy_pct"] for m in months]
    holding = [m["holding_pct"] for m in months]
    excess = [s - h for s, h in zip(strategy, holding)]
    trades = sum(m["trades"] for m in months)
    wins = sum(m["wins"] for m in months)

    edge_t = None
    if len(excess) >= 3:
        sd = statistics.stdev(excess)
        if sd > 0:
            edge_t = round(statistics.mean(excess) / (sd / len(excess) ** 0.5), 3)

    path = equity_path(strategy, start=notional)

    by_year: dict[str, dict[str, Any]] = {}
    for m in months:
        year = m["month"][:4]
        row = by_year.setdefault(year, {"year": year, "strategy_pct": 0.0,
                                        "holding_pct": 0.0, "trades": 0, "months": 0})
        row["strategy_pct"] = round(row["strategy_pct"] + m["strategy_pct"], 2)
        row["holding_pct"] = round(row["holding_pct"] + m["holding_pct"], 2)
        row["trades"] += m["trades"]
        row["months"] += 1
    years = tuple(by_year[y] for y in sorted(by_year))
    years_up = sum(1 for y in years if y["strategy_pct"] > y["holding_pct"])

    # How much of the money was at work, as a fraction: slot-time for an
    # account, the share of stock-months that traded for a basket.
    at_work = extra.get("slot_use_pct")
    if at_work is None:
        at_work = 100 * trading_months / stock_months if stock_months else 0.0
    active_excess = statistics.mean(strategy) - statistics.mean(holding) * at_work / 100

    return Basket(
        timeframe=timeframe,
        stocks=stocks,
        months=tuple(months),
        trades=trades,
        winning_trades=wins,
        avg_month_pct=round(statistics.mean(strategy), 4),
        median_month_pct=round(statistics.median(strategy), 4),
        months_positive_pct=round(100 * sum(1 for s in strategy if s > 0) / len(strategy), 2),
        best_month_pct=round(max(strategy), 4),
        worst_month_pct=round(min(strategy), 4),
        avg_holding_month_pct=round(statistics.mean(holding), 4),
        avg_excess_pct=round(statistics.mean(excess), 4),
        avg_excess_active_pct=round(active_excess, 4),
        worst_dip_pct=_dip_of(path),
        trades_per_month=round(trades / len(months), 2),
        sleeve_use_pct=round(100 * trading_months / stock_months, 2) if stock_months else 0.0,
        edge_t=edge_t,
        years=years,
        years_beating_holding_pct=round(100 * years_up / len(years), 2) if years else 0.0,
        **extra,
    )


def stock_sleeves(results: Sequence[ComboResult], *, timeframe: str) -> list[ComboResult]:
    """The tested STOCK combinations on one timeframe, with month data."""
    return [
        r for r in results
        if r.timeframe == timeframe and not r.is_index and r.skipped_reason is None
        and r.month_closes
    ]


def holding_by_month(sleeves: Sequence[ComboResult]) -> dict[str, tuple[float, int]]:
    """Equal-weight holding return per month, with how many stocks had data."""
    rows: dict[str, list[float]] = {}
    for sleeve in sleeves:
        for month, row in sleeve_months(sleeve).items():
            rows.setdefault(month, []).append(row.holding_pct)
    return {m: (sum(v) / len(v), len(v)) for m, v in rows.items()}


def build_basket(
    results: Sequence[ComboResult], *, timeframe: str, notional: float = NOTIONAL,
) -> Basket | None:
    """Equal-weight basket of every tested STOCK on `timeframe`. None if there is none."""
    sleeves = stock_sleeves(results, timeframe=timeframe)
    if not sleeves:
        return None

    by_month: dict[str, list[SleeveMonth]] = {}
    for sleeve in sleeves:
        for month, row in sleeve_months(sleeve, notional=notional).items():
            by_month.setdefault(month, []).append(row)

    months: list[dict[str, Any]] = []
    for month in sorted(by_month):
        rows = by_month[month]
        months.append({
            "month": month,
            "strategy_pct": round(sum(r.strategy_pct for r in rows) / len(rows), 4),
            "holding_pct": round(sum(r.holding_pct for r in rows) / len(rows), 4),
            "trades": sum(r.trades for r in rows),
            "wins": sum(r.wins for r in rows),
            "stocks": len(rows),
            "stocks_trading": sum(1 for r in rows if r.trades),
        })
    return summarise(
        timeframe, len(sleeves), months, notional=notional,
        stock_months=sum(m["stocks"] for m in months),
        trading_months=sum(m["stocks_trading"] for m in months),
    )


def restrict_to(result: ComboResult, from_utc: datetime) -> ComboResult:
    """The part of a combination's result from `from_utc` on.

    Trades are kept by ENTRY, matching walk_forward.split_trades: a position
    opened before the boundary belongs to the period whose data caused it.
    Months before the boundary's month are dropped; the boundary month keeps
    its first close, so holding is measured from where the period begins.
    """
    first_month = month_of(from_utc)
    return replace(
        result,
        trades=tuple(t for t in result.trades if t.entry_fill_ts >= from_utc),
        month_closes=tuple(m for m in result.month_closes if m[0] >= first_month),
    )


def daily_equity(
    results: Sequence[ComboResult],
    *,
    closes_by_symbol: Mapping[str, pd.Series],
    start_value: float = NOTIONAL,
    notional: float = NOTIONAL,
    trades: Sequence[SimTrade] | None = None,
    weight_of: Callable[[SimTrade], float] | None = None,
) -> list[dict[str, Any]]:
    """A Rs 1 lakh balance day by day, beside equal-weight holding.

    By default every sleeve's trades count, each moving the balance by
    net P&L / (notional x stocks in its exit month). An account replay passes
    its own `trades` and `weight_of` (net P&L / (notional x slots)).

    Within a calendar month the day's moves add up, so the days of a month
    still add to exactly that month's return; across months the balance
    compounds, so the last point matches `Basket.end_value` (to rounding) and
    it is the same path `summarise` measures the worst dip on. Floored at zero
    for the reason `equity_path` gives.

    Holding is the mean over stocks with a close that day of
    (close / their first close - 1), linear.

    Closed trades only: money still in a position is not marked. With two
    hundred sleeves that understates a dip less than it did for one stock,
    and the message says so.
    """
    sleeves = [r for r in results if r.skipped_reason is None and r.month_closes]
    if not sleeves:
        return []

    stocks_in_month: dict[str, int] = {}
    for r in sleeves:
        for month, _, _ in r.month_closes:
            stocks_in_month[month] = stocks_in_month.get(month, 0) + 1

    if trades is None:
        trades = [t for r in sleeves for t in r.trades]
    if weight_of is None:
        def weight_of(t: SimTrade) -> float:
            return t.net_pnl / (notional * stocks_in_month.get(month_of(t.exit_fill_ts), len(sleeves)))

    by_day: dict[Any, float] = {}
    for t in trades:
        day = t.exit_fill_ts.astimezone(IST).date()
        by_day[day] = by_day.get(day, 0.0) + weight_of(t)

    hold_rows: dict[Any, list[float]] = {}
    for symbol, closes in closes_by_symbol.items():
        if closes is None or len(closes) == 0:
            continue
        first = float(closes.iloc[0])
        if not first:
            continue
        for ts, close in closes.items():
            hold_rows.setdefault(ts.astimezone(IST).date(), []).append(float(close) / first - 1)

    days = sorted(set(by_day) | set(hold_rows))
    out: list[dict[str, Any]] = []
    opening = float(start_value)          # the balance this month began with
    running = 0.0                         # this month's return so far
    month = None
    for day in days:
        key = (day.year, day.month)
        if month is not None and key != month:
            opening = max(opening * (1 + running), 0.0)
            running = 0.0
        month = key
        running += by_day.get(day, 0.0)
        row: dict[str, Any] = {"day": day,
                               "lakh_balance": round(max(opening * (1 + running), 0.0), 2)}
        if day in hold_rows:
            row["hold_balance"] = round(start_value * (1 + float(np.mean(hold_rows[day]))), 2)
        out.append(row)
    return out


def dip_of_equity(rows: Sequence[Mapping[str, Any]]) -> float:
    """Worst dip of a daily lakh_balance path, in percent."""
    return _dip_of([float(r["lakh_balance"]) for r in rows])
