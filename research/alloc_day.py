"""The stock research day, rebuilt on NSE's own index histories (15 Nov 2026).

    python -m research.alloc_day
    python -m research.alloc_day --dry-run --no-save --skip-download
    python -m research.alloc_day --max-versions 5 --commit-journal --notify

What was wrong before. The momentum-rotation day (research/rotation_day.py)
gave the AI eight dials on one rule and tested it on TODAY's NIFTY 200 - a
list that already knows which companies grew - so every number carried about
12% a year of hindsight and a version could only be judged against another
version. After 19 versions the AI was changing the number of stocks held. And
a version that passed went nowhere: nothing started paper-trading it.

What this day does instead.
- The AI designs an INDEX ALLOCATION plan (research/alloc.py): which NSE
  indexes to hold, how many, under which trend, cash and volatility rules,
  decided monthly or weekly. A far wider toolbox than eight dials.
- It is tested on NSE's total-return index histories from April 2006 - real
  constituents on every date, so no stock-list hindsight, and 2008 is inside
  the test. Switching costs and a yearly fund fee are charged. The numbers can
  be read as they stand.
- The pass rule is the owner's own target and limit (PASS RULE below), plus
  four checks that a result is not luck or fitting.
- A plan that passes, and does not fail its exam year, is PAPER-TRADED
  AUTOMATICALLY from the next session (research/paper/promoted.json, picked up
  by research/paper_books.py).
- Every message ends with the closest plans found so far, so a day on which
  nothing passes still says how far away a pass is.

The loop, the Claude call and the final choice are the existing ones
(research.loop, research.brain, research.run_day). Records live in
research/journal/alloc/; the exam file is never read back for the AI.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from config import IST, use_utf8_stdout
from research.alloc import (BENCHMARK, CASH_YIELD, COST_PCT, FUND_FEE_PCT, MENU, VERSION, Plan, PlanError,
                            parse, run, stats)
from research.portfolio import PASS_MAX_FALL_PCT, PASS_T
from research.run_day import OpusBrain, choose_final, nothing_to_commit
from research.segment import TARGET_MONTHLY_MAX, TARGET_MONTHLY_MIN

LEVELS = Path("data/rotation/nse_menu_tri.parquet")
DIR = Path("research/journal/alloc")
PROMOTED = Path("research/paper/promoted.json")
LEDGER, EXAMS = "ledger.jsonl", "exams.jsonl"
# The first day on which EVERY setting the format allows can be computed: NSE's factor
# indexes begin on 1 Apr 2005, the longest look-back is 12 months and the longest average
# 250 sessions. A week earlier (3 Apr 2006, the first choice) a 12-month look-back was one
# day short, so a plan sat in cash for its first month and met the May 2006 crash by accident.
TRAIN_FROM = date(2006, 4, 10)
HISTORY_FROM = date(2005, 4, 1)     # an index is offered only with levels from here
EXAM_DAYS = 365
BUDGET_SECONDS = 4 * 60 * 60

# --- THE PASS RULE -------------------------------------------------------------------
# The first two are the owner's own numbers: his monthly target and his fall limit.
MIN_MONTH_PCT = TARGET_MONTHLY_MIN          # the average month, after costs and the fund fee
MAX_FALL_PCT = PASS_MAX_FALL_PCT            # worst fall from a high, 2006 on - so 2008 counts
MIN_YEARS_BEATING_PCT = 50.0
# The luck test is the t of ALPHA - the return left after allowing for how much of
# NIFTY's own move the plan carried - so sitting in cash is not held against a plan.
# 2.6 means luck alone would do this well less than 1 time in 200. It is above the 1.65
# used for a single attempt (research.portfolio.PASS_T) because this track makes many
# attempts on the same history, and the best of many needs a higher bar.
MIN_T = 2.6
# Fitting test: nudge each setting by about a fifth. A real effect survives that; a
# setting tuned to dodge one bad month does not.
NEIGHBOUR_T = PASS_T
NEIGHBOUR_FALL_PCT = MAX_FALL_PCT + 5.0
NEIGHBOURS_HOLD_PCT = 80.0
# The exam year (the last 365 days, never shown to the AI) can veto a pass - the same
# 5 points the paper books' six-month review uses.
EXAM_BEHIND_PTS = 5.0
MAX_PROMOTED = 6                            # paper books this track may have running at once

FORMAT = """\
version: alloc/1
universe:                    # 1-12 indexes, exact names from the menu
  - NIFTY MIDCAP150 MOMENTUM 50
  - NIFTY200 MOMENTUM 30
hold: 1                      # 1 to the universe's size. Each index kept gets 1/hold of the account
rank:                        # how the universe is ordered on each decision day
  by: return                 # return | return_over_volatility | none (none = the order listed)
  lookback_months: [6, 12]   # one to three whole numbers 1-12; the score averages their returns
  skip_months: 0             # 0-2, less than the shortest lookback
weighting: equal             # equal | inverse_volatility
filters:                     # optional; an index that fails one leaves its share in cash
  above_own_average: 200     # 50-250: only an index at or above its own N-session average
  beats_cash_months: 12      # 3-12: only an index whose own N-month return beat cash
market_filter:               # optional: the whole account to cash while this index is below its average
  index: NIFTY 50            # any menu index
  average: 200               # 50-250 sessions
volatility_target:           # optional: invest less when the picks swing more than this
  percent: 15                # 8-30, % a year
  sessions: 60               # 20-120: the window the swing is measured over
decide: monthly              # monthly | weekly. The account always runs as equal parts deciding on
                             # different days (monthly: four parts a week apart; weekly: one per weekday)
