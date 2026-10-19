"""One options research day, end to end (design 2026-10-19-fno-ai-research §1).

    python -m fno.ai.day
    python -m fno.ai.day --dry-run --no-save          # no AI: proves the wiring for free
    python -m fno.ai.day --max-versions 3 --notify --commit-journal

The AI proposes an options strategy, the checker forces the research rules
onto it, the engine tests it on NIFTY and BANKNIFTY over the TRAINING years,
the AI reviews that and says what to try next - up to five versions. The
loop, the Claude call and the choice of the final version are the stock
research's own (research.loop, research.brain, research.run_day.choose_final).

Only if the final version passes the options rule on an index is that index's
exam year opened, once, after the last AI call. Nothing from it is written
anywhere a later prompt reads (fno.ai.ledger).
"""

from __future__ import annotations

import argparse
import html
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from config import IST, use_utf8_stdout
from fno import atlas, report, rules, store
from fno.ai import ledger, prompts
from fno.ai.checker import check_proposal
from fno.ai.summary import OptionsSummary, index_summary, one_line, score
from research.run_day import OpusBrain, choose_final, nothing_to_commit

DRY_RUN_TITLE = "Dry run placeholder (no AI)"
DRY_RUN_STRATEGY = "short-strangle-20d"

PROPOSE_INSTRUCTION = ("Read the input and design ONE options trading strategy. Answer only with "
                       "the required fields.")
REVIEW_INSTRUCTION = ("Read the input: it is how your options strategy did on the training years. "
                      "Judge it and decide what to do next. Answer only with the required fields.")
BUDGET_SECONDS = 4 * 60 * 60

STOPPED = {"stop": "the AI chose to stop", "version_limit": "all versions used",
           "time_budget": "the time budget ran out", "stopped_limit": "Claude stopped the day"}


class Lab:
    """The engine on stored data: a strategy's training reading, or its exam year.

    Markets are loaded once per index and mode, cut at the training window's
    end for training - so a training run cannot read the exam year at all.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._markets: dict[tuple[str, str, bool], Any] = {}

    def _sessions(self, underlying: str) -> list[date]:
        from fno.market import stored_sessions
        return stored_sessions(self.root, underlying)

    def _market(self, underlying: str, mode: str, exam: bool) -> Any:
        from fno.intraday_market import IntradayMarket
        from fno.market import MarketData, training_window
        key = (underlying, mode, exam)
        if key not in self._markets:
            until = None if exam else training_window(self._sessions(underlying))[1]
            loader = IntradayMarket if mode == "intraday" else MarketData
            self._markets[key] = loader.load(self.root, underlying, until=until)
        return self._markets[key]

    def window(self, underlying: str, mode: str, *, exam: bool = False) -> tuple[date, date]:
        from fno.market import training_window
        sessions = self._sessions(underlying)
        start, end = training_window(sessions)
        if exam:
            after = [d for d in sessions if d > end]
            if not after:
                raise ValueError(f"{underlying}: no exam-year sessions stored")
            return after[0], after[-1]
        if mode == "intraday":
            first = self._market(underlying, mode, False).first_day
            if first is None:
                raise ValueError(f"{underlying}: no option minutes stored")
            start = max(start, first)
        return start, end

    def run(self, strategy: Any, *, exam: bool = False) -> tuple[Any, int]:
        """(report, sessions in the window) for one strategy on its own underlying."""
        from fno import intraday
        from fno.engine import run as run_positional
        market = self._market(strategy.underlying, strategy.mode, exam)
        start, end = self.window(strategy.underlying, strategy.mode, exam=exam)
        if exam and strategy.mode == "intraday":
            start = max(start, market.first_day or start)
        runner = intraday.run if strategy.mode == "intraday" else run_positional
        result = runner(strategy, market, start=start, end=end)
        daily = market.daily if strategy.mode == "intraday" else market
        return report.build(result, daily.closes()), len(daily.sessions(start, end))


def tester(lab: Any, *, echo: Callable[..., None] = print) -> Callable[[Any, int], OptionsSummary]:
    """The loop's `test`: the checked proposal on every index, training years only."""
    def test(checked: Any, version_no: int) -> OptionsSummary:
        rows = []
        for underlying in atlas.UNDERLYINGS:
            strategy = checked.for_index(underlying)
            started = datetime.now()
            try:
                built, sessions = lab.run(strategy)
            except Exception as exc:       # noqa: BLE001 - a result for the AI, not a crash
                rows.append({"underlying": underlying, "mode": checked.mode, "passes": False,
                             "why_not": f"the engine could not run it: {type(exc).__name__}: {exc}",
                             "avg_month_pct": None, "trades_per_month": 0.0, "worst_day_pct": 0.0,
                             "edge_t": None, "warning": ""})
                echo(f"    {underlying}: could not run: {exc}", flush=True)
                continue
            rows.append(index_summary(built, mode=checked.mode,
                                      why_not=rules.disqualified(built, checked.mode), sessions=sessions))
            echo(f"    {underlying} ({(datetime.now() - started).total_seconds():.0f}s): "
                 f"{one_line(OptionsSummary(checked.mode, rows[-1:]))}", flush=True)
        return OptionsSummary(checked.mode, rows)
    return test


