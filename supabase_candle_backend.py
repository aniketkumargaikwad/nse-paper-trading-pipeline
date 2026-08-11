"""Supabase persistence for candles, coverage, instruments and quality flags.

Kept separate from `db.py`, which owns the trading-state tables (strategies,
positions, trades, run_audit). Candle storage is a different concern with a
very different access pattern - bulk writes and range reads - so it gets its
own module rather than growing `db.py` further.

Implements two protocols defined elsewhere:

* `candle_store.CandleBackend` - `instrument_id`, `read_candles`,
  `write_candles`, `read_coverage`, `write_coverage`, `write_quality_flags`.
* `dhan_auth.TokenStore` - `get_token`, `save_token`.

`provider_tokens` holds a live 24h Dhan bearer credential and has no anon RLS
policy for that reason. Nothing in this module ever logs, prints, or
interpolates an access token into a message - only counts, provider names and
column names are.

Row shaping is exposed as pure module-level functions (`candles_to_rows`,
`rows_to_frame`, `coverage_to_row`) so it can be tested without a database.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

import pandas as pd
from postgrest.exceptions import APIError

from config import UTC
from coverage_math import CoverageRange
from providers.base import OHLCV_COLUMNS, empty_frame

# Supabase/PostgREST rejects very large payloads in one call; candle writes
# and instrument-master writes are chunked at this size.
WRITE_CHUNK_SIZE = 1000

# Page size used when reading back a (possibly large) candle range.
READ_PAGE_SIZE = 1000


class CandleBackendError(RuntimeError):
    """A candle-storage operation failed. Message says what to fix."""


# ---------------------------------------------------------------------------
# Datetime helpers — mirror db.py's _iso/_parse_ts exactly, so behaviour is
# identical across both Supabase-backed modules.
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Serialize a datetime for Supabase, refusing naive (tz-less) values.

    A naive datetime silently interpreted as the wrong zone is the classic
    trading-system bug, so we make it impossible to store one.
    """
    if dt.tzinfo is None:
        raise ValueError(
            f"Refusing to store naive datetime {dt!r}. All timestamps must be "
            "timezone-aware (use datetime(..., tzinfo=UTC) or .astimezone(UTC))."
        )
    return dt.astimezone(UTC).isoformat()


def _parse_ts(raw: str) -> datetime:
    """Parse a PostgREST timestamp string into an aware UTC datetime."""
    # PostgREST usually emits '+00:00' offsets but 'Z' also appears in the
    # wild; normalize so fromisoformat accepts both.
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:  # defensive: should never happen for timestamptz
        raise CandleBackendError(f"Database returned a timestamp without timezone: {raw!r}")
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Pure row shaping — testable with no database.
# ---------------------------------------------------------------------------


