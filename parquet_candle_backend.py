"""Candle storage as Parquet files, with metadata still in Supabase.

WHY THIS EXISTS
---------------
Candles are 99.8% of all rows in the database, and they are the wrong shape
for it: append-only, immutable once written, never updated, never joined
transactionally, always read as a contiguous time range for one symbol. That
is an analytical workload living in a transactional store.

Measured on real data (RELIANCE, 2 years of 5-minute candles):

    Postgres via PostgREST   ~120 bytes/candle    10.2 s to read
    Parquet + zstd            21.5 bytes/candle   0.22 s to read

5.6x smaller, 46x faster. The speed matters as much as the size: PostgREST
returns 1,000 rows per HTTP request, so reading a 50-symbol universe is
thousands of round trips.

WHAT STAYS IN SUPABASE
----------------------
Only the candles move. Instrument lookup, coverage and quality flags stay
where they are and are delegated to the wrapped backend, because:

  * they are tiny (thousands of rows, not millions),
  * `candle_coverage` is the honesty ledger - the record of what we genuinely
    have - and it is safer to keep that transactional and in one place.

This makes the class a DECORATOR over the Supabase backend rather than a
replacement, which is also why swapping storage does not touch anything above
`CandleBackend` (see candle_store.py).

LAYOUT
------
    {root}/{timeframe}/{instrument_id}/{year}.parquet

Partitioned by year so a backfill rewrites one bounded file rather than an
ever-growing one. Parquet has no append; a write reads the year, merges,
de-duplicates on the timestamp, and rewrites atomically.

`root` may be a local path or any fsspec URL (`s3://bucket/prefix` for
Cloudflare R2), so moving to object storage is configuration, not code.
"""

from __future__ import annotations

import io
import os
from datetime import datetime
from typing import Any, Protocol

import pandas as pd

from providers.base import OHLCV_COLUMNS, empty_frame


class ObjectStore(Protocol):
    """Where parquet bytes live. Local disk or a remote bucket."""

    def read(self, path: str) -> bytes | None: ...
    def write(self, path: str, data: bytes) -> None: ...
    def years(self, prefix: str) -> set[int]: ...


class LocalFiles:
    """Plain files under a directory. Fast, and wiped by every deploy."""

    def read(self, path: str) -> bytes | None:
        try:
            with open(path, "rb") as fh:
                return fh.read()
        except (FileNotFoundError, OSError):
            return None

    def write(self, path: str, data: bytes) -> None:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(data)

    def years(self, prefix: str) -> set[int]:
        try:
            names = os.listdir(prefix)
        except (FileNotFoundError, NotADirectoryError, OSError):
            return set()
        return _years_from_names(names)


