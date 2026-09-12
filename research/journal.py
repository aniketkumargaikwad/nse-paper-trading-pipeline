"""The daily note, and the rows a later run reads.

Written from the TRAINING review only. Locked-year numbers live in
research_runs, the dashboard and the messages - never here, because this is
one of the few things a later day's prompt builder reads (design 2.4).

The file is also why the GitHub schedule stays alive: a public repository's
scheduled workflow is disabled after 60 days without repository activity, and
committing this note every morning is that activity.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

JOURNAL_DIR = Path("research/journal")


def journal_markdown(day: date, entries: Sequence[dict[str, Any]]) -> str:
    """The note itself: one section per idea the day tried."""
    lines = [f"# Research notes — {day.isoformat()}", ""]
    if not entries:
        lines.append("No idea reached a tested version today.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        lines.append(f"## {entry.get('idea_title') or 'untitled idea'}")
        lines.append("")
        lines.append(f"**Training outcome:** {entry.get('outcome_training') or 'not recorded'}")
        lines.append("")
        lines.append(f"**Lessons:** {entry.get('lessons') or 'none recorded'}")
        lines.append("")
    return "\n".join(lines)


def write_journal(
    day: date, entries: Sequence[dict[str, Any]], *, root: Path | None = None
) -> Path:
    directory = (root or Path(".")) / JOURNAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.md"
    path.write_text(journal_markdown(day, entries), encoding="utf-8")
    return path


def note_rows(
    run_id: str, day: date, entries: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "day": day,
            "idea_title": entry.get("idea_title"),
            "outcome_training": entry.get("outcome_training"),
            "lessons": entry.get("lessons"),
        }
        for entry in entries
    ]


def entries_from_versions(versions: Sequence[Any]) -> list[dict[str, Any]]:
    """One entry per IDEA, taken from that idea's last tested version.

    An idea that produced several versions leaves one lesson, not five: the
    later day reads these, and a list of near-duplicates would crowd out the
    other days.
    """
    by_idea: dict[int, Any] = {}
    for version in versions:
        if version.valid and version.review:
            by_idea[version.idea_no] = version
    entries = []
    for _, version in sorted(by_idea.items()):
        summary = version.summary
        outcome = getattr(summary, "as_dict", None)
        if callable(outcome):
            facts = summary.as_dict()
            outcome_text = (
                f"{facts['combos_profitable']} of {facts['combos_tested']} combinations "
                f"profitable after fees, net {facts['net_pnl']:.0f}"
            )
        else:
            outcome_text = str(summary)
        entries.append({
            "idea_title": version.title,
            "outcome_training": outcome_text,
            "lessons": version.review.get("lessons"),
        })
    return entries
