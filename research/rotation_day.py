"""The stock research day, rebuilt on momentum rotation (owner's decision, 20 Oct 2026).

    python -m research.rotation_day
    python -m research.rotation_day --dry-run --no-save --skip-download
    python -m research.rotation_day --max-versions 5 --commit-journal --notify

The AI proposes a VARIATION of momentum rotation (format below); it is tested
on today's NIFTY 200, Jan 2009 to a year before the latest session, and must
beat the momentum rule already paper-traded (the BASELINE) on the same stocks.
Comparing two strategies on the same list cancels most of its hindsight bias
(docs/research/2026-10-20-momentum-rotation.md): absolute numbers here run
about 12% a year above what NSE's real indexes show, and are never quoted
alone.

The loop, the Claude call and the final choice are the existing ones
(research.loop, research.brain, research.run_day). Only a final version that
passes opens the exam year, once, after the last AI call. Records live in
research/journal/rotation/; the exam file is never read back for the AI.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
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
from research.portfolio import PASS_MAX_FALL_PCT, PASS_T
from research.rotation import INDEX, SCORES, WEIGHTINGS, Variant, month_returns, run, stats
from research.run_day import OpusBrain, choose_final, nothing_to_commit
from research.segment import TARGET_MONTHLY_MAX, TARGET_MONTHLY_MIN

VERSION = "rotation/1"
CLOSES = Path("data/rotation/closes.parquet")
DIR = Path("research/journal/rotation")
LEDGER, EXAMS = "ledger.jsonl", "exams.jsonl"
TRAIN_FROM = date(2009, 1, 1)
EXAM_DAYS = 365
COST_PCT = 0.2
WARMUP_DAYS = 800          # history fed to an exam run so its lookback and averages exist
BASELINE = Variant("baseline: 12-1 momentum, 20 stocks, NIFTY 200-day filter", 12, 1, 20, True)
MAX_FALL_PCT = PASS_MAX_FALL_PCT
MIN_YEARS_BEATING_PCT = 50.0
MIN_T = PASS_T
BUDGET_SECONDS = 4 * 60 * 60

FORMAT = """\
version: rotation/1
lookback_months: 12          # 1-24: the return each stock is ranked on
skip_months: 1               # 0-3, less than lookback: the most recent months left out
holdings: 20                 # 5-50 stocks held
score: return                # return | return_over_volatility (return / annualised volatility)
filters:                     # optional, each may be left out
  above_own_average: 200     # 20-300: only stocks closing above their own N-session average
  max_volatility_pct: 45     # 10-100: only stocks below this annualised volatility