class SupabaseStorage:
    """Objects in a Supabase Storage bucket.

    Storage is a SEPARATE quota from the database - on the free tier, 1 GB of
    files alongside 500 MB of rows. Candles are the reason the database was
    filling up, so moving them here relieves the constraint without a new
    vendor, a new account, or a second bill.

    Slower than local disk (a network hop per file) but far faster than
    reading the same candles back as database rows, and unlike local disk it
    survives a deploy.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._bucket = client.storage.from_(bucket)

    def read(self, path: str) -> bytes | None:
        try:
            return self._bucket.download(path)
        except Exception:      # noqa: BLE001 - absent is a normal outcome
            return None

    def write(self, path: str, data: bytes) -> None:
        # upsert: a refetch must be able to correct a stored year.
        self._bucket.upload(path, data, {"upsert": "true"})

    def years(self, prefix: str) -> set[int]:
        try:
            entries = self._bucket.list(prefix)
        except Exception:      # noqa: BLE001 - an absent prefix is normal
            return set()
        return _years_from_names(
            e.get("name", "") if isinstance(e, dict) else getattr(e, "name", "")
            for e in entries
        )

# zstd beats snappy by roughly 30% here at negligible CPU cost, and these files
# are written rarely and read constantly.
COMPRESSION = "zstd"

# Roots beginning with this live in a Supabase Storage bucket.
SUPABASE_SCHEME = "supabase://"


class ParquetCandleBackend:
    """CandleBackend storing candles as Parquet, delegating metadata.

    `inner` must satisfy the same protocol; everything except candle read and
    write is passed straight through to it.
    """

    def __init__(self, inner: Any, root: str, store: ObjectStore | None = None) -> None:
        self._inner = inner
        self._root = root.rstrip("/")
        self._store = store or _store_for(self._root, inner)
        # A bucket root addresses objects from the bucket root, so the scheme
        # and bucket name are not part of the object key.
        self._prefix = "" if self._is_remote else self._root

    @property
    def _is_remote(self) -> bool:
        return self._root.startswith(SUPABASE_SCHEME)

    # -- delegated, unchanged ------------------------------------------------

    def instrument_id(self, symbol: str) -> int:
        return self._inner.instrument_id(symbol)

    def read_coverage(self, instrument_id: int, timeframe: str):
        return self._inner.read_coverage(instrument_id, timeframe)

    def write_coverage(self, instrument_id: int, timeframe: str, coverage, source: str) -> None:
        self._inner.write_coverage(instrument_id, timeframe, coverage, source)

    def write_quality_flags(self, rows: list[dict[str, Any]]) -> None:
        self._inner.write_quality_flags(rows)

    def read_price_adjustments(self, instrument_id: int, timeframe: str):
        return self._inner.read_price_adjustments(instrument_id, timeframe)

    def replace_price_adjustments(self, instrument_id: int, timeframe: str, adjustments):
        return self._inner.replace_price_adjustments(
            instrument_id, timeframe, adjustments
        )

    # -- candles -------------------------------------------------------------

    def _dir(self, instrument_id: int, timeframe: str) -> str:
        leaf = f"{timeframe}/{instrument_id}"
        return f"{self._prefix}/{leaf}" if self._prefix else leaf

    def _path(self, instrument_id: int, timeframe: str, year: int) -> str:
        leaf = f"{timeframe}/{instrument_id}/{year}.parquet"
        return f"{self._prefix}/{leaf}" if self._prefix else leaf

    def _read_year(self, instrument_id: int, timeframe: str, year: int) -> pd.DataFrame:
        raw = self._store.read(self._path(instrument_id, timeframe, year))
        if raw is None:
            return empty_frame()
        frame = pd.read_parquet(io.BytesIO(raw))
        if frame.empty:
            return empty_frame()
        frame = frame.set_index("ts")
        frame.index = pd.to_datetime(frame.index, utc=True)
        return frame[list(OHLCV_COLUMNS)]

    def read_candles(
        self, instrument_id: int, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame | None:
        """Candles in [from_utc, to_utc], or None when nothing is stored.

        None and an EMPTY frame mean different things and the caller relies on
        it: None is "no file at all", empty is "a file exists but holds nothing
        in this range". Collapsing them would let a gap read as absence.
        """
        # Ask which years exist rather than probing every year in the range.
        # A wide request (a migration passes 2000-2100) would otherwise make a
        # hundred round trips, nearly all misses - invisible on local disk,
        # where a missing file fails instantly, and crippling over a network.
        stored = self._store.years(self._dir(instrument_id, timeframe))
        wanted = stored & set(range(from_utc.year, to_utc.year + 1))
        parts = [self._read_year(instrument_id, timeframe, y) for y in sorted(wanted)]
        parts = [p for p in parts if not p.empty]
        if not parts:
            return None

        frame = pd.concat(parts).sort_index()
        window = frame[(frame.index >= from_utc) & (frame.index <= to_utc)]
        return window

    def write_candles(self, instrument_id: int, timeframe: str, df: pd.DataFrame) -> None:
        """Merge candles into their year files, de-duplicating on timestamp.

        Parquet cannot be appended to, so each affected year is read, merged
        and rewritten. Existing rows LOSE to incoming ones on a timestamp
        collision: a refetch is the more recent truth, and silently keeping
        the older copy would make a correction impossible to apply.
        """
        if df.empty:
            return

        frame = df.copy()
        frame.index = pd.to_datetime(frame.index, utc=True)

        for year, chunk in frame.groupby(frame.index.year):
            path = self._path(instrument_id, timeframe, int(year))
            existing = self._read_year(instrument_id, timeframe, int(year))

            merged = chunk if existing.empty else pd.concat([existing, chunk])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()

            out = merged.reset_index().rename(columns={"index": "ts"})
            if "ts" not in out.columns:
                out = out.rename(columns={out.columns[0]: "ts"})

            buffer = io.BytesIO()
            out.to_parquet(buffer, compression=COMPRESSION, index=False)
            self._store.write(path, buffer.getvalue())


def _years_from_names(names: Any) -> set[int]:
    """Years implied by filenames like '2026.parquet'."""
    out: set[int] = set()
    for name in names:
        stem = str(name).rsplit("/", 1)[-1]
        if stem.endswith(".parquet"):
            try:
                out.add(int(stem[: -len(".parquet")]))
            except ValueError:
                continue
    return out


def _store_for(root: str, inner: Any) -> ObjectStore:
    """Pick a store from the root.

    `supabase://bucket` reuses the Supabase client the wrapped backend already
    holds, so no second set of credentials is introduced for what is the same
    account.
    """
    if root.startswith(SUPABASE_SCHEME):
        bucket = root[len(SUPABASE_SCHEME):].strip("/").split("/")[0]
        if not bucket:
            raise ValueError(
                f"{root!r} names no bucket. Use supabase://candles."
            )
        client = getattr(inner, "_client", None)
        if client is None:
            raise ValueError(
                "a supabase:// candle root needs the Supabase-backed store to "
                "wrap, because it borrows that client rather than asking for "
                "separate credentials."
            )
        return SupabaseStorage(client, bucket)
    return LocalFiles()