"""

_MM, _M30 = "NIFTY MIDCAP150 MOMENTUM 50", "NIFTY200 MOMENTUM 30"
_FACTORS = (_MM, _M30, "NIFTY500 MOMENTUM 50", "NIFTY200 ALPHA 30", "NIFTY200 QUALITY 30",
            "NIFTY100 LOW VOLATILITY 30", "NIFTY200 VALUE 30", "NIFTY SMALLCAP250 MOMENTUM QUALITY 100")

# Already paper-traded as index books (research/paper_books.py, research/momentum_paper.py).
# A new plan must not be worse than one of these on BOTH return and fall.
INCUMBENTS: tuple[Plan, ...] = (
    Plan("Midcap150 Momentum 50 + the monthly NIFTY cash rule (paper book)", (_MM,), 1, rank_by="none",
         market_index=BENCHMARK, market_average=200),
    Plan("Momentum 30 + the monthly NIFTY cash rule (paper book)", (_M30,), 1, rank_by="none",
         market_index=BENCHMARK, market_average=200),
)

# Measured by hand on 15 Nov 2026 while this track was built, and re-measured every day
# so the AI starts from the known landscape instead of rediscovering it. They count as
# attempts already made on this history.
REFERENCE: tuple[Plan, ...] = (
    Plan("Hold NIFTY 50", (BENCHMARK,), 1, rank_by="none"),
    Plan("Hold Midcap150 Momentum 50", (_MM,), 1, rank_by="none"),
    *INCUMBENTS,
    Plan("Midcap150 Momentum 50 + the NIFTY cash rule, weekly", (_MM,), 1, rank_by="none",
         market_index=BENCHMARK, market_average=200, decide="weekly"),
    Plan("Midcap150 Momentum 50 above its own 200-day average, weekly", (_MM,), 1, rank_by="none",
         own_average=200, decide="weekly"),
    Plan("Midcap150 Momentum 50 above its own 200-day average, monthly", (_MM,), 1, rank_by="none",
         own_average=200),
    Plan("Midcap150 Momentum 50, volatility target 18%", (_MM,), 1, rank_by="none", target_volatility=18.0),
    Plan("Midcap150 Momentum 50, volatility target 15%", (_MM,), 1, rank_by="none", target_volatility=15.0),
    Plan("Midcap150 Momentum 50, volatility target 15% + own 200-day average, weekly", (_MM,), 1,
         rank_by="none", target_volatility=15.0, own_average=200, decide="weekly"),
    Plan("Top 2 of 8 factor indexes by 6+12 month return, above own 200-day average", _FACTORS, 2,
         lookbacks=(6, 12), own_average=200),
    Plan("Top 3 of 8 factor indexes by 12 month return, each beating cash, weekly", _FACTORS, 3,
         positive_months=12, decide="weekly"),
)


@dataclass(frozen=True)
class CheckedPlan:
    document: dict[str, Any]
    plan: Plan


def research_name(day: date, idea: str, version: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:40] or "idea"
    return f"A-{day:%Y%m%d}-{slug}-v{version}"


def check_proposal(text: str, *, idea: str, day: date, version: int, menu: Sequence[str] = MENU) -> CheckedPlan:
    """The AI's YAML as a tested-able plan; PlanError (sent back to the AI) otherwise."""
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise PlanError(f"not valid YAML: {exc}") from exc
    name = research_name(day, idea, version)
    plan = parse(doc, name=name, menu=menu)
    return CheckedPlan({**doc, "name": name}, plan)


def same_plan(plan: Plan) -> Plan:
    """The plan without its name: two proposals are the same plan when these are equal."""
    return dataclasses.replace(plan, name="")


def tested_before(ledger: Sequence[dict], menu: Sequence[str] = MENU) -> dict[Plan, str]:
    """Every plan already measured - the reference plans and the ledger's - to its title."""
    seen = {same_plan(p): p.name for p in REFERENCE}
    for r in ledger:
        if r.get("valid") and r.get("strategy"):
            try:
                seen.setdefault(same_plan(parse({k: v for k, v in r["strategy"].items() if k != "name"},
                                                name="", menu=menu)), r.get("title") or r["strategy"].get("name", ""))
            except PlanError:
                continue
    return seen


def windows(levels: pd.DataFrame) -> tuple[tuple[date, date], tuple[date, date]]:
    """(training, exam): the exam is the last 365 days of sessions, training everything before."""
    sessions = sorted(levels[BENCHMARK].dropna().index)
    cut = sessions[-1] - timedelta(days=EXAM_DAYS)
    train_end = max(d for d in sessions if d <= cut)
    exam = [d for d in sessions if d > train_end]
    return (TRAIN_FROM, train_end), (exam[0], exam[-1])


def neighbours(plan: Plan) -> list[Plan]:
    """The same plan with each numeric setting moved about a fifth either way."""
    out: list[Plan] = []

    def vary(**change: Any) -> None:
        other = dataclasses.replace(plan, **change)
        if other != plan and other not in out:
            out.append(other)

    for f in (0.8, 1.2):
        if plan.own_average:
            vary(own_average=round(plan.own_average * f))
        if plan.market_average:
            vary(market_average=round(plan.market_average * f))
    if plan.target_volatility:
        for f in (0.85, 1.15):
            vary(target_volatility=round(plan.target_volatility * f, 1))
        for f in (0.75, 1.25):
            vary(volatility_sessions=round(plan.volatility_sessions * f))
    if plan.positive_months:
        for step in (-2, 2):
            vary(positive_months=min(12, max(1, plan.positive_months + step)))
    if plan.rank_by != "none":
        for step in (-1, 1):
            looks = tuple(min(13, max(plan.skip_months + 1, lb + step)) for lb in plan.lookbacks)
            vary(lookbacks=looks)
    if len(plan.universe) > 1:
        for step in (-1, 1):
            if 1 <= plan.hold + step <= len(plan.universe):
                vary(hold=plan.hold + step)
    return out


