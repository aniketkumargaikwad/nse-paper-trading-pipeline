"""Index allocation: which NSE indexes to hold, how much, and when to sit in cash.

Why this exists (15 Nov 2026). The stock research tested its ideas on TODAY's
NIFTY 200 list, which already knows which companies grew - about 12% a year of
hindsight - and could only turn eight dials on one rule. NSE publishes
total-return histories for its own indexes, built from each date's REAL
constituents with dividends in, so a rule tested on them carries no stock-list
hindsight and its numbers can be read as they stand. Every index here is one a
fund or ETF can track, so a rule that passes is a rule that can be followed.

A PLAN picks from a universe of indexes on each decision day (month end or
week end): rank them, keep the strongest `hold`, drop any that fail its
filters, and leave the rest of the money in cash. Each kept index gets
1/`hold` of the account, so a plan whose filters reject half its picks is
half in cash - exposure falls by itself when trends break.

Point in time throughout: a decision reads only levels up to that session's
close and is filled at the NEXT session's close.

NO LUCKY DECISION DAY. The account is always run as equal PARTS that decide on
different days: four parts a week apart for a monthly plan, five parts on the
five weekdays for a weekly one (`PARTS`). On 15 Nov 2026 the first plan to pass
did so only when decided on the month's last session - 34.6% worst fall there,
35% to 49% on seven other days of the month, 1.41% to 1.63% a month. A result
that depends on the day of the month is luck, and one account cannot pick its
day in hindsight. Split into parts, the plan is the same on any calendar, and
the paper book trades exactly what was tested.

What is charged: `COST_PCT` of every rupee switched, and `FUND_FEE_PCT` a year
on invested money (an index has no costs; the fund tracking it has a fee and
falls a little short). Tax is NOT modelled.

Pure: no I/O.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from research.rotation import _months_back, month_ends, month_returns

BENCHMARK = "NIFTY 50"
CASH_YIELD = 0.06          # a year, on money not invested
COST_PCT = 0.2             # of every rupee bought or sold
FUND_FEE_PCT = 0.75        # a year on invested money: fund fee plus tracking shortfall
CAPITAL = 500_000.0
YEAR_SESSIONS = 252
# A decision that keeps the same indexes and moves exposure by less than this is not
# traded: nobody re-balances a few hundred rupees of drift every week.
MIN_EXPOSURE_MOVE = 0.10
# How many sessions after the period's last session each part of the account decides.
PARTS: dict[str, tuple[int, ...]] = {"monthly": (0, 5, 10, 15), "weekly": (0, 1, 2, 3, 4)}

# The indexes a plan may use: every one has NSE total-return levels from April 2005
# (niftyindices.com, scripts/nse_index_history.fetch_whole). Sector indexes are
# concentrated bets; before real money, check that a fund or ETF tracks the index chosen.
MENU: tuple[str, ...] = (
    # broad
    "NIFTY 50", "NIFTY NEXT 50", "NIFTY 200", "NIFTY 500", "NIFTY MIDCAP 150", "NIFTY SMALLCAP 250",
    "NIFTY MICROCAP 250",
    # factor
    "NIFTY200 MOMENTUM 30", "NIFTY MIDCAP150 MOMENTUM 50", "NIFTY500 MOMENTUM 50", "NIFTY200 ALPHA 30",
    "NIFTY ALPHA 50", "NIFTY200 QUALITY 30", "NIFTY MIDCAP150 QUALITY 50", "NIFTY SMALLCAP250 QUALITY 50",
    "NIFTY SMALLCAP250 MOMENTUM QUALITY 100", "NIFTY100 LOW VOLATILITY 30", "NIFTY ALPHA LOW-VOLATILITY 30",
    "NIFTY200 VALUE 30", "NIFTY500 VALUE 50",
    # sector
    "NIFTY BANK", "NIFTY PRIVATE BANK", "NIFTY PSU BANK", "NIFTY FINANCIAL SERVICES", "NIFTY IT",
    "NIFTY PHARMA", "NIFTY FMCG", "NIFTY AUTO", "NIFTY METAL", "NIFTY ENERGY",
    "NIFTY INFRASTRUCTURE", "NIFTY COMMODITIES", "NIFTY MNC",
)
# Left out on purpose: NIFTY50 VALUE 20, NIFTY DIVIDEND OPPORTUNITIES 50, NIFTY REALTY,
# NIFTY INDIA CONSUMPTION and NIFTY CPSE have no levels before 2006-2009. A plan built on
# them would sit in cash through the 2008 crash for want of history and look safe for it.

RANKS = ("return", "return_over_volatility", "none")
WEIGHTINGS = ("equal", "inverse_volatility")
DECIDES = ("monthly", "weekly")


@dataclass(frozen=True)
class Plan:
    name: str
    universe: tuple[str, ...]
    hold: int                              # how many indexes share the account
    rank_by: str = "return"                # "none" keeps the universe's own order
    lookbacks: tuple[int, ...] = (12,)     # months; the score is the average return over each
    skip_months: int = 0                   # most recent months left out of the ranking return
    weighting: str = "equal"
    own_average: int | None = None         # keep an index only at or above its own N-session average
    positive_months: int | None = None     # keep an index only if its own N-month return is above cash
    market_index: str | None = None        # everything to cash while this index ...
    market_average: int | None = None      # ... closes below its N-session average
    target_volatility: float | None = None  # % a year: scale exposure down when the picks swing more
    volatility_sessions: int = 60
    decide: str = "monthly"


@dataclass(frozen=True)
class Switch:
    decided: date
    filled: date
    weights: tuple[tuple[str, float], ...]   # index -> share of THIS PART; the rest of it is cash
    traded: float
    cost: float
    part: int = 0                            # which part of the account switched (0 = decides first)


@dataclass
class Result:
    plan: Plan
    capital: float
    equity: pd.Series
    exposure: pd.Series                      # share of the account invested, per session
    switches: list[Switch] = field(default_factory=list)
    holdings: dict[str, float] = field(default_factory=dict)     # index -> share of the whole account, at the end
    parts: int = 1                           # how many parts the account was run as


def week_ends(sessions: Sequence[date]) -> list[date]:
    """The last session of each COMPLETE week (the week still running is left out)."""
    last: dict[tuple[int, int], date] = {}
    for d in sessions:
        key = tuple(d.isocalendar()[:2])
        last[key] = max(d, last.get(key, d))
    if not last:
        return []
    final = max(sessions)
    return sorted(v for k, v in last.items() if k != tuple(final.isocalendar()[:2]))


# A decision reads a handful of levels out of twenty years, a thousand times a run, and a
# research day makes hundreds of runs. Sliced as pandas objects that took 12 seconds for
# one weekly plan; as plain arrays - (session ordinals, levels) per index - it is under 2.
Arrays = Mapping[str, tuple[np.ndarray, np.ndarray]]


def arrays(series: Mapping[str, pd.Series]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Each index as (the sessions as day numbers, the levels), both sorted by session."""
    return {k: (np.fromiter((d.toordinal() for d in v.index), dtype=np.int64, count=len(v)),
                v.to_numpy(dtype=float)) for k, v in series.items()}


