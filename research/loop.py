"""The version loop: propose, check, test, review - until something stops it.

Pure by injection: the brain, the checker and the tester are parameters, so
every limit and decision here is tested with fakes and no network, no Claude
and no candles.

Stopping is a first-class result, not an exception. A day that ran out of
versions, time or allowance still has versions worth keeping, and the caller
evaluates the locked year regardless (design 8).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from research.brain import BrainStopped

# Five, not seven. Seven versions is fourteen Opus calls, which exhausted
# the owner's shared Pro allowance mid-run on 2026-09-15 and left the day
# falling back to a training-score choice. Five leaves headroom for his
# own use of Claude.
MAX_VERSIONS = 5
REPAIR_ATTEMPTS = 3
DEFAULT_BUDGET_SECONDS = 5 * 60 * 60


@dataclass
class VersionAttempt:
    idea_no: int
    version_no: int
    title: str
    description: str
    hypothesis: str
    change_note: str
    valid: bool
    error: str | None = None
    checked: Any = None
    summary: Any = None
    review: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopOutcome:
    versions: list[VersionAttempt]
    stopped_because: str
    repairs: int = 0
    ideas_dropped: int = 0
    # What the brain said when it refused - a usage limit, or a dead
    # credential. Empty for every ordinary ending.
    stop_detail: str = ""


def run_versions(
    *,
    brain: Any,
    check: Callable[..., Any],
    test: Callable[[Any, int], Any],
    day: date,
    notes: Sequence[dict[str, Any]] = (),
    ideas_tried: Sequence[str] = (),
    max_versions: int = MAX_VERSIONS,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> LoopOutcome:
    """Run versions until Opus stops, the count runs out, or time does."""
    started = now()
    versions: list[VersionAttempt] = []
    repairs = 0
    ideas_dropped = 0
    idea_no = 1
    version_no = 1
    previous_summary = None
    change_hint: str | None = None
    slowest = 0.0

    def cut_short(exc: BrainStopped) -> LoopOutcome:
        """The allowance or the credential gave out. Keep what is finished.

        The versions already tested are real results, and the locked year
        needs no AI, so the caller carries on with them (design 8).
        """
        return LoopOutcome(versions, "stopped_limit", repairs, ideas_dropped, str(exc))

    while True:
        if len(versions) >= max_versions:
            return LoopOutcome(versions, "version_limit", repairs, ideas_dropped)
        # Stop BEFORE starting a version that would not finish: a version takes
        # about as long as the slowest one so far, and being cut off mid-sweep
        # would waste the whole version.
        if versions and (now() - started) + slowest > budget_seconds:
            return LoopOutcome(versions, "time_budget", repairs, ideas_dropped)

        version_started = now()
        error: str | None = None
        checked = None
        proposal: dict[str, Any] = {}

        for attempt in range(REPAIR_ATTEMPTS + 1):
            try:
                proposal = brain.propose(
                    notes=notes, ideas_tried=ideas_tried, previous=previous_summary,
                    change_hint=change_hint, error=error,
                )
            except BrainStopped as exc:
                return cut_short(exc)
            try:
                checked = check(
                    proposal.get("strategy_yaml", ""),
                    idea=proposal.get("title", "idea"), day=day, version=version_no,
                )
                error = None
                break
            except Exception as exc:        # noqa: BLE001 - sent back to Opus
                error = str(exc)
                checked = None
                if attempt < REPAIR_ATTEMPTS:
                    repairs += 1

        attempt_row = VersionAttempt(
            idea_no=idea_no, version_no=version_no,
            title=proposal.get("title", ""), description=proposal.get("description", ""),
            hypothesis=proposal.get("hypothesis", ""), change_note=proposal.get("change_note", ""),
            valid=checked is not None, error=error, checked=checked,
        )

        if checked is None:
            # A version that never became a strategy still counts toward the
            # limit: an idea Opus cannot express is a result about the idea.
            versions.append(attempt_row)
            version_no += 1
            previous_summary = None
            change_hint = None
            continue

        attempt_row.summary = test(checked, version_no)
        versions.append(attempt_row)
        slowest = max(slowest, now() - version_started)

        versions_left = max_versions - len(versions)
        try:
            review = brain.review(
                summary=attempt_row.summary, version=len(versions), versions_left=versions_left,
            )
        except BrainStopped as exc:
            return cut_short(exc)
        attempt_row.review = review
        decision = review.get("decision", "stop")

        if decision == "stop" or versions_left <= 0:
            because = "stop" if decision == "stop" else "version_limit"
            return LoopOutcome(versions, because, repairs, ideas_dropped)
        if decision == "new_idea":
            ideas_dropped += 1
            idea_no += 1
            version_no = 1
            previous_summary = None
            change_hint = None
        else:
            version_no += 1
            previous_summary = attempt_row.summary
            change_hint = review.get("change_hint") or None