@dataclass
class Check:
    name: str
    ok: bool
    detail: str          # why it fails, in words; read only when not ok

    def __post_init__(self) -> None:
        # A comparison of numpy numbers is numpy's bool, which json.dumps(default=str)
        # stores as the STRING "False" - and that reads back as true.
        self.ok = bool(self.ok)


@dataclass
class AllocSummary:
    reading: dict[str, Any]
    checks: list[Check]
    nearby: dict[str, int] | None      # {"tested": n, "held_up": k}; None when an earlier check failed

    @property
    def passes(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def why_not(self) -> str | None:
        return next((c.detail for c in self.checks if not c.ok), None)

    @property
    def sound(self) -> bool:
        return near_sound(self.reading) and (self.nearby is None or next(c.ok for c in self.checks if c.name == "nearby"))

    def as_dict(self) -> dict[str, Any]:
        return {"passes": self.passes, "why_not": self.why_not, "plan": self.reading,
                "checks": {c.name: c.ok for c in self.checks},
                "failed": [c.detail for c in self.checks if not c.ok], "nearby": self.nearby}


def near_sound(reading: dict) -> bool:
    """Good enough to show as "closest so far": an edge in both halves that is probably not luck.

    Looser than a pass on purpose (t at the single-attempt bar, not MIN_T): the board is
    there to show how far away a pass is, from both sides, not to announce one.
    """
    t = reading.get("alpha_t")
    return bool(t is not None and t >= NEIGHBOUR_T and reading["years_beating_pct"] >= MIN_YEARS_BEATING_PCT
                and reading["alpha_first_half_pct"] > 0 and reading["alpha_second_half_pct"] > 0)


def judge(reading: dict, incumbents: Sequence[dict]) -> list[Check]:
    """Every check except the neighbours, in the order a failure is reported."""
    t = reading["alpha_t"]
    worse_than = next((i for i in incumbents if i["cagr_pct"] >= reading["cagr_pct"]
                       and i["worst_fall_pct"] <= reading["worst_fall_pct"]), None)
    return [
        Check("return", reading["avg_month_pct"] >= MIN_MONTH_PCT,
              f"averaged {reading['avg_month_pct']:.2f}% a month (the target is {MIN_MONTH_PCT:g}%)"),
        Check("fall", reading["worst_fall_pct"] <= MAX_FALL_PCT,
              f"fell {reading['worst_fall_pct']:.1f}% from a high (limit {MAX_FALL_PCT:.0f}%)"),
        Check("years", reading["years_beating_pct"] >= MIN_YEARS_BEATING_PCT,
              f"beat NIFTY 50 in only {reading['years_beating_pct']:.0f}% of years"),
        Check("luck", t is not None and t >= MIN_T, f"its edge could be luck: t = {t} (needs {MIN_T:g})"),
        Check("halves", reading["alpha_first_half_pct"] > 0 and reading["alpha_second_half_pct"] > 0,
              f"its edge was missing in one half of the history ({reading['alpha_first_half_pct']:+.1f}% "
              f"a year in the first, {reading['alpha_second_half_pct']:+.1f}% in the second)"),
        Check("better", worse_than is None,
              "no better than a plan already paper-traded" if worse_than is None else
              f"no better than '{worse_than['plan']}' ({worse_than['cagr_pct']:.1f}% a year, "
              f"worst fall {worse_than['worst_fall_pct']:.1f}%)"),
    ]


def score(summary: AllocSummary) -> tuple[bool, int, float]:
    return summary.passes, sum(c.ok for c in summary.checks), summary.reading["alpha_t"] or -99.0


def one_line(summary: AllocSummary | dict | None) -> str:
    if summary is None:
        return "not tested"
    d = summary.as_dict() if isinstance(summary, AllocSummary) else summary
    s = d["plan"]
    verdict = "PASSES" if d["passes"] else f"fails: {d['why_not']}"
    return (f"{s['avg_month_pct']:.2f}%/month ({s['cagr_pct']:.1f}%/yr; NIFTY 50 {s['bench_cagr_pct']:.1f}%), "
            f"worst fall {s['worst_fall_pct']:.1f}%, luck t {s['alpha_t']}, invested {s['invested_pct']:.0f}% "
            f"of the time - {verdict}")


class Lab:
    def __init__(self, levels: pd.DataFrame, promoted: Sequence[dict] = ()) -> None:
        self.levels = levels
        self.menu = tuple(m for m in MENU if m in levels.columns and levels[m].dropna().index[0] <= HISTORY_FROM)
        if BENCHMARK not in self.menu:
            raise RuntimeError(f"{BENCHMARK} is missing from the index levels")
        self.train, self.exam = windows(levels)
        self._reference: list[dict] | None = None
        plans = list(INCUMBENTS)
        for row in promoted:
            try:
                plans.append(parse(row["document"], name=row["title"], menu=self.menu))
            except (PlanError, KeyError):
                continue
        self._incumbent_plans = plans
        self._incumbents: list[dict] | None = None

    def reading(self, plan: Plan, window: tuple[date, date] | None = None) -> dict[str, Any]:
        a, b = window or self.train
        return stats(run(self.levels, plan, start=a, end=b), self.levels[BENCHMARK])

    def incumbents(self) -> list[dict]:
        if self._incumbents is None:
            self._incumbents = [self.reading(p) for p in self._incumbent_plans if set(p.universe) <= set(self.menu)]
        return self._incumbents

    def reference(self) -> list[dict]:
        """The known landscape: each reference plan's training reading and whether it would pass."""
        if self._reference is None:
            rows = []
            for plan in REFERENCE:
                if not set(plan.universe) <= set(self.menu):
                    continue
                r = self.reading(plan)
                checks = judge(r, [i for i in self.incumbents() if i["plan"] != plan.name])
                rows.append({**_brief(r), "sound": near_sound(r), "fails": [c.detail for c in checks if not c.ok]})
            self._reference = rows
        return self._reference

    def menu_table(self) -> list[dict]:
        """Every index on the menu, simply held: what each brings by itself."""
        rows = []
        for name in self.menu:
            r = self.reading(Plan(name, (name,), 1, rank_by="none"))
            rows.append({"index": name, "cagr_pct": r["cagr_pct"], "worst_fall_pct": r["worst_fall_pct"],
                         "alpha_t": r["alpha_t"]})
        return rows

    def test(self, checked: CheckedPlan, version_no: int = 0) -> AllocSummary:
        reading = self.reading(checked.plan)
        checks = judge(reading, self.incumbents())
        nearby = None
        if all(c.ok for c in checks):
            others = [self.reading(p) for p in neighbours(checked.plan)]
            held = sum(1 for r in others if r["worst_fall_pct"] <= NEIGHBOUR_FALL_PCT
                       and r["alpha_t"] is not None and r["alpha_t"] >= NEIGHBOUR_T)
            nearby = {"tested": len(others), "held_up": held}
            ok = not others or 100 * held / len(others) >= NEIGHBOURS_HOLD_PCT
            checks.append(Check("nearby", ok, f"fragile: only {held} of {len(others)} nearby settings hold up "
                                              f"(fall within {NEIGHBOUR_FALL_PCT:.0f}%, t at least {NEIGHBOUR_T:g})"))
        return AllocSummary(reading, checks, nearby)

    def exam_row(self, day: date, version: Any) -> dict[str, Any]:
        mine = self.reading(version.checked.plan, self.exam)
        keys = ("start", "end", "cagr_pct", "bench_cagr_pct", "worst_fall_pct", "avg_month_pct", "end_value",
                "invested_pct")
        behind = mine["bench_cagr_pct"] - mine["cagr_pct"]
        ok = bool(mine["worst_fall_pct"] <= MAX_FALL_PCT and behind <= EXAM_BEHIND_PTS)
        return {"day": day.isoformat(), "strategy_name": version.checked.document["name"], "title": version.title,
                "strategy": {k: mine[k] for k in keys}, "ok": ok,
                "why_not": None if ok else (f"fell {mine['worst_fall_pct']:.1f}% in the exam year"
                                            if mine["worst_fall_pct"] > MAX_FALL_PCT else
                                            f"{behind:.1f} points behind NIFTY 50 in the exam year "
                                            f"(limit {EXAM_BEHIND_PTS:g})")}


def _brief(r: dict) -> dict[str, Any]:
    keys = ("plan", "avg_month_pct", "cagr_pct", "worst_fall_pct", "worst_year_pct", "alpha_pct", "alpha_t", "beta",
            "years_beating_pct", "invested_pct", "switches_per_year")
    return {k: r[k] for k in keys}


# --- the closest plans so far -----------------------------------------------------------

def closest(rows: Sequence[dict]) -> dict[str, dict | None]:
    """Of the SOUND plans tested so far, the two nearest a pass, one from each side.

    rows: {"title", "avg_month_pct", "worst_fall_pct", "sound", "failed"} (sound = near_sound).
    `within_fall` is the best return among plans inside the fall limit; `at_target` the
    smallest fall among plans that reach the monthly target. One plan in both rows has both
    of the owner's numbers - and is a pass only if `failed` is empty, since soundness here
    is looser than the pass rule's luck test.
    """
    sound = [r for r in rows if r.get("sound")]
    within = [r for r in sound if r["worst_fall_pct"] <= MAX_FALL_PCT]
    reaching = [r for r in sound if r["avg_month_pct"] >= MIN_MONTH_PCT]
    return {"within_fall": max(within, key=lambda r: r["avg_month_pct"], default=None),
            "at_target": min(reaching, key=lambda r: r["worst_fall_pct"], default=None)}


def board_rows(reference: Sequence[dict], ledger: Sequence[dict], versions: Sequence[Any] = ()) -> list[dict]:
    rows = [{"title": r["plan"], "avg_month_pct": r["avg_month_pct"], "worst_fall_pct": r["worst_fall_pct"],
             "sound": r["sound"], "failed": list(r.get("fails", []))} for r in reference]
    for r in ledger:
        t = r.get("training")
        if r.get("valid") and t:
            rows.append({"title": r["title"], "avg_month_pct": t["plan"]["avg_month_pct"],
                         "worst_fall_pct": t["plan"]["worst_fall_pct"],
                         "sound": near_sound(t["plan"]) and t.get("checks", {}).get("nearby", True),
                         "failed": list(t.get("failed", []))})
    for v in versions:
        if v.valid and v.summary is not None:
            rows.append({"title": v.title, "avg_month_pct": v.summary.reading["avg_month_pct"],
                         "worst_fall_pct": v.summary.reading["worst_fall_pct"], "sound": v.summary.sound,
                         "failed": [c.detail for c in v.summary.checks if not c.ok]})
    return rows


# --- what the AI is told -------------------------------------------------------------

AIM = f"""\
You are designing an INDEX ALLOCATION plan for an Indian investor: which NSE indexes to
hold through index funds or ETFs, how much of the account each gets, and when to sit in
cash. You are not picking stocks.

Why indexes. Earlier stock research here was tested on today's stock list, which already
knows which companies grew, so its numbers were inflated by about 12% a year. NSE's index
histories use each date's real constituents with dividends included, so what you see
below is honest and absolute numbers can be believed.

What is established (NSE total-return data, Apr 2006 to a year ago, 2008 included):
- Momentum is the one edge found. Midcap150 Momentum 50 simply held made about 19% a
  year against about 12% for NIFTY 50 - and fell 73% in 2008.
- Simple protection (cash while NIFTY 50 is below its 200-day average; a volatility
  target; the index's own trend) cuts the fall to roughly 40-50% and keeps most of the
  return. The reference plans below give the exact figures.
- Nothing measured so far has BOTH reached the owner's target AND kept the worst fall
  inside his limit. That gap is the problem to solve.

The owner's target is {TARGET_MONTHLY_MIN:g}-{TARGET_MONTHLY_MAX:g}% a month.

How you are judged. Each plan is replayed on NSE total-return levels from {TRAIN_FROM:%b %Y}
to a year before the latest session. A decision is made at a session's close and filled
at the next session's close; {COST_PCT:g}% of every rupee switched is charged, {FUND_FEE_PCT:g}%
a year on invested money (the fund's fee and tracking shortfall), and cash earns
{100 * CASH_YIELD:g}% a year.

No lucky decision day. Every plan is run as equal parts that decide on different days -
a monthly plan as four parts a week apart, a weekly plan as five parts, one per weekday.
The first plan that ever passed here did so only when decided on the month's last
session: its worst fall was 35% there and up to 49% on other days of the month. So you
cannot gain anything from WHEN in the month a rule acts, and a rule that flips the whole
account at once (an on/off cash rule) is blurred into four steps. Rules that move
exposure gradually - a volatility target, several indexes each with its own filter -
lose least from this.

A plan PASSES only if ALL hold:
- average month at least {MIN_MONTH_PCT:g}%
- worst fall from a high no more than {MAX_FALL_PCT:.0f}% (2008 is in the test)
- beat NIFTY 50 in at least {MIN_YEARS_BEATING_PCT:.0f}% of calendar years
- luck test: t of its alpha against NIFTY 50 at least {MIN_T:g} (alpha allows for time spent
  in cash, so protection is not held against you)
- alpha above zero in BOTH halves of the history
- not worse on both return and fall than a plan already paper-traded
- robust: with each number in your plan moved about a fifth either way, at least
  {NEIGHBOURS_HOLD_PCT:.0f}% of those neighbours still have a fall within {NEIGHBOUR_FALL_PCT:.0f}% and t at least {NEIGHBOUR_T:g}
A plan that passes is paper-traded automatically, so a pass must be real.

How to think. The worst fall is set by 2008 and 2020: a plan fails the fall limit by
being too invested, too late. Return is lost by being in cash during recoveries (a slow
rule re-enters late) and by switching too often. Combine protections that act on
different things (the market's trend, the index's own trend, how violently it swings,
how the money is spread) rather than tuning one number. Use round, conventional
settings: a knife-edge setting fails the robustness check. Give the reason a rule
should work BEFORE the result - every extra knob is another chance to fit noise.
"""


def propose_prompt(*, notes: Sequence[dict], ideas_tried: Sequence[str], reference: Sequence[dict],
                   menu: Sequence[dict], board: dict, previous: Any = None, change_hint: str | None = None,
                   error: str | None = None) -> str:
    parts = [AIM, "The menu: every index you may use, simply held over the same window "
                  "(yearly return, worst fall, t of alpha):"]
    parts += [f"- {m['index']}: {m['cagr_pct']:.1f}% a year, fall {m['worst_fall_pct']:.0f}%, t {m['alpha_t']}"
              for m in menu]
    parts.append("\nReference plans already measured (do not propose these again; build on what they show):")
    parts += [json.dumps(r, default=str) for r in reference]
    near = [f"- inside the fall limit, the best return so far: {b['title']} - {b['avg_month_pct']:.2f}% a month, "
            f"fall {b['worst_fall_pct']:.1f}%" for b in [board.get("within_fall")] if b]
    near += [f"- at the monthly target, the smallest fall so far: {b['title']} - {b['avg_month_pct']:.2f}% a month, "
             f"fall {b['worst_fall_pct']:.1f}%" for b in [board.get("at_target")] if b]
    if near:
        parts += ["\nThe closest sound plans so far - beat one of these and you are nearer a pass:", *near]
    if notes:
        parts.append("\nWhat earlier days learned:")
        parts += [f"- {n['day']}: {n['idea_title']} - {n['lessons']}" for n in notes]
    if ideas_tried:
        parts.append("\nVersions already tried (do not repeat one that failed the same way):")
        parts += [f"- {line}" for line in ideas_tried]
    if previous is not None:
        parts += ["\nHow your previous version did:", json.dumps(previous.as_dict(), default=str)]
    if change_hint:
        parts.append(f"\nThe change to make this time: {change_hint}")
    if error:
        parts.append(f"\nYour last attempt was rejected. Fix exactly this and return the whole plan:\n{error}")
    parts += ["\nThe format (every key shown; rank, filters, market_filter and volatility_target are optional, "
              "as is each key inside them):", FORMAT,
              "Return ONE plan in this format as YAML in strategy_yaml. change_note: empty for a first "
              "version, otherwise one line on what changed and why."]
    return "\n".join(parts)


def review_prompt(*, summary: AllocSummary, version: int, versions_left: int) -> str:
    parts = [f"Your version {version} was tested.", "", json.dumps(summary.as_dict(), default=str), "",
             "Read `failed` first: every check it did not clear. Then the year-by-year rows (yearly against "
             "bench_yearly) - which years made the fall, and which years it gave return away by being in cash. "
             "invested_pct and switches_per_year say how the plan behaved.", "",
             "Then set decision to one of:",
             "- next_version: keep this idea and change one thing (say what in change_hint)",
             "- new_idea: start a different one",
             '- stop: nothing further is worth trying today; name an earlier better version in final_version '
             '("v1.2") if there is one',
             "In lessons, write what the NEXT day should know: what moved the fall, what cost return, "
             "what is not worth trying again."]
    parts.append('\nThis was the LAST VERSION allowed today, so decision MUST be "stop".' if versions_left <= 0
                 else f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)


class AllocBrain(OpusBrain):
    def __init__(self, claude: Any, *, reference: Sequence[dict], menu: Sequence[dict], board: dict,
                 echo: Any = print) -> None:
        super().__init__(claude, [FORMAT], echo=echo)
        self._reference, self._menu, self._board = reference, menu, board
        self._today: list[str] = []      # today's own versions: the loop only hands over earlier days'
        self._last: dict[str, Any] = {}

    def propose(self, *, notes, ideas_tried, previous, change_hint, error) -> dict[str, Any]:
        from research.prompts import PROPOSE_SCHEMA
        if error:
            self._echo(f"  repairing: {error.splitlines()[0][:140]}")
        prompt = propose_prompt(notes=notes, ideas_tried=[*self._today, *ideas_tried], reference=self._reference,
                                menu=self._menu, board=self._board, previous=previous, change_hint=change_hint,
                                error=error)
        self._last = self._ask(prompt, PROPOSE_SCHEMA, "Read the input and design ONE plan. Answer only "
                                                       "with the required fields.")
        return self._last

    def review(self, *, summary, version, versions_left) -> dict[str, Any]:
        from research.prompts import REVIEW_SCHEMA
        spec = " ".join(str(self._last.get("strategy_yaml", "")).split())
        self._today.insert(0, f"today, version {version} ({self._last.get('title', '')}): {one_line(summary)}"
                              f" - plan: {spec}")
        return self._ask(review_prompt(summary=summary, version=version, versions_left=versions_left),
                         REVIEW_SCHEMA, "Read the input, judge the version and decide what to do next. "
                                        "Answer only with the required fields.")


DRY_YAML = """\
version: alloc/1
universe: [NIFTY MIDCAP150 MOMENTUM 50]
hold: 1
rank: {by: none}
market_filter: {index: NIFTY 50, average: 200}
volatility_target: {percent: 15, sessions: 60}
decide: weekly
"""


class DryBrain:
    def propose(self, **_: Any) -> dict[str, Any]:
        return {"title": "Dry run placeholder (no AI)", "description": "Midcap150 Momentum 50 under a weekly "
                "NIFTY cash rule and a 15% volatility target.", "hypothesis": "None: a fixed input.",
                "strategy_yaml": DRY_YAML, "change_note": ""}

    def review(self, **_: Any) -> dict[str, Any]:
        return {"why_failed": "", "why_worked": "", "decision": "stop",
                "lessons": "Dry run: no AI was asked, so nothing was learned."}


# --- records -----------------------------------------------------------------------------

def _path(name: str, root: Path | None) -> Path:
    return (root or Path(".")) / DIR / name


def _append(path: Path, rows: Sequence[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def read_ledger(root: Path | None = None) -> list[dict]:
    p = _path(LEDGER, root)
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def notes(root: Path | None = None, limit: int = 30) -> list[dict]:
    last: dict[tuple, dict] = {}
    for r in read_ledger(root):
        if r.get("valid") and r.get("lessons"):
            last[(r["day"], r["idea_no"])] = r
    rows = sorted(last.values(), key=lambda r: (r["day"], r["idea_no"]), reverse=True)[:limit]
    return [{"day": r["day"], "idea_title": r["title"], "lessons": r["lessons"]} for r in rows]


def ideas_tried(root: Path | None = None, limit: int = 150) -> list[str]:
    rows = [r for r in read_ledger(root) if r.get("valid")][-limit:]
    return [f"{r['title']}: {one_line(r.get('training'))} - plan: "
            + " ".join(yaml.safe_dump({k: v for k, v in (r.get('strategy') or {}).items() if k != 'name'},
                                      default_flow_style=True, sort_keys=False).split())
            for r in reversed(rows)]


def version_rows(day: date, versions: Sequence[Any]) -> list[dict]:
    return [{"day": day.isoformat(), "idea_no": v.idea_no, "version_no": v.version_no, "title": v.title,
             "description": v.description, "hypothesis": v.hypothesis, "change_note": v.change_note,
             "valid": v.valid, "error": v.error,
             "strategy_name": v.checked.document["name"] if v.checked is not None else None,
             "strategy": v.checked.document if v.checked is not None else None,
             "training": v.summary.as_dict() if v.summary is not None else None,
             "lessons": v.review.get("lessons"), "decision": v.review.get("decision")} for v in versions]


def write_note(day: date, versions: Sequence[Any], root: Path | None = None) -> Path:
    tested = [v for v in versions if v.valid and v.summary is not None]
    lines = [f"# Index allocation research notes — {day.isoformat()}", ""]
    if not tested:
        lines.append("No plan reached a tested version today.")
    for v in tested:
        lines += [f"## v{v.idea_no}.{v.version_no} {v.title or 'untitled plan'}", "",
                  f"**Training outcome:** {one_line(v.summary)}", "",
                  f"**Lessons:** {v.review.get('lessons') or 'none recorded'}", ""]
    path = _path(f"{day.isoformat()}.md", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))
    return path


# --- promotion: a pass starts paper-trading by itself ----------------------------------

def read_promoted(root: Path | None = None) -> list[dict]:
    p = (root or Path(".")) / PROMOTED
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else []


def promote(day: date, version: Any, exam: dict, root: Path | None = None) -> tuple[dict | None, str]:
    """Add a passed plan to the paper books. (the new entry or None, what happened in words)."""
    rows = read_promoted(root)
    doc = {k: v for k, v in version.checked.document.items() if k != "name"}
    if any({k: v for k, v in r["document"].items() if k != "name"} == doc for r in rows):
        return None, "this exact plan is already paper-traded"
    if len(rows) >= MAX_PROMOTED:
        return None, (f"NOT paper-traded: {MAX_PROMOTED} plans from this research are already running "
                      "- drop one first")
    s = version.summary.reading
    start = day + timedelta(days=1)
    entry = {
        "key": f"alloc-{day:%Y%m%d}-{len([r for r in rows if r['day'] == day.isoformat()]) + 1}",
        "title": version.title, "day": day.isoformat(), "start": start.isoformat(),
        "document": version.checked.document,
        "training": (f"{s['avg_month_pct']:.2f}% a month ({s['cagr_pct']:.1f}% a year against NIFTY 50's "
                     f"{s['bench_cagr_pct']:.1f}%), worst fall {s['worst_fall_pct']:.0f}%, luck test t = "
                     f"{s['alpha_t']} - on NSE's own index histories {s['start']} to {s['end']}, 2008 included, "
                     f"fund fee charged; exam year {exam['strategy']['cagr_pct']:+.1f}% against NIFTY 50's "
                     f"{exam['strategy']['bench_cagr_pct']:+.1f}%"),
    }
    path = (root or Path(".")) / PROMOTED
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps([*rows, entry], indent=1, default=str) + "\n").encode("utf-8"))
    return entry, f"now PAPER-TRADED from {start.isoformat()} (Paper books: {entry['key']})"


