"""Write one research run and its children.

The run row is written first so its id can tag the children. A child failure
therefore leaves a run row with nothing under it - which the grid shows as a
run whose detail is missing, rather than losing the run entirely. The error
names the run id so the rest can be re-attached by hand if it ever matters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from db import to_native

CHUNK = 500

_CHILD_TABLES = (
    ("combos", "research_combo_results"),
    ("locked_trades", "research_locked_trades"),
    ("equity", "research_locked_equity"),
)


class ResearchStoreError(RuntimeError):
    """A research row could not be written. The message says which table."""


def _jsonable(value: Any) -> Any:
    """Dates and timestamps as ISO text.

    `to_native` handles numpy scalars but leaves datetime objects alone, and
    PostgREST sends JSON - so a row carrying a real `datetime` fails at the
    insert with "Object of type datetime is not JSON serializable", after the
    whole run has been computed. Converted here, at the database boundary, so
    the builders stay typed and testable.
    """
    if isinstance(value, datetime):     # checked first: datetime is a date
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _insert(client: Any, table: str, rows: Sequence[Mapping[str, Any]], run_id: str | None):
    payload = [
        {key: _jsonable(value) for key, value in to_native(dict(r)).items()}
        for r in rows
    ]
    try:
        return client.table(table).insert(payload).execute()
    except Exception as exc:        # noqa: BLE001 - re-raised with context
        where = f" (run {run_id})" if run_id else ""
        raise ResearchStoreError(f"could not write {table}{where}: {exc}") from exc


def save_run(
    client: Any,
    *,
    run: Mapping[str, Any],
    combos: Sequence[Mapping[str, Any]] = (),
    locked_trades: Sequence[Mapping[str, Any]] = (),
    equity: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Store a run and everything under it. Returns the new run id."""
    response = _insert(client, "research_runs", [dict(run)], None)
    rows = getattr(response, "data", None) or []
    if not rows or "id" not in rows[0]:
        raise ResearchStoreError(
            "research_runs insert returned no id, so children cannot be attached"
        )
    run_id = str(rows[0]["id"])

    children = {"combos": combos, "locked_trades": locked_trades, "equity": equity}
    for key, table in _CHILD_TABLES:
        batch = [{**dict(row), "run_id": run_id} for row in children[key]]
        for start in range(0, len(batch), CHUNK):
            _insert(client, table, batch[start:start + CHUNK], run_id)
    return run_id


def save_versions(client: Any, run_id: str, versions: Sequence[Mapping[str, Any]]) -> int:
    """Every version a run tried, valid or not (design 2.7: summaries only)."""
    rows = [{**dict(v), "run_id": run_id} for v in versions]
    if not rows:
        return 0
    for start in range(0, len(rows), CHUNK):
        _insert(client, "research_versions", rows[start:start + CHUNK], run_id)
    return len(rows)


def save_notes(client: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    """The journal rows a later day's prompt builder reads."""
    if not rows:
        return 0
    _insert(client, "research_notes", list(rows), None)
    return len(rows)


def recent_notes(client: Any, *, limit: int = 60) -> list[dict[str, Any]]:
    """The newest notes. Training lessons only, by the table's own design."""
    try:
        resp = (
            client.table("research_notes")
            .select("day,idea_title,outcome_training,lessons")
            .order("day", desc=True).limit(limit).execute()
        )
    except Exception:       # noqa: BLE001 - a missing table means no history yet
        return []
    return list(getattr(resp, "data", None) or [])


def save_research_strategy(
    store: Any, document: Mapping[str, Any], *, title: str, description: str, hypothesis: str
) -> int | None:
    """Save a proposed strategy to the library, paused, with its story.

    `db.save_strategy_document` writes the columns every strategy has; the
    research columns added later (title, description, hypothesis, origin) are
    written here, so a library row says where it came from and what it was for.

    Returns the strategy_versions id, so a run row can point at exactly the
    definition that was tested.
    """
    result = store.save_strategy_document(dict(document))
    try:
        (
            store._table("strategies")
            .update({
                "title": title, "description": description,
                "hypothesis": hypothesis, "origin": "research",
            })
            .eq("name", document["name"]).execute()
        )
    except Exception as exc:        # noqa: BLE001 - the strategy itself is saved
        raise ResearchStoreError(
            f"saved strategy {document['name']} but could not write its research "
            f"columns: {exc}"
        ) from exc
    return getattr(result, "version_id", None)
