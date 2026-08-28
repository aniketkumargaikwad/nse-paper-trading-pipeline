"""Copy the local Parquet candle store into Supabase Storage.

WHY
---
The Parquet files hold nine years of history in 186 MB, and they live in
exactly one place: `data/candles` on one laptop, gitignored because they are
regenerable data. "Regenerable" is doing a lot of work in that sentence - it
means a Dhan subscription, an intact archive, and hours of paging.

Supabase Storage is a SEPARATE quota from the database: 1 GB of files
alongside 500 MB of rows on the free tier. So this copy costs nothing, and it
is what makes it safe to clear the stale candle rows out of the database.

It is also the same layout `CANDLE_ROOT=supabase://candles` reads, so the
backup is not a dead archive - a host can be pointed straight at it.

RESUMABLE BY DESIGN
-------------------
1,452 files over a network will be interrupted. Every upload is compared
against what the bucket already holds and skipped when the size matches, so
re-running after a failure costs a listing rather than a re-upload. That also
makes this safe to run repeatedly as an ordinary backup.

Size, not checksum: Storage does not return a content hash cheaply, and a
Parquet file that changed content without changing length is not a thing that
happens here - a year file grows as candles are added. `--force` re-uploads
regardless.

    python scripts/backup_candles_to_storage.py --dry-run
    python scripts/backup_candles_to_storage.py
    python scripts/backup_candles_to_storage.py --verify
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402

LOCAL_ROOT = "data/candles"
BUCKET = "candles"


def local_files(root: str) -> dict[str, int]:
    """Every parquet file under `root`, keyed by its bucket path."""
    found: dict[str, int] = {}
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            if not name.endswith(".parquet"):
                continue
            full = os.path.join(dirpath, name)
            key = os.path.relpath(full, root).replace(os.sep, "/")
            found[key] = os.path.getsize(full)
    return found


# Supabase Storage returns at most 100 entries per list() call and gives no
# hint that it truncated. With 200 instrument directories under 5m/, an
# unpaged listing silently sees half of them - which made --verify permanently
# report ~2,400 files "missing" and the uploader re-send files already in the
# bucket. Paginate explicitly; never trust one call to be the whole directory.
_PAGE = 1000


def _list_all(bucket, prefix: str) -> list:
    """Every entry under `prefix`, following pages to the end."""
    entries: list = []
    offset = 0
    while True:
        try:
            page = bucket.list(prefix, {"limit": _PAGE, "offset": offset})
        except Exception:      # noqa: BLE001 - an absent prefix is normal
            return entries
        if not page:
            return entries
        entries.extend(page)
        if len(page) < _PAGE:
            return entries
        offset += len(page)


def remote_files(bucket) -> dict[str, int]:
    """Every object in the bucket, keyed by path, valued by size.

    Storage lists one directory level at a time, so this walks the same
    {timeframe}/{instrument_id}/{year}.parquet shape the writer produces.
    """
    found: dict[str, int] = {}

    def walk(prefix: str, depth: int) -> None:
        for entry in _list_all(bucket, prefix):
            name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
            if not name:
                continue
            path = f"{prefix}/{name}" if prefix else name
            meta = entry.get("metadata") if isinstance(entry, dict) else None
            size = (meta or {}).get("size")
            if size is not None:
                found[path] = int(size)
            elif depth < 3:
                walk(path, depth + 1)

    walk("", 0)
    return found


def main() -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would be uploaded, change nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-upload even when the size already matches")
    parser.add_argument("--verify", action="store_true",
                        help="compare local and bucket, upload nothing")
    args = parser.parse_args()

    try:
        from config import get_settings
        from db import SupabaseStore

        client = SupabaseStore.connect(get_settings())._client
        bucket = client.storage.from_(BUCKET)
    except Exception as exc:      # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    if not os.path.isdir(LOCAL_ROOT):
        print(f"No local candle store at {LOCAL_ROOT!r}. Nothing to back up.",
              file=sys.stderr)
        return 1

    local = local_files(LOCAL_ROOT)
    print(f"local:  {len(local):>5} files, {sum(local.values())/1024/1024:>7.1f} MB")
    print("listing the bucket (one request per directory, this takes a moment)...")
    remote = remote_files(bucket)
    print(f"bucket: {len(remote):>5} files, {sum(remote.values())/1024/1024:>7.1f} MB")

    missing = sorted(k for k in local if k not in remote)
    differing = sorted(
        k for k in local if k in remote and remote[k] != local[k]
    )
    extra = sorted(k for k in remote if k not in local)

    print(f"\nmissing from bucket: {len(missing)}")
    print(f"size mismatch:       {len(differing)}")
    # Not deleted. An object the local store no longer produces may be from an
    # older layout, and silently removing someone's only other copy of data is
    # not a thing a backup tool should do.
    print(f"in bucket only:      {len(extra)}  (left alone)")

    if args.verify:
        ok = not missing and not differing
        print("\nVERIFIED: every local file is in the bucket at the same size."
              if ok else
              "\nNOT COMPLETE: the bucket does not yet match local.")
        return 0 if ok else 1

    todo = sorted(set(missing) | set(differing)) if not args.force else sorted(local)
    if not todo:
        print("\nNothing to upload - the bucket already matches.")
        return 0

    total = sum(local[k] for k in todo)
    print(f"\nuploading {len(todo)} file(s), {total/1024/1024:.1f} MB")
    if args.dry_run:
        for k in todo[:20]:
            print(f"   would upload {k}  ({local[k]/1024:.0f} KB)")
        if len(todo) > 20:
            print(f"   ... and {len(todo) - 20} more")
        print("\nDry run: nothing was uploaded.")
        return 0

    done = failed = 0
    for i, key in enumerate(todo, 1):
        path = os.path.join(LOCAL_ROOT, key.replace("/", os.sep))
        try:
            with open(path, "rb") as fh:
                data = fh.read()
            bucket.upload(key, data, {"upsert": "true"})
            done += 1
        except Exception as exc:      # noqa: BLE001 - one file must not stop the run
            failed += 1
            print(f"   FAILED {key}: {exc}")
        if i % 50 == 0 or i == len(todo):
            print(f"   {i}/{len(todo)}  uploaded={done} failed={failed}")

    print(f"\nuploaded {done} file(s), {failed} failure(s).")
    if failed:
        print("Re-run to retry only what is still missing.")
        return 1
    print("Re-run with --verify to confirm the bucket matches local.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