def _upto(days: np.ndarray, day: date) -> int:
    """How many sessions are on or before `day`."""
    return int(np.searchsorted(days, day.toordinal(), side="right"))


def _swing(levels: np.ndarray) -> float:
    """Annualised volatility, in %, of the daily returns between these levels."""
    if len(levels) < 4:                       # fewer than three returns
        return float("nan")
    r = levels[1:] / levels[:-1] - 1
    return float(np.std(r, ddof=1) * YEAR_SESSIONS ** 0.5 * 100)


def target(series: Mapping[str, pd.Series], day: date, plan: Plan, *, cash_yield: float = CASH_YIELD) -> dict[str, float]:
    """The share of the account each index should hold, as known at `day`'s close. Empty = all cash."""
    return _target(arrays(series), day, plan, cash_yield)


def _target(data: Arrays, day: date, plan: Plan, cash_yield: float) -> dict[str, float]:
    today = day.toordinal()
    if plan.market_index and plan.market_average:
        days, levels = data[plan.market_index]
        n = _upto(days, day)
        if n < plan.market_average or levels[n - 1] < levels[n - plan.market_average:n].mean():
            return {}
    longest = max(plan.lookbacks)
    scores: dict[str, float] = {}
    vols: dict[str, float] = {}
    upto: dict[str, int] = {}
    for order, name in enumerate(plan.universe):
        days, levels = data[name]
        n = _upto(days, day)
        if n == 0 or days[n - 1] != today or days[0] > _months_back(day, longest).toordinal():
            continue                         # no level today, or not a full lookback of history
        if plan.own_average and (n < plan.own_average or levels[n - 1] < levels[n - plan.own_average:n].mean()):
            continue
        if plan.positive_months:
            since = _months_back(day, plan.positive_months)
            if days[0] > since.toordinal():
                continue
            hurdle = (1 + cash_yield) ** (plan.positive_months / 12) - 1
            if levels[n - 1] / levels[_upto(days, since) - 1] - 1 <= hurdle:
                continue
        vol = _swing(levels[max(0, n - (longest * 21 + 1)):n])
        if plan.rank_by == "none":
            score = -float(order)
        else:
            end = levels[_upto(days, _months_back(day, plan.skip_months)) - 1] if plan.skip_months else levels[n - 1]
            score = statistics.mean(float(end / levels[_upto(days, _months_back(day, lb)) - 1] - 1)
                                    for lb in plan.lookbacks)
            if plan.rank_by == "return_over_volatility":
                if not vol > 0:
                    continue
                score /= vol
        scores[name], vols[name], upto[name] = score, vol, n
    picked = sorted(scores, key=lambda k: (-scores[k], plan.universe.index(k)))[: plan.hold]
    if not picked:
        return {}
    filled = len(picked) / plan.hold         # picks the filters rejected leave their share in cash
    if plan.weighting == "inverse_volatility" and all(vols[k] > 0 for k in picked):
        inv = {k: 1 / vols[k] for k in picked}
        weights = {k: filled * v / sum(inv.values()) for k, v in inv.items()}
    else:
        weights = {k: 1 / plan.hold for k in picked}
    if plan.target_volatility:
        total = sum(weights.values())
        swing = _basket_swing(data, picked, upto, {k: w / total for k, w in weights.items()},
                              plan.volatility_sessions)
        if swing > 0:
            scale = min(1.0, plan.target_volatility / (swing * total))
            weights = {k: w * scale for k, w in weights.items()}
    return weights