# --- the day -----------------------------------------------------------------------------

STOPPED = {"stop": "the AI chose to stop", "version_limit": "all versions used",
           "time_budget": "the time budget ran out", "stopped_limit": "Claude stopped the day"}


def run_day(*, brain: Any, lab: Lab, day: date, max_versions: int, budget_seconds: float,
            notes_: Sequence[dict] = (), ideas: Sequence[str] = (), seen: dict[Plan, str] | None = None,
            echo: Callable[..., None] = print):
    from research.loop import run_versions

    seen = dict(seen) if seen is not None else {same_plan(p): p.name for p in REFERENCE}

    def check(text: str, *, idea: str, day: date, version: int) -> CheckedPlan:
        checked = check_proposal(text, idea=idea, day=day, version=version, menu=lab.menu)
        # On 15 Nov 2026 the AI wanted a rule the format cannot express and sent the previous
        # plan again, unchanged: a version spent on a result already known.
        before = seen.get(same_plan(checked.plan))
        if before is not None:
            raise PlanError(f"this exact plan was already tested ('{before}'). Change a setting, or propose "
                            "a different plan. If the rule you want cannot be written in the format, use the "
                            "nearest thing the format CAN express and say so in the description.")
        return checked

    def test(checked: CheckedPlan, version_no: int) -> AllocSummary:
        summary = lab.test(checked, version_no)
        seen[same_plan(checked.plan)] = checked.document["name"]
        echo(f"    {checked.document['name']}: {one_line(summary)}", flush=True)
        return summary

    outcome = run_versions(brain=brain, check=check, test=test, day=day,
                           notes=notes_, ideas_tried=ideas, max_versions=max_versions,
                           budget_seconds=budget_seconds)
    tested = [(v, v.summary) for v in outcome.versions if v.valid]
    final = choose_final(tested, score)
    # The day's final version is the AI's pick, but a pass is a pass: if the pick did not
    # pass and another version did, the best passer is the one examined.
    passed = [pair for pair in tested if pair[1].passes]
    if passed and (final is None or not final[1].passes):
        final = max(passed, key=lambda pair: score(pair[1]))
    exam = lab.exam_row(day, final[0]) if final is not None and final[1].passes else None
    return outcome, final, exam


