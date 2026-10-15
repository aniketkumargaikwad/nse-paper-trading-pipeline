"""Backtest one options strategy on daily data and print its report.

    python scripts/fno_backtest.py strategy.yaml
    python scripts/fno_backtest.py strategy.yaml --from 2019-01-01 --to 2022-12-31
    python scripts/fno_backtest.py strategy.yaml --json report.json

Runs the training years unless told otherwise: the exam year (the last 365
days) stays sealed unless --to reaches into it, and then the run says so.
Needs the F&O store, data/fno/features/ (scripts/fno_features.py) and
fno/lot_sizes.yaml (scripts/fno_lot_sizes.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import report, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    strategy = parse(args.path.read_text(encoding="utf-8"))
    root = store.root_dir()
    start, end = training_window(stored_sessions(root, strategy.underlying))
    if args.end and args.end > end:
        print(f"NOTE: --to {args.end} reaches into the exam year (it starts after {end}).")
    start, end = args.start or start, args.end or end
    market = MarketData.load(root, strategy.underlying, until=end)
    result = run(strategy, market, start=start, end=end)
    built = report.build(result, market.closes())
    print(report.to_text(built))
    if args.json:
        args.json.write_text(json.dumps(built.as_dict(), indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
