"""One-way migration of a strategy document from format v1 to v2.

Mechanical and lossless. The rule that governs every choice here: a migrated v1
strategy must backtest IDENTICALLY to the way it did before. That is why no
`session:` block is invented — v1 had no square-off, so adding one would change
the strategy while claiming to preserve it — and why `fixed_quantity` is carried
over rather than converted to a notional. Converting a share count to a rupee
amount needs a price, and any price picked here would be a fabrication.

Migration never writes in place: it returns a new document, so the caller
decides whether to persist it.
"""

from __future__ import annotations

import copy
from typing import Any

CURRENT_VERSION = 2


class MigrationError(ValueError):
    """A document is in a version this code cannot migrate."""


def _migrate_strategy_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)

    risk = out.get("risk")
    if isinstance(risk, dict) and ("stop_loss_pct" in risk or "target_pct" in risk):
        # A half-written v1 risk block is entirely plausible from a hand-edited
        # or AI-generated file. Say what is missing rather than letting a bare
        # KeyError escape — this audience cannot act on a traceback.
        missing = [k for k in ("stop_loss_pct", "target_pct") if k not in risk]
        if missing:
            name = raw.get("name", "(unnamed)")
            raise MigrationError(
                f"strategy {name!r} has an incomplete version 1 risk block: "
                f"missing {', '.join(missing)}. A version 1 strategy needs both "
                "stop_loss_pct and target_pct before it can be migrated."
            )
        try:
            stop_value = float(risk["stop_loss_pct"])
            target_value = float(risk["target_pct"])
        except (TypeError, ValueError) as exc:
            name = raw.get("name", "(unnamed)")
            raise MigrationError(
                f"strategy {name!r} has a non-numeric version 1 risk value: {exc}. "
                "stop_loss_pct and target_pct must both be numbers."
            ) from exc
        out["risk"] = {
            "stop_loss": {"type": "percent", "value": stop_value},
            "target": {"type": "percent", "value": target_value},
        }

    # v1 defaulted an absent sizing block to one share; make that explicit so
    # v2's required-sizing rule is satisfied without changing behaviour.
    if "sizing" not in out:
        out["sizing"] = {"type": "fixed_quantity", "quantity": 1}

    return out


def migrate_document(document: Any) -> dict[str, Any]:
    """Return `document` at the current format version.

    A document already at the current version is returned as an unchanged copy,
    so callers can migrate unconditionally on read.
    """
    if not isinstance(document, dict) or "version" not in document:
        raise MigrationError(
            "not a strategy document: expected a mapping with a 'version' key"
        )

    version = document["version"]
    if version == CURRENT_VERSION:
        return copy.deepcopy(document)

    if version != 1:
        raise MigrationError(
            f"unsupported strategy format version {version!r}; "
            f"this code understands 1 and {CURRENT_VERSION}"
        )

    out = copy.deepcopy(document)
    out["version"] = CURRENT_VERSION
    out["strategies"] = [
        _migrate_strategy_v1_to_v2(raw) if isinstance(raw, dict) else raw
        for raw in document.get("strategies", [])
    ]
    return out
