"""One whole research day, end to end (design §4, revised 2026-09-16).

    python -m research.run_day
    python -m research.run_day --dry-run --max-stocks 5 --stock-timeframes day --workers 2
    python -m research.run_day --max-versions 3

Opus proposes a strategy, the checker forces the research rules onto it, the
sweep tests it on the TRAINING years, Opus reviews that summary and says what
to try next - up to five versions. Only then is the pick made and the locked
year opened, once, and the day stored with a journal note for tomorrow.

The pick is a TIMEFRAME, and the exam is the basket of every stock on it
(research.portfolio): Rs 1 lakh spread equally, judged month by month. The
earlier rule picked one stock out of ~1,177 and examined it on one trade.

Two rules shape the wiring:

* Opus sees nothing from the locked year. Every AI input is built from a
  `TrainingSummary`, and the locked year is opened after the last AI call.
* Stopping is a result. A day cut short by the Pro allowance or the time
  budget keeps the versions it finished, still opens the locked year (that
  needs no AI) and still stores a row - it just says why it stopped.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from config import IST, UTC, get_settings, use_utf8_stdout
from research.brain import MODEL, BrainError, BrainStopped
from research.evaluate import (
    _PENDING,
    _rupees,
    basket_label,
    data_end_basis,
    locked_year,
    prepare_run,
    print_locked,
)

# How many times a reply we cannot read is asked for again. The answer shape
# is asked for in words rather than enforced by the CLI (see research.brain),
# so a stray sentence around the JSON is possible, and cheap to re-ask.
BRAIN_ATTEMPTS = 3

# How much history the prompt builder is given. Thirty days of notes is what
# design §4 asks for; the idea index goes wider because its lines are short
# and repeating an idea is the waste it exists to prevent.
NOTES_LIMIT = 30
IDEAS_LIMIT = 200

FORMAT_DOCS = (Path("docs/STRATEGY_FORMAT.md"), Path("docs/STRATEGY_FORMAT_V3.md"))

PROPOSE_INSTRUCTION = (
    "Read the input and design ONE trading strategy. Answer only with the "
    "required fields."
)
REVIEW_INSTRUCTION = (
    "Read the input: it is how your strategy did on the training years. Judge "
    "it and decide what to do next. Answer only with the required fields."
)

# Why the loop ended -> what the grid calls it. 'stop' and 'version_limit' are
# the day finishing as designed; the other two are the day being cut short.
STATUS_FOR = {
    "stop": "completed",
    "version_limit": "completed",
    "time_budget": "stopped_time",
    "stopped_limit": "stopped_limit",
}

DRY_RUN_TITLE = "Dry run placeholder (no AI)"
DRY_RUN_DESCRIPTION = (
    "A plain 20/50 EMA crossover, used only to prove the wiring end to end "
    "without spending the Claude allowance."
)
DRY_RUN_HYPOTHESIS = (
    "None. This strategy is not a research idea; it is a fixed input so the "
    "day's machinery can be run without an AI call."
)
DRY_RUN_YAML = """\
position_type: long
entry:
  all:
    - indicator: ema
      params: {period: 20}
      operator: crosses_above
      compare_to: {indicator: ema, params: {period: 50}}
exit:
  any:
    - indicator: ema
      params: {period: 20}
      operator: crosses_below
      compare_to: {indicator: ema, params: {period: 50}}
risk:
  stop_loss: {type: percent, value: 2.0}
  target: {type: percent, value: 4.0}
