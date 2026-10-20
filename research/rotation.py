"""Momentum rotation: rank every stock, hold the strongest, rebalance monthly.

Design: docs/superpowers/specs/2026-10-20-momentum-rotation-design.md. The
research loop cannot express this - its rules read their own stock only - so
it is measured here, on adjusted daily closes (a frame: one column per
symbol, `^NSEI` for NIFTY 50, rows are sessions).

Point in time throughout: the ranking on a month's last session reads only
closes up to that session, and the trades fill at the NEXT session's close.

Pure: no I/O.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

import pandas as pd

INDEX = "^NSEI"
TREND_SESSIONS = 200
CAPITAL = 500_000.0


@dataclass(frozen=True)
class Variant:
    name: str
    lookback: int          # months
    skip: int              # most recent months left out of the return
    top: int               # stocks held
    trend: bool            # cash while NIFTY 50 is below its trend average
    # The research AI's choices (20 Oct 2026); the defaults are the plain rule.
    score: str = "return"            # or "return_over_volatility": return / daily-return stdev
    above_sma: int | None = None     # only stocks closing above their own N-session average
    max_volatility: float | None = None   # only stocks below this annualised volatility, %
    weighting: str = "equal"         # or "inverse_volatility"
    trend_sessions: int = 200        # the NIFTY 50 average the trend filter reads


@dataclass(frozen=True)
class Rebalance:
    decided: date
    filled: date
    holdings: tuple[str, ...]
    traded: float          # rupees bought plus sold
    cost: float


@dataclass
class Result:
    variant: Variant
    capital: float
    equity: pd.Series
    rebalances: list[Rebalance] = field(default_factory=list)


def month_ends(sessions: Sequence[date]) -> list[date]:
    """The last session of each COMPLETE month (the month still running is left out)."""
    last: dict[tuple[int, int], date] = {}
    for d in sessions:
        last[(d.year, d.month)] = max(d, last.get((d.year, d.month), d))
    if not last:
        return []
    final = max(sessions)
    return sorted(v for k, v in last.items() if k != (final.year, final.month))


def _months_back(day: date, months: int) -> date:
    y, m = divmod(day.year * 12 + day.month - 1 - months, 12)
    m += 1
    return date(y, m, min(day.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]))


SCORES = ("return", "return_over_volatility")
WEIGHTINGS = ("equal", "inverse_volatility")
YEAR_SESSIONS = 252


def _volatility(s: pd.Series, since: date) -> float:
    """Annualised volatility of daily returns since `since`, in %."""
    r = s.loc[since:].pct_change().dropna()
    return float(r.std() * YEAR_SESSIONS ** 0.5 * 100) if len(r) > 2 else float("nan")


def ranking(closes: pd.DataFrame, day: date, variant: Variant) -> list[str]:
    """The strongest stocks by the variant's score over the lookback, as known at `day`'s close."""
    return [s for s, _ in scored(closes, day, variant)]


def scored(closes: pd.DataFrame, day: date, variant: Variant) -> list[tuple[str, float]]:
    """(symbol, volatility %) of the stocks the variant holds, strongest first."""
    known = closes.loc[closes.index <= day]
    since, until = _months_back(day, variant.lookback), _months_back(day, variant.skip)
    scores: dict[str, float] = {}
    vols: dict[str, float] = {}
    for symbol in known.columns:
        if symbol.startswith("^"):
            continue
        s = known[symbol].dropna()
        if s.empty or s.index[0] > since or s.index[-1] < day:
            continue                    # not listed a full lookback ago, or no price today
        start, end = s.loc[:since].iloc[-1], s.loc[:until].iloc[-1]
        if start <= 0:
            continue
        if variant.above_sma and (len(s) < variant.above_sma
                                  or s.iloc[-1] < s.iloc[-variant.above_sma:].mean()):
            continue
        vol = _volatility(s, since)
        if variant.max_volatility is not None and not vol <= variant.max_volatility:
            continue
        ret = end / start - 1
        if variant.score == "return_over_volatility":
            if not vol > 0:
                continue
            ret = ret / vol
        scores[symbol], vols[symbol] = ret, vol
    best = sorted(scores, key=lambda k: (-scores[k], k))[: variant.top]
    return [(k, vols[k]) for k in best]


def _weights(target: list[tuple[str, float]], variant: Variant) -> dict[str, float]:
    if not target:
        return {}
    if variant.weighting == "inverse_volatility" and all(v > 0 for _, v in target):
        inv = {s: 1 / v for s, v in target}
        total = sum(inv.values())
        return {s: w / total for s, w in inv.items()}
    return {s: 1 / len(target) for s, _ in target}


def run(closes: pd.DataFrame, variant: Variant, *, start: date, end: date, cost_pct: float,
        capital: float = CAPITAL) -> Result:
    closes = closes.sort_index()
    prices = closes.ffill()
    sessions = [d for d in closes.index if start <= d <= end]
    # On the index's OWN sessions: the frame is a union of every symbol's days, and a
    # missing index day inside the window would otherwise blank the average (20 Oct 2026).
    index = closes[INDEX].dropna()
    trend = index.rolling(variant.trend_sessions).mean()
    decisions = set(month_ends(sessions))
    rate = cost_pct / 100
    cash, shares = capital, {}
    pending: tuple[date, list[str]] | None = None
    equity, rebalances = {}, []
    for day in sessions:
        px = prices.loc[day]
        if pending is not None:
            decided, target = pending
            value = cash + sum(n * px[s] for s, n in shares.items())
            weights = _weights(target, variant)
            want = {s: value * weights[s] / px[s] for s in weights}
            traded = sum(abs(want.get(s, 0.0) - shares.get(s, 0.0)) * px[s] for s in set(want) | set(shares))
            cost = rate * traded
            shares = {s: n * (1 - cost / value) for s, n in want.items()} if want else {}
            cash = 0.0 if want else value - cost
            rebalances.append(Rebalance(decided, day, tuple(sym for sym, _ in target), traded, cost))
            pending = None
        equity[day] = cash + sum(n * px[s] for s, n in shares.items())
        if day in decisions:
            seen = index.loc[:day]
            level = trend.loc[:day]
            calm = not variant.trend or (len(seen) > 0 and not math.isnan(level.iloc[-1])
                                         and seen.iloc[-1] >= level.iloc[-1])
            pending = (day, scored(closes, day, variant) if calm else [])
    return Result(variant, capital, pd.Series(equity, dtype=float), rebalances)


def month_returns(equity: pd.Series) -> pd.Series:
    ends = equity.groupby(pd.Index([f"{d.year}-{d.month:02d}" for d in equity.index])).last()
    first = equity.iloc[0]
    return ends / ends.shift(1).fillna(first) - 1


def stats(result: Result, benchmark: pd.Series) -> dict:
    """The account's reading against holding `benchmark` (a close series) over the same sessions."""
    eq = result.equity
    bench = benchmark.reindex(eq.index).ffill()
    bench = bench / bench.iloc[0] * result.capital
    m, b = month_returns(eq), month_returns(bench)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    def by_year(s: pd.Series) -> pd.Series:
        ends = s.groupby(pd.Index([d.year for d in s.index])).last()
        return ends / ends.shift(1).fillna(s.iloc[0]) - 1
    yearly, byearly = by_year(eq), by_year(bench)
    excess = (m - b).dropna()
    t = (statistics.mean(excess) / (statistics.stdev(excess) / math.sqrt(len(excess)))
         if len(excess) > 2 and statistics.stdev(excess) > 0 else None)
    daily = eq.pct_change().dropna()
    held = [r for r in result.rebalances if r.holdings]
    swaps = sum(len(set(r.holdings) ^ set(p.holdings)) for p, r in zip(result.rebalances, result.rebalances[1:]))
    return {
        "variant": result.variant.name, "start": eq.index[0], "end": eq.index[-1],
        "end_value": round(float(eq.iloc[-1]), 0),
        "cagr_pct": round(100 * ((eq.iloc[-1] / eq.iloc[0]) ** (1 / years) - 1), 2),
        "bench_cagr_pct": round(100 * ((bench.iloc[-1] / bench.iloc[0]) ** (1 / years) - 1), 2),
        "avg_month_pct": round(100 * m.mean(), 2), "months_up_pct": round(100 * (m > 0).mean(), 1),
        "worst_month_pct": round(100 * m.min(), 2),
        "worst_fall_pct": round(100 * (1 - (eq / eq.cummax()).min()), 1),
        "bench_worst_fall_pct": round(100 * (1 - (bench / bench.cummax()).min()), 1),
        "worst_day_pct": round(100 * daily.min(), 2),
        "years_beating_pct": round(100 * (yearly > byearly.reindex(yearly.index)).mean(), 0),
        "luck_t": None if t is None else round(t, 2),
        "months_in_cash_pct": round(100 * (1 - len(held) / max(len(result.rebalances), 1)), 0),
        "trades_per_month": round((swaps + len(held[:1]) * result.variant.top) / max(len(result.rebalances), 1), 1),
        "costs": round(sum(r.cost for r in result.rebalances), 0),
        "yearly": {int(y): round(100 * v, 1) for y, v in yearly.items()},
        "bench_yearly": {int(y): round(100 * v, 1) for y, v in byearly.items()},
    }


