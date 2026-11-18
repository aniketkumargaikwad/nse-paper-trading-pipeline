"""Paper-trade every version that passed training before 18 Nov 2026, exam year or not.

    python scripts/promote_past_passes.py

The owner's instruction on 18 Nov 2026: push every pass to paper trading, even
when its exam year failed - including the two index plans of 17 Nov. From that
day the research days do this themselves (research/alloc_day.py,
research/rotation_day.py, fno/ai/day.py); this adds the passes recorded in the
ledgers before then, through the same promote() functions, so the rows are
identical in shape. Run once; a second run adds nothing (each skips what is
already paper-traded, including the books listed by hand in
research/paper_books.BOOKS).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import alloc_day, paper_books, rotation_day  # noqa: E402


def rows(track: str) -> list[dict]:
    path = Path(f"research/journal/{track}/ledger.jsonl")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def exams(track: str) -> dict[str, dict]:
    path = Path(f"research/journal/{track}/exams.jsonl")
    if not path.exists():
        return {}
    return {e["strategy_name"]: e for e in map(json.loads, path.read_text(encoding="utf-8").splitlines()) if e}


def main() -> int:
    said = []
    alloc_exams = exams("alloc")
    for r in rows("alloc"):
        if not (r.get("training") or {}).get("passes"):
            continue
        version = SimpleNamespace(title=r["title"], checked=SimpleNamespace(document=r["strategy"]),
                                  summary=SimpleNamespace(reading=r["training"]["plan"]))
        _, words = alloc_day.promote(date.fromisoformat(r["day"]), version, alloc_exams.get(r["strategy_name"]))
        said.append(f"index {r['day']} v{r['idea_no']}.{r['version_no']} {r['title']}: {words}")

    # Rotation rules already paper-traded by hand (research/paper_books.BOOKS) are skipped.
    by_hand = [{k: v for k, v in asdict(b.variant).items() if k != "name"}
               for b in paper_books.BOOKS if b.kind == "rotation"]
    rot_exams = exams("rotation")
    for r in rows("rotation"):
        if not (r.get("training") or {}).get("passes"):
            continue
        day = date.fromisoformat(r["day"])
        checked = rotation_day.check_proposal(yaml.safe_dump(r["strategy"]), idea=str(r["idea_no"]), day=day,
                                              version=int(r["version_no"]))
        if {k: v for k, v in asdict(checked.variant).items() if k != "name"} in by_hand:
            said.append(f"rotation {r['day']} v{r['idea_no']}.{r['version_no']}: already a paper book")
            continue
        version = SimpleNamespace(title=r["title"], checked=checked, idea_no=r["idea_no"],
                                  version_no=r["version_no"],
                                  summary=SimpleNamespace(as_dict=lambda t=r["training"]: t))
        _, words = rotation_day.promote(day, version, rot_exams.get(r["strategy_name"]))
        said.append(f"rotation {r['day']} v{r['idea_no']}.{r['version_no']} {r['title']}: {words}")
    print("\n".join(said) or "no past passes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
