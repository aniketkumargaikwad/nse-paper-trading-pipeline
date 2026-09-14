"""Where the candle store lives on both sides: this disk, and the bucket.

The uploader and the downloader need the same three things - what is here,
what is there, and which paths differ - so they live here rather than in one
script that the other has to import across the `scripts/` boundary.

Sizes, not checksums: Storage does not return a content hash cheaply, and a
year file that changed content without changing length is not a thing that
happens - it grows as candles are added.
"""

from __future__ import annotations

import os
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor

LOCAL_ROOT = "data/candles"
BUCKET = "candles"

# What a research day actually reads. Every intraday timeframe is resampled
# from 5m, and `1m` is never touched - listing it costs nothing but
# downloading it spends metered egress on data no sweep will open.
RESEARCH_TIMEFRAMES: tuple[str, ...] = ("5m", "60m", "day")

# Supabase Storage returns at most 100 entries per list() call and gives no
# hint that it truncated. With 200 instrument directories under 5m/, an
# unpaged listing silently sees half of them - which made --verify
# permanently report ~2,400 files "missing" and the uploader re-send files
# already in the bucket. Paginate explicitly; never trust one call to be the
# whole directory.
_PAGE = 1000

# Storage lists one directory at a time, and the store has about 400 of them.
# Walked one after another that is a minute or two of pure round trip, paid on
# every run whether or not anything needs fetching.
LIST_WORKERS = 8
LIST_ATTEMPTS = 4


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


class ListingFailed(RuntimeError):
    """A directory could not be listed, so the bucket contents are unknown."""


def _page(bucket, prefix: str, offset: int) -> list:
    """One page, retried. A refusal must never look like an empty directory.

    Storage throttles a burst of concurrent listings, and the first version
    of this swallowed that and returned []. The uploader read it as "missing,
    re-upload" - wasteful but safe. The DOWNLOADER read it as "not in the
    bucket, skip", which silently leaves a research day sweeping half a
    candle store and reporting the numbers as though they were whole.
    """
    last: Exception | None = None
    for attempt in range(LIST_ATTEMPTS):
        try:
            return bucket.list(prefix, {"limit": _PAGE, "offset": offset}) or []
        except Exception as exc:      # noqa: BLE001 - retried, then raised
            last = exc
            time.sleep(0.4 * (attempt + 1))
    raise ListingFailed(f"could not list {prefix!r} after {LIST_ATTEMPTS} attempts: {last}")


def _list_all(bucket, prefix: str) -> list:
    """Every entry under `prefix`, following pages to the end."""
    entries: list = []
    offset = 0
    while True:
        page = _page(bucket, prefix, offset)
        if not page:
            return entries
        entries.extend(page)
        if len(page) < _PAGE:
            return entries
        offset += len(page)


def remote_files(bucket, *, workers: int = LIST_WORKERS) -> dict[str, int]:
    """Every object in the bucket, keyed by path, valued by size.

    Storage lists one directory level at a time, so this walks the same
    {timeframe}/{instrument_id}/{year}.parquet shape the writer produces -
    but a whole level at once, because each listing is a round trip and the
    levels are wide (200 instrument directories under 5m alone).
    """
    found: dict[str, int] = {}
    level = [""]

    def children(prefix: str) -> tuple[dict[str, int], list[str]]:
        files: dict[str, int] = {}
        folders: list[str] = []
        for entry in _list_all(bucket, prefix):
            name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", "")
            if not name:
                continue
            path = f"{prefix}/{name}" if prefix else name
            meta = entry.get("metadata") if isinstance(entry, dict) else None
            size = (meta or {}).get("size")
            if size is not None:
                files[path] = int(size)
            else:
                folders.append(path)
        return files, folders

    for _depth in range(3):
        if not level:
            break
        with ThreadPoolExecutor(max_workers=min(workers, len(level))) as pool:
            results = list(pool.map(children, level))
        level = []
        for files, folders in results:
            found.update(files)
            level.extend(folders)
    return found


def missing_locally(
    remote: Mapping[str, int],
    local: Mapping[str, int],
    *,
    timeframes: Sequence[str] | None = None,
) -> list[str]:
    """Bucket paths this disk does not have, or has at a different size."""
    wanted = tuple(timeframes) if timeframes is not None else None
    return sorted(
        path for path, size in remote.items()
        if (wanted is None or path.split("/", 1)[0] in wanted)
        and local.get(path) != size
    )