def candles_to_rows(
    instrument_id: int, timeframe: str, df: pd.DataFrame
) -> list[dict[str, Any]]:
    """Shape a canonical candle frame as a list of `candles` table rows."""
    rows: list[dict[str, Any]] = []
    for ts, row in df.iterrows():
        rows.append({
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "ts": _iso(ts.to_pydatetime()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": int(row["volume"]),
        })
    return rows


def rows_to_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    """Rebuild a canonical candle frame from `candles` table rows.

    Sorts and de-duplicates on timestamp so a caller can hand this raw pages
    fetched in any order without corrupting the index.
    """
    if not rows:
        return empty_frame()
    df = pd.DataFrame(rows)
    index = pd.DatetimeIndex(
        pd.to_datetime(df["ts"], utc=True, format="ISO8601"), name="ts"
    )
    out = df[OHLCV_COLUMNS].astype(float)
    out.index = index
    out = out[~out.index.duplicated(keep="first")].sort_index()
    return out


def coverage_to_row(
    instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
) -> dict[str, Any]:
    """Shape a coverage range as a `candle_coverage` table row."""
    return {
        "instrument_id": instrument_id,
        "timeframe": timeframe,
        "first_ts": _iso(coverage.first_ts),
        "last_ts": _iso(coverage.last_ts),
        "source": source,
        "last_refreshed_at": _iso(datetime.now(tz=UTC)),
    }


# ---------------------------------------------------------------------------
# The backend
# ---------------------------------------------------------------------------


class SupabaseCandleBackend:
    """`CandleBackend` + `TokenStore` implementation backed by Supabase."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._instrument_ids: dict[str, int] = {}  # per-instance memo

    # -- low-level helpers ----------------------------------------------------

    def _table(self, name: str):
        return self._client.table(name)

    @staticmethod
    def _wrap(exc: Exception, doing: str) -> CandleBackendError:
        return CandleBackendError(
            f"Supabase error while {doing}: {exc}. If this says a relation "
            "does not exist, run sql/002_data_foundation.sql in the Supabase "
            "SQL editor."
        )

    # -- instruments ------------------------------------------------------------

    def instrument_id(self, symbol: str) -> int:
        """Resolve 'NSE:RELIANCE' to its `instruments.id`, memoised."""
        if symbol in self._instrument_ids:
            return self._instrument_ids[symbol]
        try:
            resp = (
                self._table("instruments")
                .select("id")
                .eq("symbol", symbol)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, f"resolving instrument {symbol}") from exc
        if not resp.data:
            raise CandleBackendError(
                f"Instrument {symbol!r} is not in the instruments table. "
                "Run `python backfill.py --refresh-instruments` first."
            )
        instrument_id = int(resp.data[0]["id"])
        self._instrument_ids[symbol] = instrument_id
        return instrument_id

    def upsert_instruments(self, rows: list[dict[str, Any]]) -> int:
        """Store security-master rows. Returns how many were written.

        Duplicate symbols are collapsed first: Postgres rejects an upsert
        whose batch contains the same conflict target twice ("ON CONFLICT DO
        UPDATE command cannot affect row a second time"), and that error names
        nothing useful. Callers apply their own precedence before this point;
        this is a last-resort guard.
        """
        if not rows:
            return 0
        deduped = {row["symbol"]: row for row in rows}
        unique_rows = list(deduped.values())
        for i in range(0, len(unique_rows), WRITE_CHUNK_SIZE):
            chunk = unique_rows[i:i + WRITE_CHUNK_SIZE]
            try:
                self._table("instruments").upsert(chunk, on_conflict="symbol").execute()
            except APIError as exc:
                raise self._wrap(exc, "writing instruments") from exc
        return len(unique_rows)

    # -- symbol groups (universes) ------------------------------------------

    def known_symbols(self) -> dict[str, int]:
        """Every symbol in the instruments table, mapped to its id.

        PAGED, and that is load-bearing: PostgREST caps an unpaged select at
        1000 rows, while this table holds ~5,200. An unpaged read returned a
        silently truncated set, which made universe resolution report real
        constituents as "not found in instruments" — the exact class of
        quietly-wrong data this codebase refuses elsewhere.
        """
        known: dict[str, int] = {}
        start = 0
        while True:
            try:
                resp = (
                    self._table("instruments")
                    .select("symbol,id")
                    .order("id")
                    .range(start, start + READ_PAGE_SIZE - 1)
                    .execute()
                )
            except APIError as exc:
                raise self._wrap(exc, "listing instruments") from exc
            page = resp.data
            known.update({row["symbol"]: int(row["id"]) for row in page})
            if len(page) < READ_PAGE_SIZE:
                return known
            start += READ_PAGE_SIZE

    def upsert_symbol_group(
        self, name: str, *, source: str, as_of: date, description: str | None = None
    ) -> int:
        """Create or update a universe row; returns its id."""
        row: dict[str, Any] = {
            "name": name,
            "source": source,
            "constituents_as_of": as_of.isoformat(),
            "is_system": source == "nse",
        }
        if description is not None:
            row["description"] = description
        try:
            self._table("symbol_groups").upsert(row, on_conflict="name").execute()
            resp = self._table("symbol_groups").select("id").eq("name", name).execute()
        except APIError as exc:
            raise self._wrap(exc, f"saving universe {name}") from exc
        if not resp.data:
            raise RuntimeError(f"symbol_group {name!r} vanished immediately after upsert")
        return int(resp.data[0]["id"])

    def replace_group_members(self, group_id: int, instrument_ids: list[int]) -> None:
        """Set a universe membership to exactly `instrument_ids`.

        Delete-then-insert rather than upsert: an index rebalance REMOVES
        names, and an upsert would leave dropped constituents in the group
        forever — quietly backtesting a universe that no longer exists.
        """
        try:
            self._table("symbol_group_members").delete().eq("group_id", group_id).execute()
            if instrument_ids:
                self._table("symbol_group_members").insert(
                    [{"group_id": group_id, "instrument_id": i} for i in instrument_ids]
                ).execute()
        except APIError as exc:
            raise self._wrap(exc, f"writing members of group {group_id}") from exc

    # -- candles ------------------------------------------------------------

    def read_candles(
        self, instrument_id: int, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame | None:
        """Return stored candles in [from_utc, to_utc], or None if none exist.

        `None` (not an empty frame) is the CandleBackend protocol's contract
        for "nothing cached" - CandleStore relies on that to distinguish "no
        coverage yet" from "coverage exists but this slice is empty".
        """
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            try:
                resp = (
                    self._table("candles")
                    .select("*")
                    .eq("instrument_id", instrument_id)
                    .eq("timeframe", timeframe)
                    .gte("ts", _iso(from_utc))
                    .lte("ts", _iso(to_utc))
                    .order("ts")
                    .range(start, start + READ_PAGE_SIZE - 1)
                    .execute()
                )
            except APIError as exc:
                raise self._wrap(exc, "reading candles") from exc
            page = resp.data
            rows.extend(page)
            if len(page) < READ_PAGE_SIZE:
                break
            start += READ_PAGE_SIZE
        return rows_to_frame(rows) if rows else None

    def write_candles(self, instrument_id: int, timeframe: str, df: pd.DataFrame) -> None:
        """Upsert candles, chunked, on the (instrument, timeframe, ts) key.

        Upserting (not inserting) is what makes a re-run over an already
        stored range idempotent instead of erroring on a duplicate key.
        """
        rows = candles_to_rows(instrument_id, timeframe, df)
        for i in range(0, len(rows), WRITE_CHUNK_SIZE):
            chunk = rows[i:i + WRITE_CHUNK_SIZE]
            try:
                self._table("candles").upsert(
                    chunk, on_conflict="instrument_id,timeframe,ts"
                ).execute()
            except APIError as exc:
                raise self._wrap(exc, "writing candles") from exc

    # -- coverage ------------------------------------------------------------

    def read_coverage(self, instrument_id: int, timeframe: str) -> CoverageRange | None:
        try:
            resp = (
                self._table("candle_coverage")
                .select("*")
                .eq("instrument_id", instrument_id)
                .eq("timeframe", timeframe)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading coverage") from exc
        if not resp.data:
            return None
        row = resp.data[0]
        return CoverageRange(
            first_ts=_parse_ts(row["first_ts"]),
            last_ts=_parse_ts(row["last_ts"]),
        )

    def write_coverage(
        self, instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
    ) -> None:
        try:
            self._table("candle_coverage").upsert(
                coverage_to_row(instrument_id, timeframe, coverage, source),
                on_conflict="instrument_id,timeframe",
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "writing coverage") from exc

    # -- quality flags --------------------------------------------------------

    def write_quality_flags(self, rows: list[dict[str, Any]]) -> None:
        """Upsert detected quality issues.

        Upsert (not insert): the table has UNIQUE(instrument_id, timeframe,
        flag_type, ts), and detection re-runs on every fetch, so a plain
        insert would raise a unique-violation the second time the same issue
        is (re-)detected. Upserting makes redetection idempotent.
        """
        if not rows:
            return
        try:
            self._table("data_quality_flags").upsert(
                list(rows), on_conflict="instrument_id,timeframe,flag_type,ts"
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "writing quality flags") from exc

    # -- provider token (dhan_auth.TokenStore) ---------------------------------

    def get_token(self, provider: str):
        """TokenStore protocol: read the cached provider token, or None."""
        from dhan_auth import StoredToken

        try:
            resp = (
                self._table("provider_tokens")
                .select("*")
                .eq("provider", provider)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading the provider token") from exc
        if not resp.data:
            return None
        row = resp.data[0]
        return StoredToken(
            access_token=row["access_token"],
            expires_at=_parse_ts(row["expires_at"]),
        )

    def save_token(self, provider: str, token) -> None:
        """TokenStore protocol: cache a provider token. Value never logged."""
        try:
            self._table("provider_tokens").upsert(
                {
                    "provider": provider,
                    "access_token": token.access_token,
                    "expires_at": _iso(token.expires_at),
                    "updated_at": _iso(datetime.now(tz=UTC)),
                },
                on_conflict="provider",
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "saving the provider token") from exc
