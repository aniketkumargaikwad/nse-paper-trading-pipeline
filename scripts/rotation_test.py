"""The pre-registered momentum rotation test (docs/superpowers/specs/2026-10-20-momentum-rotation-design.md).

    python scripts/rotation_test.py            # training years, every variant
    python scripts/rotation_test.py --exam     # the picked variant's exam year, once

Reads data/rotation/closes.parquet (Yahoo, adjusted). Writes data/rotation/results.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from research.rotation import INDEX, Variant, run, stats  # noqa: E402

VARIANTS = [Variant("R1", 12, 1, 20, False), Variant("R2", 6, 0, 20, False),
            Variant("R3", 12, 1, 20, True), Variant("R4", 12, 1, 10, False)]
EQUAL = Variant("all 200 held equally", 12, 0, 10_000, False)
TRAIN = (date(2009, 1, 1), date(2025, 10, 17))
EXAM = (date(2025, 10, 20), date(2026, 10, 19))
COST_PCT = 0.2


def picked(rows: list[dict]) -> str | None:
    ok = [r for r in rows if r["worst_fall_pct"] <= 30 and r["years_beating_pct"] >= 50]
    return max(ok, key=lambda r: r["cagr_pct"])["variant"] if ok else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--exam", action="store_true")
    args = parser.parse_args()
    closes = pd.read_parquet("data/rotation/closes.parquet")
    nifty = closes[INDEX]
    out = Path("data/rotation/results.json")
    if not args.exam:
        rows = [stats(run(closes, v, start=TRAIN[0], end=TRAIN[1], cost_pct=COST_PCT), nifty)
                for v in [*VARIANTS, EQUAL]]
        for r in rows:
            print({k: v for k, v in r.items() if k not in ("yearly", "bench_yearly")}, flush=True)
        pick = picked(rows[:-1])
        print("PICK (training rule):", pick)
        out.write_text(json.dumps({"training": rows, "pick": pick}, default=str, indent=1), encoding="utf-8")
        return 0
    saved = json.loads(out.read_text(encoding="utf-8"))
    if saved["pick"] is None:
        print("No variant qualified on training: the exam is not opened.")
        return 0
    v = next(x for x in VARIANTS if x.name == saved["pick"])
    lookback_start = date(2024, 1, 1)
    exam = stats(run(closes.loc[closes.index >= lookback_start], v, start=EXAM[0], end=EXAM[1],
                     cost_pct=COST_PCT), nifty)
    equal = stats(run(closes.loc[closes.index >= lookback_start], EQUAL, start=EXAM[0], end=EXAM[1],
                      cost_pct=COST_PCT), nifty)
    for r in (exam, equal):
        print({k: v for k, v in r.items() if k not in ("yearly", "bench_yearly")})
    saved["exam"] = [exam, equal]
    out.write_text(json.dumps(saved, default=str, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