weighting: equal             # equal | inverse_volatility
market_filter: 200           # 50-300: hold cash while NIFTY 50 is below its N-session average; or none
"""


class CheckError(ValueError):
    """A proposal that cannot be tested. The message goes back to the AI."""


@dataclass(frozen=True)
class CheckedRotation:
    document: dict[str, Any]
    variant: Variant


def research_name(day: date, idea: str, version: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:40] or "idea"
    return f"M-{day:%Y%m%d}-{slug}-v{version}"


def _whole(doc: dict, key: str, lo: int, hi: int, default: Any = None) -> int | None:
    value = doc.get(key, default)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
        raise CheckError(f"{key} must be a whole number from {lo} to {hi}")
    return value


def check_proposal(text: str, *, idea: str, day: date, version: int) -> CheckedRotation:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CheckError(f"not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CheckError("the strategy must be ONE YAML mapping in the rotation/1 format")
    allowed = {"version", "name", "lookback_months", "skip_months", "holdings", "score", "filters",
               "weighting", "market_filter"}
    extra = sorted(map(str, set(doc) - allowed))
    if extra:
        raise CheckError(f"unknown key(s) {', '.join(extra)} (allowed: {', '.join(sorted(allowed))})")
    if doc.get("version") != VERSION:
        raise CheckError(f"version must be {VERSION}")
    lookback = _whole(doc, "lookback_months", 1, 24, 12)
    skip = _whole(doc, "skip_months", 0, 3, 0)
    if skip >= lookback:
        raise CheckError("skip_months must be less than lookback_months")
    holdings = _whole(doc, "holdings", 5, 50, 20)
    score = doc.get("score", "return")
    if score not in SCORES:
        raise CheckError(f"score must be one of {', '.join(SCORES)}")
    weighting = doc.get("weighting", "equal")
    if weighting not in WEIGHTINGS:
        raise CheckError(f"weighting must be one of {', '.join(WEIGHTINGS)}")
    filters = doc.get("filters") or {}
    if not isinstance(filters, dict):
        raise CheckError("filters must be a mapping")
    fextra = sorted(map(str, set(filters) - {"above_own_average", "max_volatility_pct"}))
    if fextra:
        raise CheckError(f"unknown filter(s) {', '.join(fextra)} (allowed: above_own_average, max_volatility_pct)")
    above = _whole(filters, "above_own_average", 20, 300)
    max_vol = filters.get("max_volatility_pct")
    if max_vol is not None and (not isinstance(max_vol, (int, float)) or isinstance(max_vol, bool)
                                or not 10 <= max_vol <= 100):
        raise CheckError("filters.max_volatility_pct must be a number from 10 to 100")
    market = doc.get("market_filter", 200)
    if market in (None, "none", False):
        market = None
    elif not isinstance(market, int) or isinstance(market, bool) or not 50 <= market <= 300:
        raise CheckError("market_filter must be a whole number of sessions from 50 to 300, or none")
    name = research_name(day, idea, version)
    doc = {**doc, "name": name}
    variant = Variant(name, lookback, skip, holdings, market is not None, score=score, above_sma=above,
                      max_volatility=None if max_vol is None else float(max_vol), weighting=weighting,
                      trend_sessions=market or 200)
    return CheckedRotation(doc, variant)


def windows(closes: pd.DataFrame) -> tuple[tuple[date, date], tuple[date, date]]:
    """(training, exam): the exam is the last 365 days of sessions, training everything before."""
    sessions = sorted(closes[INDEX].dropna().index)
    cut = sessions[-1] - timedelta(days=EXAM_DAYS)
    train_end = max(d for d in sessions if d <= cut)
    exam = [d for d in sessions if d > train_end]
    return (TRAIN_FROM, train_end), (exam[0], exam[-1])


def _versus(eq: pd.Series, base: pd.Series) -> dict[str, Any]:
    m, b = month_returns(eq), month_returns(base)
    excess = (m - b).dropna()
    sd = statistics.stdev(excess) if len(excess) > 2 else 0.0
    t = statistics.mean(excess) / (sd / math.sqrt(len(excess))) if sd > 0 else None
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = lambda s: 100 * ((s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1)        # noqa: E731

    def by_year(s: pd.Series) -> pd.Series:
        ends = s.groupby(pd.Index([d.year for d in s.index])).last()
        return ends / ends.shift(1).fillna(s.iloc[0]) - 1
    ye, yb = by_year(eq), by_year(base)
    return {"cagr_lead_pct": round(cagr(eq) - cagr(base), 2), "luck_t": None if t is None else round(t, 2),
            "years_beating_baseline_pct": round(100 * float((ye > yb.reindex(ye.index)).mean()), 0)}


def disqualified(s: dict, vs: dict) -> str | None:
    if s["worst_fall_pct"] > MAX_FALL_PCT:
        return f"fell {s['worst_fall_pct']:.1f}% from a high (limit {MAX_FALL_PCT:.0f}%)"
    if s["years_beating_pct"] < MIN_YEARS_BEATING_PCT:
        return f"beat NIFTY 50 in only {s['years_beating_pct']:.0f}% of years"
    if vs["cagr_lead_pct"] <= 0:
        return (f"no better than the momentum rule already paper-traded "
                f"({vs['cagr_lead_pct']:+.2f}% a year against it)")
    if vs["luck_t"] is None or vs["luck_t"] < MIN_T:
        return f"its lead over the paper-traded rule could be luck: t = {vs['luck_t']} (needs {MIN_T:g})"
    return None


@dataclass
class RotationSummary:
    reading: dict[str, Any]
    versus_baseline: dict[str, Any]
    baseline: dict[str, Any]
    why_not: str | None

    @property
    def passes(self) -> bool:
        return self.why_not is None

    def as_dict(self) -> dict[str, Any]:
        return {"passes": self.passes, "why_not": self.why_not, "strategy": self.reading,
                "versus_baseline": self.versus_baseline, "baseline": self.baseline}


def score(summary: RotationSummary) -> tuple[bool, float]:
    return summary.passes, summary.versus_baseline["cagr_lead_pct"]


def one_line(summary: RotationSummary | dict | None) -> str:
    if summary is None:
        return "not tested"
    d = summary.as_dict() if isinstance(summary, RotationSummary) else summary
    s, v = d["strategy"], d["versus_baseline"]
    verdict = "PASSES" if d["passes"] else f"fails: {d['why_not']}"
    return (f"{v['cagr_lead_pct']:+.2f}%/yr vs the paper rule (t {v['luck_t']}), worst fall "
            f"{s['worst_fall_pct']:.1f}%, beat NIFTY in {s['years_beating_pct']:.0f}% of years - {verdict}")


class Lab:
    def __init__(self, closes: pd.DataFrame) -> None:
        self.closes = closes
        self.train, self.exam = windows(closes)
        self._baseline: Any = None

    def _run(self, variant: Variant, window: tuple[date, date], warm: bool) -> Any:
        frame = self.closes
        if warm:
            frame = frame.loc[frame.index >= window[0] - timedelta(days=WARMUP_DAYS)]
        return run(frame, variant, start=window[0], end=window[1], cost_pct=COST_PCT)

    def baseline(self) -> Any:
        if self._baseline is None:
            self._baseline = self._run(BASELINE, self.train, False)
        return self._baseline

    def test(self, checked: CheckedRotation, version_no: int = 0) -> RotationSummary:
        result = self._run(checked.variant, self.train, False)
        base = self.baseline()
        nifty = self.closes[INDEX]
        reading, base_reading = stats(result, nifty), stats(base, nifty)
        vs = _versus(result.equity, base.equity)
        for r in (reading, base_reading):
            r["yearly"] = {int(k): v for k, v in r["yearly"].items()}
        return RotationSummary(reading, vs, base_reading, disqualified(reading, vs))

    def exam_row(self, day: date, version: Any) -> dict[str, Any]:
        nifty = self.closes[INDEX]
        mine = stats(self._run(version.checked.variant, self.exam, True), nifty)
        base = stats(self._run(BASELINE, self.exam, True), nifty)
        keys = ("start", "end", "cagr_pct", "bench_cagr_pct", "worst_fall_pct", "avg_month_pct", "end_value")
        return {"day": day.isoformat(), "strategy_name": version.checked.document["name"], "title": version.title,
                "strategy": {k: mine[k] for k in keys}, "baseline": {k: base[k] for k in keys}}


# --- what the AI is told -------------------------------------------------------------

AIM = f"""\
You are improving a stock MOMENTUM ROTATION strategy for NSE large caps.