def index_switch(index: pd.Series, signal: pd.Series, *, start: date, end: date, cash_yield: float,
                 cost_pct: float, capital: float = CAPITAL) -> Result:
    """Hold `index` while `signal` closes at or above its 200-session average, cash otherwise.

    Decided on each month's last session, switched at the next session's close
    (paying `cost_pct` of the account); cash earns `cash_yield` a year. Both
    series are close levels; days are the sessions both have.
    """
    days = sorted(set(index.dropna().index) & set(signal.dropna().index))
    sig = signal.reindex(days)
    average = sig.rolling(TREND_SESSIONS, min_periods=1).mean()
    inside = [d for d in days if start <= d <= end]
    decisions = set(month_ends(inside)) | ({inside[-1]} if inside else set())
    equity, rebalances, invested, pending, value, prev = {}, [], False, None, capital, None
    for d in inside:
        if prev is not None:
            if invested:
                value *= index[d] / index[prev]
            else:
                value *= (1 + cash_yield) ** ((d - prev).days / 365.25)
        if pending is not None:
            if pending != invested:
                cost = cost_pct / 100 * value
                value -= cost
                rebalances.append(Rebalance(prev, d, ("INDEX",) if pending else (), value, cost))
            invested, pending = pending, None
        equity[d] = value
        if d in decisions:
            pending = bool(sig[d] >= average[d])
        prev = d
    variant = Variant("index + trend", 0, 0, 1, True)
    return Result(variant, capital, pd.Series(equity, dtype=float), rebalances)
