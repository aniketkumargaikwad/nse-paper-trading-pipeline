"""Copy candles out of Supabase into Parquet, verifying every symbol.

    .venv\Scripts\python.exe scripts/migrate_candles_to_parquet.py --dry-run
    .venv\Scripts\python.exe scripts/migrate_candles_to_parquet.py
    .venv\Scripts\python.exe scripts/migrate_candles_to_parquet.py --verify-only

COPIES, never deletes. The Supabase rows are left exactly as they are, so the
migration is reversible by changing CANDLE_STORE back. Deleting them is a
separate, deliberate act once you trust the copy - see --print-cleanup-sql.

Every symbol is verified by reading it back and comparing candle-for-candle.
A migration that silently dropped or altered a bar would corrupt every
backtest built on it afterwards while looking like it worked.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

UTC = timezone.utc
# Wide enough to cover anything stored; coverage rows say what actually exists.
FROM_UTC = datetime(2000, 1, 1, tzinfo=UTC)
TO_UTC = datetime(2100, 1, 1, tzinfo=UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy candles from Supabase into Parquet files."
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be copied, write nothing")
    parser.add_argument("--verify-only", action="store_true",
                        help="compare an existing Parquet copy against Supabase")
    parser.add_argument("--root", default=None,
                        help="destination (default: CANDLE_ROOT from config)")
    parser.add_argument("--print-cleanup-sql", action="store_true",
                        help="print the SQL that would free the Supabase rows, and exit")
    return parser


CLEANUP_SQL = """-- Run this ONLY after the migration has verified clean AND you have
-- backtested successfully with CANDLE_STORE=parquet.
--
-- This is irreversible. The Parquet files become the only copy.
--
--   delete from candles;
--   vacuum full candles;   -- reclaims the space; briefly locks the table
--
-- candle_coverage is deliberately NOT touched: it is the record of what you
-- genuinely have, and it stays in Supabase in either storage mode."""


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.print_cleanup_sql:
        print(CLEANUP_SQL)
        return 0

    from config import get_settings
    from db import SupabaseStore
    from supabase_candle_backend import SupabaseCandleBackend
    from parquet_candle_backend import ParquetCandleBackend

    settings = get_settings()
    root = args.root or settings.candle_root

    try:
        store = SupabaseStore.connect(settings)
    except Exception as exc:                       # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    sb = SupabaseCandleBackend(store._client)
    pq = ParquetCandleBackend(sb, root)
    client = store._client

    coverage = client.table("candle_coverage").select(
        "instrument_id,timeframe,first_ts,last_ts"
    ).execute().data
    if not coverage:
        print("Nothing to migrate: candle_coverage is empty.")
        return 0

    ids = sorted({c["instrument_id"] for c in coverage})
    names: dict[int, str] = {}
    for i in range(0, len(ids), 200):
        for row in client.table("instruments").select("id,symbol").in_(
            "id", ids[i:i + 200]
        ).execute().data:
            names[int(row["id"])] = row["symbol"]

    print(f"Destination: {root}")
    print(f"{len(coverage)} (instrument, timeframe) pair(s) to process\n")

    copied = verified = failed = 0
    for entry in coverage:
        iid, tf = int(entry["instrument_id"]), entry["timeframe"]
        label = f"{names.get(iid, iid)} {tf}"

        if args.dry_run:
            # Deliberately does NOT read the candles. Reading 50 symbols to
            # count them takes minutes, and a dry run that is slower than the
            # thing it previews does not get used. The coverage row already
            # says what is there.
            print(
                f"  {label:<24} would copy "
                f"{entry['first_ts'][:10]} -> {entry['last_ts'][:10]}"
            )
            continue

        source = sb.read_candles(iid, tf, FROM_UTC, TO_UTC)
        if source is None or source.empty:
            print(f"  {label:<24} no candles, skipped")
            continue

        if not args.verify_only:
            pq.write_candles(iid, tf, source)
            copied += 1

        back = pq.read_candles(iid, tf, FROM_UTC, TO_UTC)
        if back is None:
            print(f"  {label:<24} FAILED - nothing written", file=sys.stderr)
            failed += 1
            continue

        # Compare candle-for-candle. A migration that silently dropped or
        # altered a bar would corrupt every backtest built on it afterwards
        # while appearing to have worked.
        aligned = back.reindex(source.index)
        same = (
            len(back) == len(source)
            and source.astype("float64").equals(aligned.astype("float64"))
        )
        if same:
            verified += 1
            print(f"  {label:<24} {len(source):>7,} candles  verified")
        else:
            failed += 1
            print(
                f"  {label:<24} MISMATCH: {len(source):,} in Supabase, "
                f"{len(back):,} read back",
                file=sys.stderr,
            )

    print()
    if args.dry_run:
        print("Dry run: nothing was written.")
        return 0

    print(f"copied {copied}, verified {verified}, failed {failed}")
    if failed:
        print("\nDO NOT switch CANDLE_STORE while anything failed.", file=sys.stderr)
        return 1

    print(
        "\nAll verified. To use it:\n"
        "  1. set CANDLE_STORE=parquet (and CANDLE_ROOT if not the default)\n"
        "  2. run a backtest and confirm the numbers match\n"
        "  3. only then consider freeing the Supabase rows:\n"
        "     python scripts/migrate_candles_to_parquet.py --print-cleanup-sql"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