The evidence so far (NSE's own total-return indexes, real constituents, dividends in):
- Nifty200 Momentum 30: about 21% a year 2009 to Oct 2025, against 15% for NIFTY 50,
  worst fall 34%. The edge is real but its luck test is t = 1.7.
- The rule now paper-traded - hold momentum only while NIFTY 50 is above its
  200-day average, cash otherwise - made about 17.5% a year with a worst fall of
  23%, and held up in the exam year when NIFTY fell 10%.

The owner's target is {TARGET_MONTHLY_MIN:g}-{TARGET_MONTHLY_MAX:g}% a month (about 20-27% a year) on the REAL market.

How you are judged. Every version is tested on today's NIFTY 200 stocks, Jan 2009
to Oct 2025, monthly rebalancing at the next session's close, 0.2% of every rupee
traded. Today's list knows which companies grew, so ABSOLUTE numbers here are
inflated by about 12% a year - never believe them alone. A version is judged
against the BASELINE (12-1 momentum, 20 stocks, NIFTY 200-day filter - the
paper-traded rule) on the SAME stocks, which cancels most of that bias. It PASSES
only if ALL hold:
- worst fall from a high no more than {MAX_FALL_PCT:.0f}%
- beat NIFTY 50 in at least {MIN_YEARS_BEATING_PCT:.0f}% of calendar years
- a higher yearly return than the baseline
- that lead is more than luck: t of the monthly excess over the baseline >= {MIN_T:g}

Change the rule for a REASON (a known effect: volatility-adjusted momentum,
trend confirmation, avoiding crash-prone stocks, concentration), not to fit
the history: every knob you turn is another chance to fit noise.
"""


def propose_prompt(*, notes: Sequence[dict], ideas_tried: Sequence[str], baseline: dict,
                   previous: Any = None, change_hint: str | None = None, error: str | None = None) -> str:
    parts = [AIM, "The baseline's own reading on this data:",
             json.dumps({k: baseline[k] for k in ("cagr_pct", "worst_fall_pct", "years_beating_pct",
                                                    "months_in_cash_pct", "yearly")}, default=str)]
    if notes:
        parts.append("\nWhat earlier rotation days learned:")
        parts += [f"- {n['day']}: {n['idea_title']} - {n['lessons']}" for n in notes]
    if ideas_tried:
        parts.append("\nVersions already tried (do not repeat one that failed the same way):")
        parts += [f"- {line}" for line in ideas_tried]
    if previous is not None:
        parts += ["\nHow your previous version did:", json.dumps(previous.as_dict(), default=str)]
    if change_hint:
        parts.append(f"\nThe change to make this time: {change_hint}")
    if error:
        parts.append(f"\nYour last attempt was rejected. Fix exactly this and return the whole strategy:\n{error}")
    parts += ["\nThe format (every key shown; filters and their keys are optional):", FORMAT,
              "Return ONE strategy in this format as YAML in strategy_yaml. change_note: empty for a first "
              "version, otherwise one line on what changed and why."]
    return "\n".join(parts)


def review_prompt(*, summary: RotationSummary, version: int, versions_left: int) -> str:
    parts = [f"Your version {version} was tested. Judge it against the baseline, not on its absolute "
             "numbers (they carry about 12% a year of hindsight bias).", "",
             json.dumps(summary.as_dict(), default=str), "",
             "Read versus_baseline (cagr_lead_pct, luck_t, years_beating_baseline_pct), the worst fall, and "
             "the year-by-year rows of both before believing an average.", "",
             "Then set decision to one of:",
             "- next_version: keep this idea and change one thing (say what in change_hint)",
             "- new_idea: start a different one",
             '- stop: nothing further is worth trying today; name an earlier better version in final_version '
             '("v1.2") if there is one']
    parts.append('\nThis was the LAST VERSION allowed today, so decision MUST be "stop".' if versions_left <= 0
                 else f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)


class RotationBrain(OpusBrain):
    def __init__(self, claude: Any, *, baseline: dict, echo: Any = print) -> None:
        super().__init__(claude, [FORMAT], echo=echo)
        self._baseline = baseline
        # The loop hands every proposal the same list of EARLIER days' versions; without
        # today's own, a "new idea" came back as the version just tested (20 Oct 2026).
        self._today: list[str] = []
        self._last: dict[str, Any] = {}

    def propose(self, *, notes, ideas_tried, previous, change_hint, error) -> dict[str, Any]:
        from research.prompts import PROPOSE_SCHEMA
        if error:
            self._echo(f"  repairing: {error.splitlines()[0][:140]}")
        prompt = propose_prompt(notes=notes, ideas_tried=[*self._today, *ideas_tried], baseline=self._baseline,
                                previous=previous, change_hint=change_hint, error=error)
        self._last = self._ask(prompt, PROPOSE_SCHEMA, "Read the input and design ONE strategy. Answer only "
                                                       "with the required fields.")
        return self._last

    def review(self, *, summary, version, versions_left) -> dict[str, Any]:
        from research.prompts import REVIEW_SCHEMA
        spec = " ".join(str(self._last.get("strategy_yaml", "")).split())
        self._today.insert(0, f"today, version {version} ({self._last.get('title', '')}): {one_line(summary)}"
                              f" - strategy: {spec}")
        return self._ask(review_prompt(summary=summary, version=version, versions_left=versions_left),
                         REVIEW_SCHEMA, "Read the input, judge the version and decide what to do next. "
                                        "Answer only with the required fields.")


DRY_YAML = """\
version: rotation/1
lookback_months: 12
skip_months: 1
holdings: 20
score: return_over_volatility
weighting: equal
market_filter: 200
"""


class DryBrain:
    def propose(self, **_: Any) -> dict[str, Any]:
        return {"title": "Dry run placeholder (no AI)", "description": "The baseline, ranked on "
                "volatility-adjusted return.", "hypothesis": "None: a fixed input.",
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


def ideas_tried(root: Path | None = None, limit: int = 200) -> list[str]:
    rows = [r for r in read_ledger(root) if r.get("valid")][-limit:]
    return [f"{r['strategy_name']} ({r['title']}): {one_line(r.get('training'))}"
            + (f" - lesson: {r['lessons']}" if r.get("lessons") else "") for r in reversed(rows)]


def version_rows(day: date, versions: Sequence[Any]) -> list[dict]:
    return [{"day": day.isoformat(), "idea_no": v.idea_no, "version_no": v.version_no, "title": v.title,
             "description": v.description, "hypothesis": v.hypothesis, "change_note": v.change_note,
             "valid": v.valid, "error": v.error,
             "strategy_name": v.checked.document["name"] if v.checked is not None else None,
             "strategy": v.checked.document if v.checked is not None else None,
             "training": v.summary.as_dict() if v.summary is not None else None,
             "lessons": v.review.get("lessons"), "decision": v.review.get("decision")} for v in versions]


def write_note(day: date, versions: Sequence[Any], root: Path | None = None) -> Path:
    by_idea = {v.idea_no: v for v in versions if v.valid and v.review}
    lines = [f"# Rotation research notes — {day.isoformat()}", ""]
    if not by_idea:
        lines.append("No idea reached a tested version today.")
    for _, v in sorted(by_idea.items()):
        lines += [f"## {v.title or 'untitled idea'}", "", f"**Training outcome:** {one_line(v.summary)}", "",
                  f"**Lessons:** {v.review.get('lessons') or 'none recorded'}", ""]
    path = _path(f"{day.isoformat()}.md", root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))
    return path


# --- the day -----------------------------------------------------------------------------

STOPPED = {"stop": "the AI chose to stop", "version_limit": "all versions used",
           "time_budget": "the time budget ran out", "stopped_limit": "Claude stopped the day"}


def run_day(*, brain: Any, lab: Lab, day: date, max_versions: int, budget_seconds: float,
            notes_: Sequence[dict] = (), ideas: Sequence[str] = (), echo: Callable[..., None] = print):
    from research.loop import run_versions

    def test(checked: CheckedRotation, version_no: int) -> RotationSummary:
        summary = lab.test(checked, version_no)
        echo(f"    {checked.document['name']}: {one_line(summary)}", flush=True)
        return summary

    outcome = run_versions(brain=brain, check=check_proposal, test=test, day=day, notes=notes_,
                           ideas_tried=ideas, max_versions=max_versions, budget_seconds=budget_seconds)
    tested = [(v, v.summary) for v in outcome.versions if v.valid]
    final = choose_final(tested, score)
    exam = lab.exam_row(day, final[0]) if final is not None and final[1].passes else None
    return outcome, final, exam


def message(day: date, outcome: Any, final: Any, exam: dict | None, *, dry_run: bool = False) -> tuple[str, list]:
    """(subject, blocks) in the morning message's layout (research.blocks). The verdict comes first."""
    from research.blocks import Block, lesson
    if exam is not None:
        s, b = exam["strategy"], exam["baseline"]
        verdict = (f"PASSED training; exam year {s['cagr_pct']:+.1f}% against the paper rule's "
                   f"{b['cagr_pct']:+.1f}% - " + ("it beat it." if s["cagr_pct"] > b["cagr_pct"] else
                                                  "it did NOT beat it."))
    elif not [v for v in outcome.versions if v.valid]:
        verdict = "NOTHING TESTED: no version got past the checker."
    else:
        verdict = "NOTHING PASSED: no version beats the paper-traded momentum rule by more than luck."
    blocks = [Block(f"📊 Stock momentum research — {day.isoformat()}" + (" (dry run, no AI)" if dry_run else ""),
                    note=f"Verdict: {verdict}")]
    for v in outcome.versions:
        tag = f"v{v.idea_no}.{v.version_no}"
        if not v.valid:
            blocks.append(Block(f"{tag} - {v.title or '(no title)'}",
                                note=f"REJECTED by the checker: {(v.error or '').splitlines()[0][:200]}"))
            continue
        d = v.summary.as_dict()
        st, vs = d["strategy"], d["versus_baseline"]
        mark = " (the day's final version)" if final is not None and v is final[0] else ""
        rows = (("vs paper rule", f"{vs['cagr_lead_pct']:+.2f}% a year"), ("luck t", str(vs["luck_t"])),
                ("worst fall", f"{st['worst_fall_pct']:.1f}%"), ("beat NIFTY", f"{st['years_beating_pct']:.0f}% of years"),
                ("result", "PASSES" if d["passes"] else "FAILS"))
        note = " ".join([f"Why: {d['why_not']}." if d["why_not"] else "", lesson(v.review.get("lessons"))]).strip()
        blocks.append(Block(f"{tag} - {v.title}{mark}", rows, note))
    if exam is not None:
        s, b = exam["strategy"], exam["baseline"]
        span = f" {s['start']} to {s['end']}" if s.get("start") and s.get("end") else ""
        rows = [("this version", f"{s['cagr_pct']:+.1f}%"), ("paper rule", f"{b['cagr_pct']:+.1f}%")]
        if s.get("bench_cagr_pct") is not None:
            rows.append(("NIFTY 50", f"{s['bench_cagr_pct']:+.1f}%"))
        if s.get("worst_fall_pct") is not None:
            rows.append(("worst fall", f"{s['worst_fall_pct']:.1f}%"))
        blocks.append(Block(f"Exam year{span}, opened once", tuple(rows)))
    blocks.append(Block("How the day ended", (("stopped", STOPPED.get(outcome.stopped_because, outcome.stopped_because)),),
                        outcome.stop_detail[:200] if outcome.stop_detail else ""))
    return f"Stock momentum research {day.isoformat()}: {verdict.split(':')[0].split(';')[0]}"[:120], blocks


def _commit(day: date) -> None:
    try:
        subprocess.run(["git", "add", str(DIR)], check=True, capture_output=True, text=True)
        done = subprocess.run(["git", "-c", "user.name=research loop", "-c", "user.email=noreply@anthropic.com",
                               "commit", "-m", f"docs(research): rotation notes for {day}"],
                              capture_output=True, text=True)
        if done.returncode != 0 and not nothing_to_commit(done.stdout):
            print(f"WARNING: notes not committed: {done.stderr.strip() or done.stdout.strip()}", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"WARNING: notes not committed: {exc}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Run one stock rotation research day.")
    parser.add_argument("--max-versions", type=int, default=0, help="default 5")
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-save", action="store_true")
    parser.add_argument("--skip-download", action="store_true", help="use the stored Yahoo closes")
    parser.add_argument("--commit-journal", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)

    from research.brain import MODEL, Claude
    from research.loop import MAX_VERSIONS

    if not args.skip_download or not CLOSES.exists():
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        from rotation_prices import download
        closes = download()
        CLOSES.parent.mkdir(parents=True, exist_ok=True)
        closes.to_parquet(CLOSES)
    closes = pd.read_parquet(CLOSES)
    lab = Lab(closes)
    day = datetime.now(IST).date()
    print(f"day        {day}")
    print(f"training   {lab.train[0]} -> {lab.train[1]}; exam {lab.exam[0]} -> {lab.exam[1]} (opened only on a pass)")
    print(f"brain      {'dry run - no AI is asked' if args.dry_run else f'claude -p, model {MODEL}, no tools'}")
    base = stats(lab.baseline(), closes[INDEX])
    print(f"baseline   {base['cagr_pct']}%/yr on today's list (biased), worst fall {base['worst_fall_pct']}%")
    n, ideas = notes(), ideas_tried()
    brain: Any = DryBrain() if args.dry_run else RotationBrain(Claude(), baseline=base)
    outcome, final, exam = run_day(brain=brain, lab=lab, day=day, max_versions=args.max_versions or MAX_VERSIONS,
                                   budget_seconds=args.budget_seconds, notes_=n, ideas=ideas)
    subject, text = message(day, outcome, final, exam, dry_run=args.dry_run)
    print("\n" + text)
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