def message(day: date, outcome: Any, final: Any, exam: dict | None, *, board: dict, promoted: str = "",
            dry_run: bool = False) -> tuple[str, list]:
    """(subject, blocks) in the morning message's layout (research.blocks). The verdict comes first."""
    from research.blocks import Block, lesson
    tested = [v for v in outcome.versions if v.valid]
    if exam is not None and exam["ok"]:
        verdict = f"A PLAN PASSED, exam year included. {promoted}".strip()
    elif exam is not None:
        verdict = f"PASSED training but FAILED its exam year: {exam['why_not']}. Not paper-traded."
    elif not tested:
        verdict = "NOTHING TESTED: no plan got past the checker."
    else:
        verdict = (f"NOTHING PASSED: no plan reached {MIN_MONTH_PCT:g}% a month with a worst fall "
                   f"inside {MAX_FALL_PCT:.0f}% and cleared the luck checks.")
    blocks = [Block(f"📊 Index allocation research — {day.isoformat()}" + (" (dry run, no AI)" if dry_run else ""),
                    note=f"Verdict: {verdict}")]
    for v in outcome.versions:
        tag = f"v{v.idea_no}.{v.version_no}"
        if not v.valid:
            blocks.append(Block(f"{tag} - {v.title or '(no title)'}",
                                note=f"REJECTED by the checker: {(v.error or '').splitlines()[0][:200]}"))
            continue
        s = v.summary.reading
        mark = " (the day's final version)" if final is not None and v is final[0] else ""
        rows = (("avg month", f"{s['avg_month_pct']:.2f}%"),
                ("a year", f"{s['cagr_pct']:.1f}% (NIFTY {s['bench_cagr_pct']:.1f}%)"),
                ("worst fall", f"{s['worst_fall_pct']:.1f}%"), ("luck t", str(s["alpha_t"])),
                ("invested", f"{s['invested_pct']:.0f}% of days"),
                ("result", "PASSES" if v.summary.passes else "FAILS"))
        failed = [c.detail for c in v.summary.checks if not c.ok]
        note = " ".join([f"Why: {'; '.join(failed)}." if failed else "", lesson(v.review.get("lessons"))]).strip()
        blocks.append(Block(f"{tag} - {v.title}{mark}", rows, note))
    if exam is not None:
        s = exam["strategy"]
        blocks.append(Block(f"Exam year {s['start']} to {s['end']}, opened once",
                            (("this plan", f"{s['cagr_pct']:+.1f}%"), ("NIFTY 50", f"{s['bench_cagr_pct']:+.1f}%"),
                             ("worst fall", f"{s['worst_fall_pct']:.1f}%"),
                             ("result", "OK" if exam["ok"] else "FAILED")),
                            promoted if exam["ok"] else f"{exam['why_not']}."))
    blocks += closest_blocks(board)
    blocks.append(Block("How the day ended", (("stopped", STOPPED.get(outcome.stopped_because, outcome.stopped_because)),),
                        outcome.stop_detail[:200] if outcome.stop_detail else ""))
    return f"Index allocation research {day.isoformat()}: {verdict.split(':')[0].split('.')[0]}"[:120], blocks


