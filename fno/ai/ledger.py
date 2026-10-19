"""The options research records, in the repository (design §5).

`ledger.jsonl` holds one line per version - the idea, the strategy, its
TRAINING summary and the AI's review - and is what later days' prompts read.
`exams.jsonl` holds each opened exam and is written here and read nowhere in
this package: the AI never sees an exam result (design 2.4 of the stock
research, the owner's rule). A test asserts the read side cannot return one.

The journal note is the day's training lessons as markdown, committed like
the stock notes; committing it is also what keeps GitHub's schedule alive.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from fno.ai.summary import one_line

DIR = Path("research/journal/options")
LEDGER = "ledger.jsonl"
EXAMS = "exams.jsonl"
NOTES_LIMIT = 30
IDEAS_LIMIT = 200


def _dir(root: Path | None) -> Path:
    return (root or Path(".")) / DIR


def _append(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as f:
        for row in rows:
            f.write(json.dumps(row, default=str, sort_keys=True) + "\n")


def version_rows(day: date, versions: Sequence[Any]) -> list[dict[str, Any]]:
    """One ledger line per version the day tried, rejected ones included."""
    rows = []
    for v in versions:
        summary = v.summary.as_dict() if v.summary is not None else None
        rows.append({
            "day": day.isoformat(), "idea_no": v.idea_no, "version_no": v.version_no,
            "title": v.title, "description": v.description, "hypothesis": v.hypothesis,
            "change_note": v.change_note, "valid": v.valid, "error": v.error,
            "strategy_name": v.checked.document["name"] if v.checked is not None else None,
            "strategy": v.checked.document if v.checked is not None else None,
            "training": summary,
            "why_failed": v.review.get("why_failed"), "why_worked": v.review.get("why_worked"),
            "lessons": v.review.get("lessons"), "decision": v.review.get("decision"),
        })
    return rows


def append_versions(rows: Sequence[dict[str, Any]], *, root: Path | None = None) -> None:
    _append(_dir(root) / LEDGER, rows)


def append_exam(row: dict[str, Any], *, root: Path | None = None) -> None:
    _append(_dir(root) / EXAMS, [row])


def read_ledger(*, root: Path | None = None) -> list[dict[str, Any]]:
    path = _dir(root) / LEDGER
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def notes(*, root: Path | None = None, limit: int = NOTES_LIMIT) -> list[dict[str, Any]]:
    """The last lesson of each idea, newest day first - what earlier options days learned."""
    last: dict[tuple[str, int], dict[str, Any]] = {}
    for row in read_ledger(root=root):
        if row.get("valid") and row.get("lessons"):
            last[(row["day"], row["idea_no"])] = row
    ordered = sorted(last.values(), key=lambda r: (r["day"], r["idea_no"]), reverse=True)
    return [{"day": r["day"], "idea_title": r["title"], "lessons": r["lessons"]} for r in ordered[:limit]]


def ideas_tried(*, root: Path | None = None, limit: int = IDEAS_LIMIT) -> list[str]:
    """One line per tested version, newest first, with the numbers that decided it."""
    rows = [r for r in read_ledger(root=root) if r.get("valid")]
    lines = []
    for r in reversed(rows[-limit:]):
        line = f"{r['strategy_name']} ({r['title']}): {one_line(r.get('training'))}"
        if r.get("lessons"):
            line += f" - lesson: {r['lessons']}"
        lines.append(line)
    return lines


def write_note(day: date, versions: Sequence[Any], *, root: Path | None = None) -> Path:
    """The day's journal note: per idea, its last tested version's training result and lesson."""
    by_idea: dict[int, Any] = {}
    for v in versions:
        if v.valid and v.review:
            by_idea[v.idea_no] = v
    lines = [f"# Options research notes — {day.isoformat()}", ""]
    if not by_idea:
        lines.append("No idea reached a tested version today.")
    for _, v in sorted(by_idea.items()):
        lines += [f"## {v.title or 'untitled idea'}", "",
                  f"**Training outcome:** {one_line(v.summary)}", "",
                  f"**Lessons:** {v.review.get('lessons') or 'none recorded'}", ""]
    path = _dir(root) / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(lines).rstrip("\n") + "\n").encode("utf-8"))
    return path
