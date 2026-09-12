"""Write one research run and its children.

The run row is written first so its id can tag the children. A child failure
therefore leaves a run row with nothing under it - which the grid shows as a
run whose detail is missing, rather than losing the run entirely. The error
names the run id so the rest can be re-attached by hand if it ever matters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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


def _insert(client: Any, table: str, rows: Sequence[Mapping[str, Any]], run_id: str | None):
    payload = [to_native(dict(r)) for r in rows]
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
