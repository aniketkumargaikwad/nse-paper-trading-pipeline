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
from collections.abc import Mapping, Sequence

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