"""


def format_documents(root: Path | None = None) -> list[str]:
    """The strategy format references Opus writes against.

    A missing file is not fatal: the day is still worth running on one format,
    and a hard failure here would waste a whole scheduled run.
    """
    base = root or Path(".")
    docs = []
    for name in FORMAT_DOCS:
        path = base / name
        if path.exists():
            docs.append(path.read_text(encoding="utf-8"))
        else:
            print(f"WARNING: {path} is missing, so Opus will not see that format",
                  file=sys.stderr)
    return docs


def idea_line(row: Mapping[str, Any]) -> str:
    """One line of the ideas index, with the numbers that decide an idea.

    Words alone let the same family come back three days running ("RSI-2
    washout in a daily uptrend", 13-15 September). The basket's average month
    and how broadly it beat holding are what would have stopped that.
    """
    name = str(row.get("strategy_name") or "")
    facts = row.get("training_summary") or {}
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except ValueError:
            facts = {}
    parts = [name]
    accounts = facts.get("accounts") or facts.get("baskets") or []
    if accounts:
        best = max(accounts, key=lambda b: b.get("avg_month_pct") or float("-inf"))
        parts.append(
            f"best account {best.get('timeframe')}: {best.get('avg_month_pct', 0):+.2f}%/month, "
            f"{best.get('months_positive_pct', 0):.0f}% months up"
            + ("" if best.get("qualifies") else " (not pickable)")
        )
    tested, beat = facts.get("combos_tested"), facts.get("combos_beating_hold")
    if tested:
        parts.append(f"{beat if beat is not None else '?'} of {tested} beat holding")
    lesson = (row.get("lessons") or "").strip()
    if lesson:
        parts.append(lesson)
    return " — ".join(parts)


def tried_ideas(client: Any, *, limit: int = IDEAS_LIMIT) -> list[str]:
    """One line per version already tried, newest first.

    A missing table means no history yet, which is the first day - not an
    error worth ending the run over.
    """
    try:
        response = (
            client.table("research_versions")
            .select("strategy_name,lessons,training_summary,created_at")
            .order("created_at", desc=True).limit(limit).execute()
        )
    except Exception:       # noqa: BLE001 - no history is a normal first day
        return []
    lines: list[str] = []
    seen: set[str] = set()
    for row in getattr(response, "data", None) or []:
        name = row.get("strategy_name")
        if not name or name in seen:
            continue
        seen.add(name)
        lines.append(idea_line(row))
    return lines


class OpusBrain:
    """The two calls the loop makes, each one `claude -p` with no tools."""

    def __init__(
        self, claude: Any, formats: Sequence[str], *, echo: Any = print,
        atlas: Sequence[str] = (),
    ) -> None:
        self._claude, self._formats, self._echo = claude, formats, echo
        self._atlas = list(atlas)

    def _ask(self, prompt: str, schema: dict[str, Any], instruction: str) -> dict[str, Any]:
        """Ask once, and again if the reply cannot be read.

        One unreadable reply is a stray sentence around the JSON. Three in a
        row is the day being over: raised as a stop so the versions already
        finished are kept and the locked year is still opened, rather than as
        a crash that would throw the morning away.
        """
        last: BrainError | None = None
        for attempt in range(1, BRAIN_ATTEMPTS + 1):
            try:
                return self._claude.ask(prompt, schema, instruction=instruction)
            except BrainError as exc:
                last = exc
                self._echo(f"  unreadable reply ({attempt}/{BRAIN_ATTEMPTS}): {str(exc)[:160]}")
        raise BrainStopped(f"Claude's reply could not be read {BRAIN_ATTEMPTS} times: {last}")

    def propose(self, *, notes, ideas_tried, previous, change_hint, error) -> dict[str, Any]:
        from research.prompts import PROPOSE_SCHEMA, propose_prompt

        if error:
            self._echo(f"  repairing: {error.splitlines()[0][:140]}")
        prompt = propose_prompt(
            formats=self._formats, notes=notes, ideas_tried=ideas_tried,
            previous=previous, change_hint=change_hint, error=error, atlas=self._atlas,
        )
        return self._ask(prompt, PROPOSE_SCHEMA, PROPOSE_INSTRUCTION)

    def review(self, *, summary, version, versions_left) -> dict[str, Any]:
        from research.prompts import REVIEW_SCHEMA, review_prompt

        prompt = review_prompt(summary=summary, version=version, versions_left=versions_left)
        return self._ask(prompt, REVIEW_SCHEMA, REVIEW_INSTRUCTION)


class DryBrain:
    """No AI at all: one fixed strategy, one fixed review, then stop.

    This is what `--dry-run` uses. It exercises every step of the day except
    the two that cost money, so the wiring can be proven for free.
    """

    def propose(self, **_: Any) -> dict[str, Any]:
        return {
            "title": DRY_RUN_TITLE,
            "description": DRY_RUN_DESCRIPTION,
            "hypothesis": DRY_RUN_HYPOTHESIS,
            "strategy_yaml": DRY_RUN_YAML,
            "change_note": "",
        }

    def review(self, **_: Any) -> dict[str, Any]:
        return {
            "why_failed": "",
            "why_worked": "",
            "lessons": "Dry run: no AI was asked, so there is nothing learned.",
            "decision": "stop",
        }


def choose_final(tested: Sequence[tuple[Any, Any]], score: Any) -> tuple[Any, Any] | None:
    """The version the day ends on.

    The last version whose review said `stop` is Opus's own choice and wins.
    With no such review - the allowance ran out, or the clock did - the best
    training score by the fixed pick rule stands in, which needs no AI. The
    score IS the pick rule (research.picker.best_score): the 15 September run
    scored by yearly return instead, and chose the version that had merely
    risen most.
    """
    stopped = [pair for pair in tested if pair[0].review.get("decision") == "stop"]
    if stopped:
        # Opus may name an earlier version of the day as the better one.
        named = str(stopped[-1][0].review.get("final_version") or "").strip().lower()
        for pair in tested:
            if named and named == f"v{pair[0].idea_no}.{pair[0].version_no}":
                return pair
        return stopped[-1]
    if not tested:
        return None
    return max(tested, key=lambda pair: score(pair[1]))


def main(argv: list[str] | None = None) -> int:        # noqa: PLR0915 - one day, in order
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Run one whole research day.")
    parser.add_argument("--max-versions", type=int, default=0,
                        help="how many versions the day may try (default 5)")
    parser.add_argument("--budget-seconds", type=float, default=0.0,
                        help="stop starting versions after this long (default 5 hours)")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--max-stocks", type=int, default=0,
                        help="test only the first N stocks (quick run)")
    parser.add_argument("--stock-timeframes", default="",
                        help="comma-separated; overrides universe.SWEEP_TIMEFRAMES (daily "
                             "only). Any bar under a day moves DATA_END back to where the "
                             "5-minute sessions are whole")
    parser.add_argument("--no-save", action="store_true",
                        help="print the day without storing anything")
    parser.add_argument("--dry-run", action="store_true",
                        help="skip every AI call and use one fixed built-in strategy")
    parser.add_argument("--run-id-file", default="",
                        help="write the stored run's id here, for the next step")
    parser.add_argument("--fallback-file", default="",
                        help="write the whole day here if the database refuses it")
    parser.add_argument("--commit-journal", action="store_true",
                        help="git-commit the journal note (what the scheduled run does)")
    args = parser.parse_args(argv)
    started_at = datetime.now(UTC)

    from costs import HoldingCostModel
    from db import SupabaseStore
    from research.brain import Claude
    from research.journal import entries_from_versions, note_rows, write_journal
    from research.loop import DEFAULT_BUDGET_SECONDS, MAX_VERSIONS, run_versions
    from research.checker import check_proposal
    from research.picker import best_score, pick_timeframe
    from research.records import (
        MAX_STORED_LOCKED_TRADES,
        basket_trade_rows,
        combo_rows,
        equity_rows,
        run_row,
    )
    from research.store import (
        ResearchStoreError,
        recent_notes,
        save_notes,
        save_research_strategy,
        save_run,
        save_versions,
    )
    from research.summary import build_summary
    from research.sweep import count_results, run_sweep
    from research.universe import (
        SWEEP_TIMEFRAMES,
        document_uses_volume,
        index_timeframes_within,
        research_combos,
    )
    from universes import newest_snapshot, parse_constituent_csv

    max_versions = args.max_versions or MAX_VERSIONS
    budget_seconds = args.budget_seconds or DEFAULT_BUDGET_SECONDS

    settings = get_settings(require_supabase=True)
    store = SupabaseStore.connect(settings)

    snapshot_path, as_of = newest_snapshot("NIFTY200")
    stocks = list(parse_constituent_csv(snapshot_path.read_text(encoding="utf-8")))
    if args.max_stocks:
        stocks = stocks[: args.max_stocks]
    timeframes = tuple(t.strip() for t in args.stock_timeframes.split(",") if t.strip()) \
        or SWEEP_TIMEFRAMES

    try:
        reader, windows, data_end, symbol_ends = prepare_run(settings, store, stocks,
                                                             timeframes=timeframes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    cost_model = HoldingCostModel()
    day = datetime.now(IST).date()

    def window_days_for(r) -> int:
        return windows.training_days(
            is_index=r.is_index, timeframe=r.timeframe,
            data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
        )

    print(f"day        {day}")
    print(f"prices     frozen at {data_end} (DATA_END from {data_end_basis(timeframes)}, "
          f"complete for {len(symbol_ends)} of {len(stocks)} stocks)")
    print(f"bars       {', '.join(timeframes)}")
    print(f"training   before {windows.locked_from}")
    print(f"locked     {windows.locked_from} -> {data_end}  (opened once, at the end)")
    print(f"universe   NIFTY200 as of {as_of} ({len(stocks)} stocks)")
    print(f"brain      {'dry run - no AI is asked' if args.dry_run else f'claude -p, model {MODEL}, no tools'}")
    print(f"versions   up to {max_versions}, budget {budget_seconds / 3600:.1f} h, "
          f"{args.workers} worker(s)")

    from research.atlas import atlas_lines

    notes = recent_notes(store._client, limit=NOTES_LIMIT)
    ideas = tried_ideas(store._client)
    atlas = atlas_lines(store._client)
    print(f"history    {len(notes)} note(s), {len(ideas)} idea(s) already tried, "
          f"{len(atlas)} baseline(s) in the atlas")

    brain: Any = (DryBrain() if args.dry_run
                  else OpusBrain(Claude(), format_documents(), atlas=atlas))

    # The sweep of each valid version, in the order the loop tested them. The
    # loop's own valid versions come out in that same order, so the two zip
    # together - and the pick needs the FINAL version's own results, not a
    # summary of them.
    sweeps: list[Any] = []

    def test(checked: Any, version_no: int) -> Any:
        combos = research_combos(
            stocks,
            include_indexes=not document_uses_volume(checked.document),
            stock_timeframes=timeframes,
            index_timeframes=index_timeframes_within(timeframes),
        )
        print(f"  testing {len(combos)} combinations...", flush=True)
        started = datetime.now(UTC)
        results = run_sweep(
            checked.strategy, combos, reader,
            lambda c: windows.training(is_index=c.is_index, timeframe=c.timeframe),
            slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
        )
        elapsed = (datetime.now(UTC) - started).total_seconds()
        sweeps.append(results)
        summary = build_summary(results, cost_model, window_days_for=window_days_for)
        print(f"  training: {summary.combos_profitable} of {summary.combos_tested} profitable "
              f"after fees, {summary.combos_beating_hold} beat holding, "
              f"{summary.total_trades} trades, net {_rupees(summary.net_pnl)}  "
              f"({elapsed:.0f}s)")
        for b in summary.accounts:
            print(f"    account {b['timeframe']:4s} {b['avg_month_pct']:+6.2f}%/month · "
                  f"{b['months_positive_pct']:3.0f}% up · {b['trades_per_month']:6.1f} trades/month"
                  + ("" if b["qualifies"] else f" · not pickable: {b['why_not']}"))
        return summary

    outcome = run_versions(
        # The checker validates against the bars this day actually sweeps: a
        # rule that cannot run on them goes back to Opus instead of being tested.
        brain=brain, check=functools.partial(check_proposal, timeframes=timeframes),
        test=test, day=day,
        notes=notes, ideas_tried=ideas,
        max_versions=max_versions, budget_seconds=budget_seconds,
    )

    status = STATUS_FOR.get(outcome.stopped_because, "completed")
    print(f"\nVERSIONS   {len(outcome.versions)} tried, {outcome.repairs} repair(s), "
          f"{outcome.ideas_dropped} idea(s) dropped · stopped: {outcome.stopped_because}")
    if outcome.stop_detail:
        # The day was cut short rather than finished. Everything below still
        # runs: the locked year needs no AI.
        print(f"           Claude stopped the day: {outcome.stop_detail}")
    for v in outcome.versions:
        head = f"  v{v.idea_no}.{v.version_no}  {v.title or '(no title)'}"
        if not v.valid:
            print(f"{head}  — REJECTED: {(v.error or '').splitlines()[0][:120]}")
            continue
        decision = v.review.get("decision", "(no review)")
        print(f"{head}  — {decision}")
        if v.review.get("lessons"):
            print(f"      lesson: {v.review['lessons'][:200]}")

    tested = list(zip([v for v in outcome.versions if v.valid], sweeps))
    final = choose_final(tested, best_score)

    # Every version goes to the library, paused, with the story it came from -
    # including the ones this day did not choose, because tomorrow's index of
    # ideas already tried is built from exactly these rows.
    version_ids: dict[int, int | None] = {}
    if not args.no_save:
        for v, _ in tested:
            try:
                version_ids[id(v)] = save_research_strategy(
                    store, v.checked.document,
                    title=v.title, description=v.description, hypothesis=v.hypothesis,
                )
            except ResearchStoreError as exc:
                print(f"WARNING: {exc}", file=sys.stderr)

    warnings = [
        f"prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)",
    ]
    if outcome.stopped_because in ("time_budget", "stopped_limit"):
        warnings.append(f"the day was cut short ({outcome.stopped_because}); "
                        "the final version was chosen by the pick rule, not by Opus")

    def store_day(*, results, pick, exam, counts) -> int | None:
        """Write the run, its versions and its notes. Returns the run id."""
        final_version = final[0] if final else None
        run = run_row(
            started_at=started_at, finished_at=datetime.now(UTC), status=status,
            data_end=data_end, locked_from=windows.locked_from,
            strategy_name=(final_version.checked.document["name"] if final_version else None),
            final_version_id=version_ids.get(id(final_version)) if final_version else None,
            pick_symbol=None if exam is None else basket_label(exam.account),
            pick_timeframe=None if pick is None else pick.timeframe,
            locked=None if exam is None else exam.lakh,
            hold_end_value=None if exam is None else exam.hold_end_value,
            combos_profitable=counts.profitable, combos_tested=counts.tested,
            versions_tried=len(outcome.versions), ideas_dropped=outcome.ideas_dropped,
            ai_review=(final_version.review.get("lessons") if final_version else None),
            trigger="dry_run" if args.dry_run else "manual",
            warnings=warnings,
            basket=None if exam is None else exam.account,
            training_basket=None if pick is None else pick.account,
        )
        children: dict[str, Any] = {}
        if results is not None:
            children["combos"] = combo_rows(_PENDING, results, cost_model,
                                            window_days_for=window_days_for)
        if exam is not None:
            children["locked_trades"] = basket_trade_rows(
                _PENDING, exam.trades, stocks=exam.account.slots)
            children["equity"] = equity_rows(_PENDING, exam.equity)
        version_rows = [
            {
                "idea_no": v.idea_no, "version_no": v.version_no,
                "strategy_name": (v.checked.document["name"] if v.checked else None),
                "strategy_version_id": version_ids.get(id(v)),
                "valid": v.valid, "error": v.error, "change_note": v.change_note,
                "why_failed": v.review.get("why_failed"),
                "why_worked": v.review.get("why_worked"),
                "lessons": v.review.get("lessons"),
                "decision": v.review.get("decision"),
                "training_summary": v.summary.as_dict() if v.summary is not None else None,
            }
            for v in outcome.versions
        ]
        # Built before the first network call, so a refused write still has
        # something complete to hand back.
        payload = {
            "run": dict(run),
            "versions": version_rows,
            "notes": note_rows(_PENDING, day, entries),
            "combos": len(children.get("combos", [])),
        }

        try:
            run_id = save_run(store._client, run=run, **children)
            save_versions(store._client, run_id, version_rows)
            save_notes(store._client, note_rows(run_id, day, entries))
        except ResearchStoreError:
            write_fallback(args.fallback_file, payload)
            raise
        return run_id

    def finish(*, results, pick, exam, counts) -> int:
        """Journal, commit, store, and report - the same tail for every ending."""
        print("\nWARNINGS")
        for line in warnings:
            print(f"  {line}")
        print(f"\njournal    {journal_path}")
        if args.commit_journal:
            commit_journal(journal_path, day)
        if args.no_save:
            print("\nnot saved (--no-save)")
            return 0
        try:
            saved = store_day(results=results, pick=pick, exam=exam, counts=counts)
        except ResearchStoreError as exc:
            print(f"\nWARNING: the day was NOT stored: {exc}", file=sys.stderr)
            return 1
        write_run_id(args.run_id_file, saved)
        print(f"\nsaved as run {saved}")
        return 0

    entries = entries_from_versions(outcome.versions)
    journal_path = write_journal(day, entries)

    if final is None:
        print("\nNo version survived the checker, so there is nothing to pick from. "
              "Locked year not opened.")
        return finish(results=None, pick=None, exam=None, counts=count_results([]))

    final_version, final_results = final
    counts = count_results(final_results)
    strategy_name = final_version.checked.document["name"]
    print(f"\nFINAL      {strategy_name}  ({final_version.title})")
    print(f"           {counts.tested} tested, {counts.skipped} skipped, "
          f"{counts.profitable} profitable after fees")

    pick = pick_timeframe(final_results)
    if pick is None:
        print("\nNo qualifying timeframe: no basket beat holding while trading 10+ times a "
              "month within a 30% dip. Locked year not opened.")
        warnings.append(f"no qualifying timeframe among {counts.tested} combinations")
        return finish(results=final_results, pick=None, exam=None, counts=counts)

    b = pick.account
    print(f"\nPICK       {pick.timeframe} account over {b.stocks} stocks  (training: "
          f"{b.avg_month_pct:+.2f}%/month, {b.months_positive_pct:.0f}% months up, "
          f"worst dip {b.worst_dip_pct:.1f}%)")

    # The first and only time the locked year is opened, and the last AI call
    # is already behind us.
    strategy = final_version.checked.strategy
    exam = locked_year(
        strategy, pick.timeframe, stocks, reader, windows,
        slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
    )
    if exam is None:
        print("\nThe basket has no locked-year data. Locked year not opened.")
        warnings.append("the picked basket had no locked-year data")
        return finish(results=final_results, pick=pick, exam=None, counts=counts)

    warnings.extend(print_locked(exam, is_short=strategy.position_type == "short",
                                 data_end=data_end))
    print(f"  broad or lucky: profitable in {counts.profitable} of {counts.tested} "
          "training combinations")
    if len(exam.trades) > MAX_STORED_LOCKED_TRADES:
        warnings.append(f"{len(exam.trades)} locked-year trades: too many to store, "
                        "the daily balance is stored instead")
    warnings.append(f"{counts.tested} combinations tried: some look good in training by luck alone")
    return finish(results=final_results, pick=pick, exam=exam, counts=counts)


def write_fallback(path: str, payload: Mapping[str, Any]) -> None:
    """The whole day as JSON, for when the database will not take it.

    Forty minutes of sweeping and six Opus calls are not worth losing to a
    transient network error. The workflow keeps this as an artifact, and the
    message says the run stored nothing (design 8).
    """
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(payload), indent=2, default=str), encoding="utf-8")


def write_run_id(path: str, run_id: str | None) -> None:
    """Leave the run id where the next workflow step can read it.

    Scraping "saved as run <id>" out of stdout would work until a warning
    line moved.
    """
    if not path or not run_id:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(str(run_id), encoding="utf-8")


# Git says "there was nothing to do" in several different ways, and none of
# them is a problem worth waking someone at 6am for. An unchanged note is the
# normal case when a day is re-run.
_NOTHING_TO_COMMIT = (
    "nothing to commit",
    "nothing added to commit",
    "no changes added to commit",
    "working tree clean",
)


def nothing_to_commit(output: str) -> bool:
    """Did `git commit` decline because there was no change to record?"""
    lowered = (output or "").lower()
    return any(phrase in lowered for phrase in _NOTHING_TO_COMMIT)


def commit_journal(path: Path, day: Any) -> None:
    """Commit the note. Also what keeps GitHub from disabling the schedule.

    A failure here is reported and shrugged off: the day's real output is in
    Supabase, and a dirty working tree is not worth losing the run over.
    """
    try:
        subprocess.run(["git", "add", str(path)], check=True, capture_output=True, text=True)
        # A runner has no git identity and `git commit` refuses without one.
        # Passed per command with -c rather than written with `git config`,
        # which would permanently overwrite the identity in whatever
        # repository this happens to run in - including a laptop's.
        done = subprocess.run(
            ["git", "-c", "user.name=research loop",
             "-c", "user.email=noreply@anthropic.com",
             "commit", "-m", f"docs(research): journal note for {day}"],
            capture_output=True, text=True,
        )
        if done.returncode != 0 and not nothing_to_commit(done.stdout):
            print(f"WARNING: the journal note was not committed: "
                  f"{done.stderr.strip() or done.stdout.strip()}", file=sys.stderr)
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"WARNING: the journal note was not committed: {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