def closest_blocks(board: dict) -> list:
    """The "closest so far" block: how far a pass is, in the owner's two numbers."""
    from research.blocks import Block
    inside, reaching = board.get("within_fall"), board.get("at_target")
    figures = lambda b: f"{b['avg_month_pct']:.2f}%/mo, fall {b['worst_fall_pct']:.0f}%"      # noqa: E731
    if inside and reaching and inside["title"] == reaching["title"]:
        failed = inside.get("failed") or []
        note = (f"'{inside['title']}' has BOTH of your numbers: {MIN_MONTH_PCT:g}% a month and a worst fall "
                f"inside {MAX_FALL_PCT:.0f}%. "
                + (f"It is not a pass yet because: {'; '.join(failed)}." if failed else "It passed."))
        return [Block("Closest so far (every day's plans)", (("both numbers", figures(inside)),), note)]
    rows, names = [], []
    if inside:
        rows.append((f"fall <= {MAX_FALL_PCT:.0f}%", figures(inside)))
        names.append(f"Inside the fall limit, the best return is '{inside['title']}'.")
    if reaching:
        rows.append((f">= {MIN_MONTH_PCT:g}%/mo", figures(reaching)))
        names.append(f"At the monthly target, the smallest fall is '{reaching['title']}'.")
    if not rows:
        return []
    return [Block("Closest so far (every day's plans)", tuple(rows),
                  " ".join(names) + " A pass needs one plan with both.")]


