"""Measure the options atlas on the training years, NIFTY and BANKNIFTY.

    python scripts/fno_atlas.py                    # every atlas strategy
    python scripts/fno_atlas.py --only iron-condor

Writes data/fno/atlas/results.json (every report in full) and prints a
markdown table, one row per strategy, for docs/research/. The exam year is
cut out at load (fno.market) and never read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import atlas, report, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402
from research.portfolio import luck_label  # noqa: E402

HEADER = ("| strategy | index | avg month | months up | worst day | worst fall | trades/month "
          "| years beating index | luck test | Rs 5 lakh became |\n"
          "|---|---|---:|---:|---:|---:|---:|---:|---|---:|")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(atlas.ATLAS))
    args = parser.parse_args(argv)
    root = store.root_dir()
    names = [args.only] if args.only else list(atlas.ATLAS)
    reports, lines = [], [HEADER]
    for underlying in atlas.UNDERLYINGS:
        start, end = training_window(stored_sessions(root, underlying))
        print(f"{underlying}: training {start} -> {end}", flush=True)
        market = MarketData.load(root, underlying, until=end)
        for name in names:
            result = run(parse(atlas.document(name, underlying)), market, start=start, end=end)
            built = report.build(result, market.closes())
            reports.append(built.as_dict())
            h = built.headline
            lines.append(f"| {name} | {underlying} | {h['avg_month_pct']:+.2f}% | {h['months_positive_pct']:.0f}% "
                         f"| {h['worst_day_pct']:+.2f}% | {h['worst_fall_pct']:.1f}% | {h['trades_per_month']:.1f} "
                         f"| {h['years_beating_holding_pct']:.0f}% | {luck_label(h['edge_t'])} "
                         f"| Rs {h['end_value']:,.0f} |")
            print(lines[-1], flush=True)
    path = root / "atlas" / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