def _basket_swing(data: Arrays, picked: Sequence[str], upto: Mapping[str, int], share: Mapping[str, float],
                  sessions: int) -> float:
    """Annualised volatility, in %, of the picks held in these shares over their last `sessions` returns."""
    tails = {k: (data[k][0][max(0, upto[k] - (sessions + 1)):upto[k]],
                 data[k][1][max(0, upto[k] - (sessions + 1)):upto[k]]) for k in picked}
    first = tails[picked[0]][0]
    if all(len(d) == len(first) and (d == first).all() for d, _ in tails.values()):
        if len(first) < 4:
            return float("nan")
        basket = sum(share[k] * (v[1:] / v[:-1] - 1) for k, (_, v) in tails.items())
        return float(np.std(basket, ddof=1) * YEAR_SESSIONS ** 0.5 * 100)
    # The picks do not share every session (one index missing a day): line them up by date.
    frame = pd.DataFrame({k: pd.Series(v, index=d) for k, (d, v) in tails.items()})
    basket = (frame.pct_change().dropna() * pd.Series(dict(share))).sum(axis=1)
    return float(basket.std() * YEAR_SESSIONS ** 0.5 * 100) if len(basket) > 2 else float("nan")


def run(levels: pd.DataFrame, plan: Plan, *, start: date, end: date, cost_pct: float = COST_PCT,
        fee_pct: float = FUND_FEE_PCT, cash_yield: float = CASH_YIELD, capital: float = CAPITAL,
        parts: Sequence[int] | None = None) -> Result:
    """Replay `plan` over the benchmark's sessions in [start, end]. `levels`: one column per index.

    The account is split equally into parts that decide on different days (`PARTS`; see the
    module docstring). `parts` overrides the offsets - (0,) is one account deciding on the
    period's last session, which is how a single lucky day is measured, not how a plan is judged.
    """
    levels = levels.sort_index()
    needed = sorted({*plan.universe, BENCHMARK, *([plan.market_index] if plan.market_index else [])})
    series = {k: levels[k].dropna() for k in needed}
    data = arrays(series)
    every = list(series[BENCHMARK].index)
    sessions = [d for d in every if start <= d <= end]
    before = [d for d in every if d < start][-1:]
    px = levels[needed].ffill()
    column = {k: i for i, k in enumerate(px.columns)}
    grid = px.loc[sessions].to_numpy(dtype=float)
    offsets = tuple(parts) if parts is not None else PARTS[plan.decide]
    ends = week_ends(sessions) if plan.decide == "weekly" else month_ends(sessions)
    place = {d: i for i, d in enumerate(sessions)}
    runs = []
    for number, offset in enumerate(offsets):
        days = {sessions[place[d] + offset] for d in ends if place[d] + offset < len(sessions)}
        runs.append(_part(data, grid, column, sessions, days, plan, before=before, cost_pct=cost_pct,
                          fee_pct=fee_pct, cash_yield=cash_yield, capital=capital / len(offsets), number=number))
    equity = sum(r[0] for r in runs)
    invested = sum(r[0] * r[1] for r in runs)
    switches = sorted((s for r in runs for s in r[2]), key=lambda s: (s.filled, s.part))
    total = float(equity.iloc[-1]) if len(equity) else 0.0
    holdings: dict[str, float] = {}
    for r in runs:
        for k, v in r[3].items():
            holdings[k] = holdings.get(k, 0.0) + (v / total if total else 0.0)
    return Result(plan, capital, equity, invested / equity, switches, holdings, len(offsets))


