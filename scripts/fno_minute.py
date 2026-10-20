"""Fill or top up the minute store: expired NIFTY / BANKNIFTY options from Upstox,
the indexes and India VIX from Upstox (or Dhan).

    python scripts/fno_minute.py                  # everything due
    python scripts/fno_minute.py --options-only
    python scripts/fno_minute.py --indexes-only
    python scripts/fno_minute.py --indexes-only --index-source dhan
    python scripts/fno_minute.py --only NIFTY

Resumable: minute/_expiries.csv records every expiry fetched, index files say
how far they reach. Run scripts/fno_daily.py first - the strike band reads
the NSE session list and the index closes.

Needs UPSTOX_ANALYTICS_TOKEN; DHAN_* plus SUPABASE_* only for --index-source dhan.
Dhan filled the index history; since 19 Oct 2026 Upstox tops it up, because
Dhan's login fails from GitHub (a stored day of each matched Upstox within
0.01%).
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, use_utf8_stdout  # noqa: E402
from fno import index_minutes, minute, spot, store, upstox  # noqa: E402
from fno.contracts import MINUTE_UNDERLYINGS  # noqa: E402


def options(root: Path, underlyings: list[str], today, *, budget_seconds: float | None = None) -> int:
    """Every due expiry, oldest first. With a budget, stops between expiries once it is
    spent - a GitHub job killed at its time limit never reaches its publish step."""
    started = time.monotonic()
    client = upstox.UpstoxClient(upstox.token_from_env())
    sessions = sorted(d for d, status in store.load_ledger(root).items() if status == "ok")
    for underlying in underlyings:
        try:
            spot.refresh(root, underlying)
        except Exception as exc:          # Yahoo hiccup: yesterday's closes still serve
            print(f"{underlying}: index closes not refreshed ({type(exc).__name__}); using stored")
        closes = spot.closes(root, underlying)
        served = client.expiries(underlying)
        todo = minute.due(underlying, served, minute.load_ledger(root), today=today)
        print(f"{underlying}: Upstox serves {len(served)} expiries"
              + (f", {served[0]} -> {served[-1]}" if served else "") + f"; {len(todo)} to fetch", flush=True)
        for i, expiry in enumerate(todo):
            if budget_seconds is not None and time.monotonic() - started > budget_seconds:
                print(f"{underlying}: stopping - time budget of {budget_seconds / 60:.0f} min spent; "
                      f"{len(todo) - i} expiries left for the next run", flush=True)
                return 0
            # 45 days back covers a monthly expiry's 20-session window
            daily = store.read_daily(root, underlying, expiry - timedelta(days=45), expiry)
            row = minute.fetch_expiry(client, root, underlying, expiry, sessions, closes,
                                      daily=daily, today=today)
            minute.record(root, row)
            print(f"  {expiry}: {row['status']}, {row['contracts']} contracts, {row['rows']:,} minutes",
                  flush=True)
    return 0


def indexes(root: Path, today, source: str = "upstox") -> int:
    provider = index_minutes.create_provider(source)
    sessions = {d for d, status in store.load_ledger(root).items() if status == "ok"}
    for name in index_minutes.INDEXES:
        n = index_minutes.fill(provider, root, name, today=today, sessions=sessions)
        print(f"{name}: {n:,} minutes fetched", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=MINUTE_UNDERLYINGS)
    parser.add_argument("--budget-minutes", type=float,
                        help="stop fetching options (cleanly, between expiries) after this long")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--options-only", action="store_true")
    group.add_argument("--indexes-only", action="store_true")
    parser.add_argument("--index-source", choices=index_minutes.SOURCES, default="upstox")
    args = parser.parse_args(argv)
    root = store.root_dir()
    today = datetime.now(IST).date()
    failed = False
    if not args.indexes_only:
        try:
            options(root, [args.only] if args.only else list(MINUTE_UNDERLYINGS), today,
                    budget_seconds=None if args.budget_minutes is None else args.budget_minutes * 60)
        except upstox.UpstoxError as exc:
            print(f"OPTIONS STOPPED: {exc}", file=sys.stderr)
            failed = True
    if not args.options_only:
        try:
            indexes(root, today, args.index_source)
        except Exception as exc:
            print(f"INDEXES STOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