def _commit(day: date) -> None:
    try:
        paths = [str(DIR)] + ([str(PROMOTED)] if PROMOTED.exists() else [])
        subprocess.run(["git", "add", *paths], check=True, capture_output=True, text=True)
        done = subprocess.run(["git", "-c", "user.name=research loop", "-c", "user.email=noreply@anthropic.com",
                               "commit", "-m", f"docs(research): index allocation notes for {day}"],
                              capture_output=True, text=True)
        if done.returncode != 0 and not nothing_to_commit(done.stdout):
            print(f"WARNING: notes not committed: {done.stderr.strip() or done.stdout.strip()}", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"WARNING: notes not committed: {exc}", file=sys.stderr)


def download() -> pd.DataFrame:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from nse_index_history import fetch_whole
    levels = fetch_whole(list(MENU))
    missing = [m for m in MENU if m not in levels.columns]
    if BENCHMARK not in levels.columns or len(missing) > 5:
        raise RuntimeError(f"NSE index download incomplete: missing {', '.join(missing) or BENCHMARK}")
    return levels


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Run one index allocation research day.")
    parser.add_argument("--max-versions", type=int, default=0, help="default 5")
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--skip-download", action="store_true", help="use the stored NSE index levels")
    parser.add_argument("--commit-journal", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)

    from research.brain import MODEL, Claude
    from research.loop import MAX_VERSIONS

    if not args.skip_download or not LEVELS.exists():
        levels = download()
        LEVELS.parent.mkdir(parents=True, exist_ok=True)
        levels.to_parquet(LEVELS)
    levels = pd.read_parquet(LEVELS)
    lab = Lab(levels, read_promoted())
    day = datetime.now(IST).date()
    print(f"day        {day}")
    print(f"training   {lab.train[0]} -> {lab.train[1]}; exam {lab.exam[0]} -> {lab.exam[1]} (opened only on a pass)")
    print(f"menu       {len(lab.menu)} NSE indexes")
    print(f"brain      {'dry run - no AI is asked' if args.dry_run else f'claude -p, model {MODEL}, no tools'}")
    reference, ledger = lab.reference(), read_ledger()
    for r in reference:
        print(f"reference  {r['plan']}: {r['avg_month_pct']:.2f}%/month, fall {r['worst_fall_pct']:.1f}%, t {r['alpha_t']}")
    brain: Any = DryBrain() if args.dry_run else AllocBrain(
        Claude(), reference=reference, menu=lab.menu_table(), board=closest(board_rows(reference, ledger)))
    outcome, final, exam = run_day(brain=brain, lab=lab, day=day, max_versions=args.max_versions or MAX_VERSIONS,
                                   budget_seconds=args.budget_seconds, notes_=notes(), ideas=ideas_tried(),
                                   seen=None if args.dry_run else tested_before(ledger, lab.menu))
    promoted = ""
    if exam is not None and exam["ok"] and not args.no_save and not args.dry_run:
        _, promoted = promote(day, final[0], exam)
    from research.blocks import as_text, send
    board = closest(board_rows(reference, ledger, outcome.versions))
    subject, blocks = message(day, outcome, final, exam, board=board, promoted=promoted, dry_run=args.dry_run)
    print("\n" + as_text(blocks))
    if not args.no_save:
        _append(_path(LEDGER, None), version_rows(day, outcome.versions))
        print(f"\njournal    {write_note(day, outcome.versions)}")
        if exam is not None:
            _append(_path(EXAMS, None), [exam])
        if args.commit_journal:
            _commit(day)
    if args.notify:
        send(subject, blocks)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
