"""Measure the options atlas on the training years, NIFTY and BANKNIFTY.

    python scripts/fno_atlas.py                       # the positional atlas (daily data, since 2016)
    python scripts/fno_atlas.py --intraday            # the intraday atlas (minute data, since Oct 2024)
    python scripts/fno_atlas.py --only iron-condor

Writes data/fno/atlas/results.json or intraday.json (every report in full, each
with whether it passes the options rule and why not) and prints a markdown
table. The exam year is cut out at load and never read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import atlas, intraday, report, rules, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.intraday_market import IntradayMarket  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402
from research.portfolio import luck_label  # noqa: E402

HEADER = ("| strategy | index | passes? | avg month | months up | worst day | worst fall | trades/month "
          "| years beating index | luck test | Rs 5 lakh became |\n"
          "|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--intraday", action="store_true")
    parser.add_argument("--only", choices=sorted(set(atlas.ATLAS) | set(atlas.INTRADAY)))
    args = parser.parse_args(argv)
    catalogue = atlas.INTRADAY if args.intraday else atlas.ATLAS
    if args.only and args.only not in catalogue:
        parser.error(f"{args.only} is not in the {'intraday' if args.intraday else 'positional'} atlas")
    mode = "intraday" if args.intraday else "positional"
    root = store.root_dir()
    names = [args.only] if args.only else list(catalogue)
    reports, lines = [], [HEADER]
    for underlying in atlas.UNDERLYINGS:
        start, end = training_window(stored_sessions(root, underlying))
        if args.intraday:
            market = IntradayMarket.load(root, underlying, until=end)
            if market.first_day is None:
                print(f"{underlying}: no option minutes stored")
                continue
            start = max(start, market.first_day)
            closes, runner, document = market.daily.closes(), intraday.run, atlas.intraday_document
        else:
            market = MarketData.load(root, underlying, until=end)
            closes, runner, document = market.closes(), run, atlas.document
        print(f"{underlying}: training {start} -> {end}", flush=True)
        for name in names:
            result = runner(parse(document(name, underlying)), market, start=start, end=end)
            built = report.build(result, closes)
            why = rules.disqualified(built, mode)
            saved = built.as_dict()
            saved.update(passes=why is None, why_not=why)
            reports.append(saved)
            h = built.headline
            verdict = "**yes**" if why is None else f"no - {why}"
            lines.append(f"| {name} | {underlying} | {verdict} | {h['avg_month_pct']:+.2f}% "
                         f"| {h['months_positive_pct']:.0f}% | {h['worst_day_pct']:+.2f}% "
                         f"| {h['worst_fall_pct']:.1f}% | {h['trades_per_month']:.1f} "
                         f"| {h['years_beating_holding_pct']:.0f}% | {luck_label(h['edge_t'])} "
                         f"| Rs {h['end_value']:,.0f} |")
            print(lines[-1], flush=True)
    path = root / "atlas" / ("intraday.json" if args.intraday else "results.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