class OptionsBrain(OpusBrain):
    """The stock brain's retrying `claude -p` call, asked the options questions."""

    def __init__(self, claude: Any, *, format_doc: str, atlas_lines: Sequence[str],
                 echo: Any = print) -> None:
        super().__init__(claude, [format_doc], echo=echo, atlas=atlas_lines)
        self._format_doc = format_doc

    def propose(self, *, notes, ideas_tried, previous, change_hint, error) -> dict[str, Any]:
        if error:
            self._echo(f"  repairing: {error.splitlines()[0][:140]}")
        prompt = prompts.propose_prompt(format_doc=self._format_doc, atlas=self._atlas, notes=notes,
                                        ideas_tried=ideas_tried, previous=previous,
                                        change_hint=change_hint, error=error)
        return self._ask(prompt, prompts.PROPOSE_SCHEMA, PROPOSE_INSTRUCTION)

    def review(self, *, summary, version, versions_left) -> dict[str, Any]:
        prompt = prompts.review_prompt(summary=summary, version=version, versions_left=versions_left)
        return self._ask(prompt, prompts.REVIEW_SCHEMA, REVIEW_INSTRUCTION)


class DryBrain:
    """No AI: one atlas strategy, one fixed review, then stop."""

    def propose(self, **_: Any) -> dict[str, Any]:
        return {"title": DRY_RUN_TITLE, "description": "The positional atlas's 20-delta strangle.",
                "hypothesis": "None: a fixed input that proves the day's wiring without an AI call.",
                "strategy_yaml": atlas.document(DRY_RUN_STRATEGY, "NIFTY"), "change_note": ""}

    def review(self, **_: Any) -> dict[str, Any]:
        return {"why_failed": "", "why_worked": "", "decision": "stop",
                "lessons": "Dry run: no AI was asked, so nothing was learned."}


def exam_row(day: date, version: Any, pick: Any, built: Any, sessions: int) -> dict[str, Any]:
    h = built.headline
    return {"day": day.isoformat(), "strategy_name": version.checked.document["name"],
            "title": version.title, "underlying": pick["underlying"], "mode": version.checked.mode,
            "start": built.start, "end": built.end, "sessions": sessions, "skipped": built.skipped,
            **{k: h.get(k) for k in ("avg_month_pct", "months_positive_pct", "worst_day_pct",
                                     "worst_fall_pct", "trades", "trades_per_month", "win_rate_pct",
                                     "end_value", "avg_holding_month_pct")},
            "beat_holding": (h.get("avg_month_pct") or 0) > (h.get("avg_holding_month_pct") or 0)}


def message(day: date, outcome: Any, final: Any, pick: Any, exam: dict[str, Any] | None,
            *, dry_run: bool = False) -> tuple[str, str]:
    """(subject, plain text). The verdict comes first, in one line."""
    if exam is not None:
        verdict = (f"PASSED training on {pick['underlying']}; exam year "
                   f"{exam['avg_month_pct']:+.2f}%/month against holding's "
                   f"{exam['avg_holding_month_pct']:+.2f}% - "
                   + ("it beat holding." if exam["beat_holding"] else "it did NOT beat holding."))
    elif not [v for v in outcome.versions if v.valid]:
        verdict = "NOTHING TESTED: no version got past the checker."
    else:
        verdict = "NOTHING PASSED: no version passes the rule on NIFTY or BANKNIFTY. Not usable."
    lines = [f"Options research {day.isoformat()}" + (" (dry run, no AI)" if dry_run else ""),
             f"Verdict: {verdict}", ""]
    for v in outcome.versions:
        mark = " <- final" if final is not None and v is final[0] else ""
        if not v.valid:
            lines.append(f"v{v.idea_no}.{v.version_no} {v.title or '(no title)'}: REJECTED - "
                         f"{(v.error or '').splitlines()[0][:160]}")
            continue
        lines.append(f"v{v.idea_no}.{v.version_no} {v.title} ({v.checked.mode}){mark}")
        lines.append(f"  {one_line(v.summary)}")
        if v.review.get("lessons"):
            lines.append(f"  lesson: {v.review['lessons'][:300]}")
    lines += ["", f"Stopped: {STOPPED.get(outcome.stopped_because, outcome.stopped_because)}"
              + (f" ({outcome.stop_detail[:200]})" if outcome.stop_detail else "")]
    if exam is not None:
        lines.append(f"Exam year {exam['start']} to {exam['end']}: {exam['trades']} trades, "
                     f"worst day {exam['worst_day_pct']:+.1f}%, worst fall {exam['worst_fall_pct']:.1f}%, "
                     f"Rs 5 lakh became Rs {exam['end_value']:,.0f}.")
    subject = f"Options research {day.isoformat()}: {verdict.split(':')[0].split(';')[0]}"
    return subject[:120], "\n".join(lines)