def _part(data: Arrays, grid: np.ndarray, column: Mapping[str, int], sessions: Sequence[date],
          decisions: set[date], plan: Plan, *, before: Sequence[date], cost_pct: float, fee_pct: float,
          cash_yield: float, capital: float,
          number: int) -> tuple[pd.Series, pd.Series, list[Switch], dict[str, float]]:
    """One part of the account: (equity, exposure, switches, rupees held per index at the end).

    `grid` is the forward-filled levels, one row per session, `column` each index's column."""
    rate = cost_pct / 100
    cash, held = capital, {}
    pending: tuple[date, dict[str, float]] | None = None
    equity, exposure, switches, prev = {}, {}, [], None
    wanted: dict[str, float] = {}
    # Start in the position the plan would already hold: decided on the session before
    # `start`, filled at the first session's close. Without it every run began with up to
    # a month in cash, which mattered in a one-year exam and in a new paper book.
    if before and sessions:
        wanted = _target(data, before[-1], plan, cash_yield)
        pending = (before[-1], wanted)
    for row, day in enumerate(sessions):
        if prev is not None:
            span = (day - prev).days / 365.25
            keep = (1 - fee_pct / 100) ** span
            held = {k: v * grid[row, column[k]] / grid[row - 1, column[k]] * keep for k, v in held.items()}
            cash *= (1 + cash_yield) ** span
        total = cash + sum(held.values())
        if pending is not None:
            decided, weights = pending
            now = sum(held.values()) / total if total else 0.0
            same = set(weights) == set(held) and abs(sum(weights.values()) - now) < MIN_EXPOSURE_MOVE
            if not same:
                traded = sum(abs(total * weights.get(k, 0.0) - held.get(k, 0.0)) for k in set(weights) | set(held))
                cost = rate * traded
                total -= cost
                held = {k: total * w for k, w in weights.items()}
                cash = total - sum(held.values())
                switches.append(Switch(decided, day, tuple(sorted(weights.items())), traded, cost, number))
            pending = None
        equity[day] = total
        exposure[day] = sum(held.values()) / total if total else 0.0
        if day in decisions:
            wanted = _target(data, day, plan, cash_yield)
            pending = (day, wanted)
        prev = day
    return pd.Series(equity, dtype=float), pd.Series(exposure, dtype=float), switches, held


