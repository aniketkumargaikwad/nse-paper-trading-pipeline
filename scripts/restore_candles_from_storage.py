"""Bring the Parquet candle store down from Supabase Storage.

WHY
---
A GitHub runner starts with an empty disk. The research sweep reads nine
years of candles, so before anything can run they have to arrive - 663 MB of
them, from the bucket `scripts/backup_candles_to_storage.py` fills.

RESUMABLE, AND CHEAP TO RE-RUN
------------------------------
Every file already on disk at the same size is skipped, so a second run after
an interrupted first costs a listing rather than 663 MB. That matters more
here than for the uploader: Supabase's free tier meters egress at 5 GB a
month, which is about seven full restores. The Actions cache is what keeps a
normal day from spending any of it at all.

By default only the timeframes a research day reads are fetched (5m, 60m,
day) - `1m` exists in the bucket and no sweep ever opens it.

    python scripts/restore_candles_from_storage.py --dry-run
    python scripts/restore_candles_from_storage.py
    python scripts/restore_candles_from_storage.py --all-timeframes
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from candle_backup import (  # noqa: E402
    BUCKET,
    LOCAL_ROOT,
    RESEARCH_TIMEFRAMES,
    local_files,
    missing_locally,
    remote_files,
)
from config import use_utf8_stdout  # noqa: E402


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(
        description="Download the candle store from Supabase Storage.")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be downloaded, write nothing")
    parser.add_argument("--all-timeframes", action="store_true",
                        help="include timeframes the research loop never reads")
    parser.add_argument("--root", default=LOCAL_ROOT)
    args = parser.parse_args()

    try:
        from config import get_settings
        from db import SupabaseStore

        client = SupabaseStore.connect(get_settings())._client
        bucket = client.storage.from_(BUCKET)
    except Exception as exc:      # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    print("listing the bucket (one request per directory, this takes a moment)...")
    remote = remote_files(bucket)
    if not remote:
        print(f"The bucket {BUCKET!r} is empty. Run "
              "scripts/backup_candles_to_storage.py from a machine that has the "
              "candles first.", file=sys.stderr)
        return 1

    local = local_files(args.root) if os.path.isdir(args.root) else {}
    timeframes = None if args.all_timeframes else RESEARCH_TIMEFRAMES
    todo = missing_locally(remote, local, timeframes=timeframes)

    print(f"bucket: {len(remote):>5} files, {sum(remote.values())/1024/1024:>7.1f} MB")
    print(f"local:  {len(local):>5} files, {sum(local.values())/1024/1024:>7.1f} MB")
    if not todo:
        print("\nNothing to download - the local store already matches.")
        return 0

    total = sum(remote[path] for path in todo)
    print(f"\ndownloading {len(todo)} file(s), {total/1024/1024:.1f} MB")
    if args.dry_run:
        for path in todo[:20]:
            print(f"  {path}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0

    done = 0
    for path in todo:
        destination = Path(args.root) / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            destination.write_bytes(bucket.download(path))
        except Exception as exc:      # noqa: BLE001 - one file, not the run
            print(f"FAILED {path}: {exc}", file=sys.stderr)
            continue
        done += 1
        if done % 250 == 0:
            print(f"  {done}/{len(todo)}")

    print(f"\ndownloaded {done} of {len(todo)} file(s) into {args.root}")
    # A partial restore is a failure: a sweep on half a candle store produces
    # numbers that look real.
    return 0 if done == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