def send(subject: str, text: str) -> None:
    from research.notify import send_all, token_warning

    def pair(*names: str) -> tuple[str, ...] | None:
        values = tuple((os.environ.get(n) or "").strip() for n in names)
        return values if all(values) else None

    warning = token_warning(os.environ.get("CLAUDE_TOKEN_CREATED", ""))
    body = f"{text}\n{warning}" if warning else text
    head, _, rest = body.partition("\n")
    tg = f"<b>{html.escape(head)}</b>\n<pre>{html.escape(rest)[:3800]}</pre>"
    results = send_all(tg, subject, body, telegram=pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                       email=pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"))
    for channel, result in results.items():
        print(f"{channel:9s} {'sent' if result.sent else result.detail}")


def commit(day: date) -> None:
    """Commit the options notes, ledger and exams. A failure is reported, not fatal."""
    try:
        subprocess.run(["git", "add", str(ledger.DIR)], check=True, capture_output=True, text=True)
        done = subprocess.run(["git", "-c", "user.name=research loop", "-c", "user.email=noreply@anthropic.com",
                               "commit", "-m", f"docs(research): options notes for {day}"],
                              capture_output=True, text=True)
        if done.returncode != 0 and not nothing_to_commit(done.stdout):
            print(f"WARNING: the options notes were not committed: {done.stderr.strip() or done.stdout.strip()}",
                  file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"WARNING: the options notes were not committed: {exc}", file=sys.stderr)


def run_day(*, brain: Any, lab: Any, day: date, max_versions: int, budget_seconds: float,
            notes: Sequence[dict[str, Any]] = (), ideas: Sequence[str] = (),
            echo: Callable[..., None] = print) -> tuple[Any, Any, Any, dict[str, Any] | None]:
    """The day's research: (loop outcome, final (version, summary), pick, exam row)."""
    from research.loop import run_versions
    outcome = run_versions(brain=brain, check=check_proposal, test=tester(lab, echo=echo), day=day,
                           notes=notes, ideas_tried=ideas, max_versions=max_versions,
                           budget_seconds=budget_seconds)
    tested = [(v, v.summary) for v in outcome.versions if v.valid]
    final = choose_final(tested, score)
    pick = final[1].pick() if final is not None else None
    exam = None
    if pick is not None:
        # The first and only time the exam year is opened; the last AI call is behind us.
        version = final[0]
        built, sessions = lab.run(version.checked.for_index(pick["underlying"]), exam=True)
        exam = exam_row(day, version, pick, built, sessions)
    return outcome, final, pick, exam


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Run one options research day.")
    parser.add_argument("--max-versions", type=int, default=0, help="default 5")
    parser.add_argument("--budget-seconds", type=float, default=BUDGET_SECONDS)
    parser.add_argument("--dry-run", action="store_true", help="no AI: one fixed atlas strategy")
    parser.add_argument("--no-save", action="store_true", help="write no ledger, note or exam")
    parser.add_argument("--commit-journal", action="store_true")
    parser.add_argument("--notify", action="store_true", help="send the Telegram / email message")
    args = parser.parse_args(argv)

    from research.brain import MODEL, Claude
    from research.loop import MAX_VERSIONS

    root = store.root_dir()
    day = datetime.now(IST).date()
    lab = Lab(root)
    notes, ideas = ledger.notes(), ledger.ideas_tried()
    baselines = prompts.atlas_lines(root)
    print(f"day        {day}")
    for u in atlas.UNDERLYINGS:
        start, end = lab.window(u, "positional")
        print(f"{u:10s} training {start} -> {end}; exam year opened only for a passing version")
    print(f"brain      {'dry run - no AI is asked' if args.dry_run else f'claude -p, model {MODEL}, no tools'}")
    print(f"history    {len(notes)} note(s), {len(ideas)} idea(s) tried, {len(baselines)} baseline(s)")

    brain: Any = (DryBrain() if args.dry_run else
                  OptionsBrain(Claude(), format_doc=prompts.format_reference(), atlas_lines=baselines))
    outcome, final, pick, exam = run_day(brain=brain, lab=lab, day=day,
                                         max_versions=args.max_versions or MAX_VERSIONS,
                                         budget_seconds=args.budget_seconds, notes=notes, ideas=ideas)
    subject, text = message(day, outcome, final, pick, exam, dry_run=args.dry_run)
    print("\n" + text)
    if not args.no_save:
        ledger.append_versions(ledger.version_rows(day, outcome.versions))
        print(f"\njournal    {ledger.write_note(day, outcome.versions)}")
        if exam is not None:
            ledger.append_exam(exam)
        if args.commit_journal:
            commit(day)
    if args.notify:
        send(subject, text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
