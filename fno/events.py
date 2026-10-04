"""Market event days: Union Budget, RBI policy, general-election results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

EVENTS_FILE = Path(__file__).with_name("events.yaml")


@dataclass(frozen=True)
class Events:
    rows: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path = EVENTS_FILE) -> "Events":
        rows = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
        for row in rows:
            row["date"] = date.fromisoformat(str(row["date"]))
        return cls(tuple(rows))

    def on(self, day: date) -> list[str]:
        return sorted(r["kind"] for r in self.rows if r["date"] == day)

    def between(self, start: date, end: date) -> list[date]:
        return sorted({r["date"] for r in self.rows if start <= r["date"] <= end})
