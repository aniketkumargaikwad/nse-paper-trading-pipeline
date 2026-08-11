"""Refresh symbol universes from NSE and write dated snapshots.

Usage (from the project folder):

    .venv\\Scripts\\python.exe scripts/refresh_universes.py
    .venv\\Scripts\\python.exe scripts/refresh_universes.py --universes NIFTY50
    .venv\\Scripts\\python.exe scripts/refresh_universes.py --dry-run

Run this after an index rebalance (roughly twice a year). Snapshots written to
data/universes/ are meant to be COMMITTED: they are what keeps universes
resolvable when NSE blocks or changes its endpoints, and what lets a backtest
state how old the membership it used actually was.

Requires the instruments table to be populated first:

    .venv\\Scripts\\python.exe backfill.py --refresh-instruments
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402
from universes import (  # noqa: E402
    NSE_INDEX_URLS,
    SNAPSHOT_DIR,
    UniverseError,
    load_constituents,
    project_storage,
    resolve_universe,
    snapshot_filename,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh symbol universes from NSE and write snapshots."
    )
    parser.add_argument(
        "--universes",
        help=(
            "comma-separated names (default: all of "
            f"{', '.join(sorted(NSE_INDEX_URLS))})"
        ),
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and report, but write neither snapshots nor the database",
    )
    parser.add_argument(
        "--years", type=float, default=2.0,
        help="years of history to project storage for (default 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = (
        [n.strip().upper() for n in args.universes.split(",") if n.strip()]
        if args.universes
        else sorted(NSE_INDEX_URLS)
    )

    # Same construction path as backfill.py: SupabaseStore owns the connection
    # and the credential checks; the candle backend wraps its raw client.
    from db import SupabaseStore
    from supabase_candle_backend import SupabaseCandleBackend

    try:
        store = SupabaseStore.connect(get_settings())
    except Exception as exc:                       # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    backend = SupabaseCandleBackend(store._client)
    known = backend.known_symbols()
    if not known:
        print(
            "The instruments table is empty, so nothing could be resolved.\n"
            "Populate it first:\n"
            "    .venv\\Scripts\\python.exe backfill.py --refresh-instruments",
            file=sys.stderr,
        )
        return 1

    exit_code = 0
    for name in names:
        try:
            constituents = load_constituents(name)
        except UniverseError as exc:
            print(f"{name}: FAILED - {exc}", file=sys.stderr)
            exit_code = 1
            continue

        # Printed to stderr so it is impossible to miss even when stdout is
        # being piped somewhere: a stale membership silently used is exactly
        # the failure this snapshot mechanism exists to make visible.
        if constituents.warning:
            print(f"WARNING: {constituents.warning}", file=sys.stderr)

        try:
            resolution = resolve_universe(
                name=name,
                listed=constituents.symbols,
                known=set(known),
                as_of=constituents.as_of,
                source=constituents.source,
            )
        except UniverseError as exc:
            print(f"{name}: FAILED - {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(resolution.summary())
        projection = project_storage(
            symbol_count=len(resolution.symbols), years=args.years
        )
        print(f"  {projection.summary()}")

        if args.dry_run:
            continue

        # Only a live fetch is worth snapshotting; re-writing a snapshot from
        # a snapshot would just move its date forward and lose the honest
        # record of how old the membership really is.
        if constituents.source == "nse":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = SNAPSHOT_DIR / snapshot_filename(name, constituents.as_of)
            snapshot.write_text(constituents.raw_csv, encoding="utf-8")
            print(f"  snapshot written: {snapshot} (commit this)")

        try:
            group_id = backend.upsert_symbol_group(
                name, source="nse", as_of=constituents.as_of
            )
            backend.replace_group_members(
                group_id, [known[s] for s in resolution.symbols]
            )
        except Exception as exc:                   # noqa: BLE001
            print(f"{name}: FAILED to store - {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(f"  stored: group {group_id}, {len(resolution.symbols)} members")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