def _alpha(m: pd.Series, b: pd.Series, rf: float) -> tuple[float, float | None, float]:
    """(alpha % a year, its t, beta) of monthly returns `m` against `b`, both over cash.

    Alpha is what is left after allowing for how much of the market's own move
    the account carried. A plain "beat the index" test punishes a rule for
    sitting in cash; this one does not.
    """
    y, x = (m - rf).to_numpy(float), (b - rf).to_numpy(float)
    n = len(y)
    if n < 12 or float(np.var(x)) == 0:
        return 0.0, None, 0.0
    beta = float(np.cov(x, y, ddof=1)[0, 1] / np.var(x, ddof=1))
    alpha = float(y.mean() - beta * x.mean())
    resid = y - alpha - beta * x
    s2 = float(resid @ resid) / (n - 2)
    se = math.sqrt(s2 * (1 / n + x.mean() ** 2 / float(((x - x.mean()) ** 2).sum())))
    return 100 * 12 * alpha, (alpha / se if se > 0 else None), beta


def stats(result: Result, benchmark: pd.Series, *, cash_yield: float = CASH_YIELD) -> dict:
    """The account's reading against holding `benchmark` over the same sessions."""
    eq = result.equity
    bench = benchmark.reindex(eq.index).ffill()
    bench = bench / bench.iloc[0] * result.capital
    m, b = month_returns(eq), month_returns(bench)
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)

    def by_year(s: pd.Series) -> pd.Series:
        ends = s.groupby(pd.Index([d.year for d in s.index])).last()
        return ends / ends.shift(1).fillna(s.iloc[0]) - 1

    def fall(s: pd.Series) -> float:
        return round(100 * (1 - (s / s.cummax()).min()), 1)

    yearly, byearly = by_year(eq), by_year(bench)
    rf = (1 + cash_yield) ** (1 / 12) - 1
    alpha, alpha_t, beta = _alpha(m, b, rf)
    half = len(m) // 2
    first, second = _alpha(m.iloc[:half], b.iloc[:half], rf)[0], _alpha(m.iloc[half:], b.iloc[half:], rf)[0]
    cagr = lambda s: 100 * ((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1)       # noqa: E731
    return {
        "plan": result.plan.name, "start": eq.index[0], "end": eq.index[-1],
        "end_value": round(float(eq.iloc[-1]), 0),
        "cagr_pct": round(cagr(eq), 2), "bench_cagr_pct": round(cagr(bench), 2),
        "avg_month_pct": round(100 * m.mean(), 2), "months_up_pct": round(100 * (m > 0).mean(), 1),
        "worst_month_pct": round(100 * m.min(), 2),
        "worst_fall_pct": fall(eq), "bench_worst_fall_pct": fall(bench),
        "worst_year_pct": round(100 * yearly.min(), 1),
        "years_beating_pct": round(100 * (yearly > byearly.reindex(yearly.index)).mean(), 0),
        "alpha_pct": round(alpha, 2), "alpha_t": None if alpha_t is None else round(alpha_t, 2),
        "beta": round(beta, 2),
        "alpha_first_half_pct": round(first, 2), "alpha_second_half_pct": round(second, 2),
        "invested_pct": round(100 * float(result.exposure.mean()), 0),
        "switches_per_year": round(len(result.switches) / years / max(result.parts, 1), 1),   # per part
        "costs": round(sum(s.cost for s in result.switches), 0),
        "yearly": {int(y): round(100 * v, 1) for y, v in yearly.items()},
        "bench_yearly": {int(y): round(100 * v, 1) for y, v in byearly.items()},
    }


class PlanError(ValueError):
    """A plan document that cannot be tested. The message goes back to whoever wrote it."""


def _whole(doc: Mapping, key: str, lo: int, hi: int, default: int | None = None, where: str = "") -> int | None:
    value = doc.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise PlanError(f"{where}{key} must be a whole number from {lo} to {hi}")
    return value


def _only(doc: Mapping, allowed: Sequence[str], where: str) -> None:
    extra = sorted(str(k) for k in set(doc) - set(allowed))
    if extra:
        raise PlanError(f"unknown key(s) in {where}: {', '.join(extra)} (allowed: {', '.join(allowed)})")


def _section(doc: Mapping, key: str) -> Mapping:
    value = doc.get(key) or {}
    if not isinstance(value, Mapping):
        raise PlanError(f"{key} must be a mapping")
    return value


VERSION = "alloc/1"


def parse(doc: Mapping, *, name: str, menu: Sequence[str] = MENU) -> Plan:
    """A plan document (the alloc/1 format, research/alloc_day.FORMAT) as a Plan, or PlanError."""
    if not isinstance(doc, Mapping):
        raise PlanError("the plan must be ONE YAML mapping in the alloc/1 format")
    _only(doc, ("version", "name", "universe", "hold", "rank", "weighting", "filters", "market_filter",
                "volatility_target", "decide"), "the plan")
    if doc.get("version") != VERSION:
        raise PlanError(f"version must be {VERSION}")
    known = {m.upper(): m for m in menu}
    raw = doc.get("universe")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 12:
        raise PlanError("universe must be a list of 1 to 12 index names from the menu")
    universe = []
    for item in raw:
        index = known.get(" ".join(str(item).upper().split()))
        if index is None:
            raise PlanError(f"'{item}' is not on the menu; use an exact name from it")
        if index in universe:
            raise PlanError(f"'{index}' is listed twice in universe")
        universe.append(index)
    hold = _whole(doc, "hold", 1, len(universe), 1)
    rank = _section(doc, "rank")
    _only(rank, ("by", "lookback_months", "skip_months"), "rank")
    by = rank.get("by", "return")
    if by not in RANKS:
        raise PlanError(f"rank.by must be one of {', '.join(RANKS)}")
    looks = rank.get("lookback_months", [12])
    looks = [looks] if isinstance(looks, int) and not isinstance(looks, bool) else looks
    if (not isinstance(looks, list) or not 1 <= len(looks) <= 3
            or any(not isinstance(x, int) or isinstance(x, bool) or not 1 <= x <= 12 for x in looks)):
        raise PlanError("rank.lookback_months must be one to three whole numbers from 1 to 12")
    skip = _whole(rank, "skip_months", 0, 2, 0, "rank.")
    if skip >= min(looks):
        raise PlanError("rank.skip_months must be less than the shortest lookback")
    weighting = doc.get("weighting", "equal")
    if weighting not in WEIGHTINGS:
        raise PlanError(f"weighting must be one of {', '.join(WEIGHTINGS)}")
    filters = _section(doc, "filters")
    _only(filters, ("above_own_average", "beats_cash_months"), "filters")
    market = _section(doc, "market_filter")
    _only(market, ("index", "average"), "market_filter")
    market_index = None
    if market:
        market_index = known.get(" ".join(str(market.get("index", "")).upper().split()))
        if market_index is None:
            raise PlanError("market_filter.index must be an index from the menu")
    swing = _section(doc, "volatility_target")
    _only(swing, ("percent", "sessions"), "volatility_target")
    percent = swing.get("percent")
    if swing and (not isinstance(percent, (int, float)) or isinstance(percent, bool) or not 8 <= percent <= 30):
        raise PlanError("volatility_target.percent must be a number from 8 to 30")
    decide = doc.get("decide", "monthly")
    if decide not in DECIDES:
        raise PlanError(f"decide must be one of {', '.join(DECIDES)}")
    return Plan(
        name=name, universe=tuple(universe), hold=hold, rank_by=by, lookbacks=tuple(looks), skip_months=skip,
        weighting=weighting,
        own_average=_whole(filters, "above_own_average", 50, 250, where="filters."),
        positive_months=_whole(filters, "beats_cash_months", 3, 12, where="filters."),
        market_index=market_index,
        market_average=_whole(market, "average", 50, 250, 200, "market_filter.") if market else None,
        target_volatility=float(percent) if swing else None,
        volatility_sessions=_whole(swing, "sessions", 20, 120, 60, "volatility_target.") if swing else 60,
        decide=decide)
