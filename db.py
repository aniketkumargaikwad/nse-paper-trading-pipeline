"""Thin, typed data-access layer over Supabase.

Every piece of persistent state in the system flows through this module, so
the engines never build queries inline. Each method:

* takes/returns plain Python types or the dataclasses below (never raw JSON),
* converts all timestamps to timezone-aware UTC datetimes,
* turns the two "expected" conflict cases (position already open, trade
  already recorded) into calm return values instead of exceptions — that is
  what makes engine re-runs for the same candle idempotent,
* wraps connection/credential failures in messages that say what to fix.

Run a connectivity self-test any time with:

    python db.py            # read-only: checks every table is reachable
    python db.py --write    # also inserts one run_audit row (run_type=selftest)
"""

from __future__ import annotations

import argparse
import json

import yaml
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Sequence

from postgrest.exceptions import APIError
from supabase import Client, create_client

from config import UTC, Settings
from universes import UniverseError
from strategy_schema import (
    CURRENT_VERSION,
    MigrationError,
    Strategy,
    migrate_document,
    parse_strategy_dict,
)
from strategy.v3 import CURRENT_V3_VERSION, is_v3_document

# Postgres error code for unique-constraint violations. We treat these as
# "someone (a previous run) already did this" — the core of idempotency.
_PG_UNIQUE_VIOLATION = "23505"


class DatabaseError(RuntimeError):
    """Raised when Supabase is unreachable or a query fails unexpectedly."""


# ---------------------------------------------------------------------------
# Datetime helpers — everything in this system is stored as UTC.
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
        raise DatabaseError(f"Database returned a timestamp without timezone: {raw!r}")
    return dt.astimezone(UTC)


# ---------------------------------------------------------------------------
# Row dataclasses — the shapes the engines work with.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenPosition:
    """One OPEN simulated position (mirror of a `positions` row)."""

    id: str
    strategy_name: str
    instrument: str
    position_type: str  # 'long' | 'short'
    quantity: int
    entry_signal_candle_ts: datetime
    entry_fill_ts: datetime
    intended_entry_price: float
    entry_price: float
    stop_loss_price: float
    target_price: float

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "OpenPosition":
        return cls(
            id=row["id"],
            strategy_name=row["strategy_name"],
            instrument=row["instrument"],
            position_type=row["position_type"],
            quantity=int(row["quantity"]),
            entry_signal_candle_ts=_parse_ts(row["entry_signal_candle_ts"]),
            entry_fill_ts=_parse_ts(row["entry_fill_ts"]),
            intended_entry_price=float(row["intended_entry_price"]),
            entry_price=float(row["entry_price"]),
            stop_loss_price=float(row["stop_loss_price"]),
            target_price=float(row["target_price"]),
        )


@dataclass(frozen=True)
class ClosedTrade:
    """One COMPLETED simulated round-trip (mirror of a `trades` row)."""

    strategy_name: str
    instrument: str
    position_type: str
    quantity: int
    entry_signal_candle_ts: datetime
    entry_fill_ts: datetime
    intended_entry_price: float
    entry_price: float
    exit_signal_candle_ts: datetime
    exit_fill_ts: datetime
    intended_exit_price: float
    exit_price: float
    exit_reason: str  # 'signal' | 'stop_loss' | 'target' | 'end_of_day'
    gross_pnl: float
    costs: float
    net_pnl: float


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


def to_native(value: Any) -> Any:
    """Convert numpy scalars to plain Python types, recursively.

    The simulator reads prices out of numpy arrays, so a trade's net_pnl is a
    numpy.float64 and every comparison built from it - `net_pnl > 0` in the
    kill-rule flags - is a numpy.bool_. Neither survives JSON encoding, so an
    otherwise complete backtest died at the insert with "Object of type bool_
    is not JSON serializable", losing every row it had just spent minutes
    computing.

    Applied at the database boundary rather than at each call site: the
    simulator is entitled to use numpy, and every writer would otherwise have
    to remember this independently. Anything without .item() passes through
    untouched, so dates, strings and None are unaffected.
    """
    if isinstance(value, dict):
        return {k: to_native(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_native(v) for v in value]
    item = getattr(value, "item", None)
    if item is not None and hasattr(value, "dtype"):
        return item()
    return value


def canonical_definition_hash(definition: Mapping[str, Any]) -> str:
    """sha256 over the definition with sorted keys and no whitespace.

    Canonical so that two saves of the same rules produce the same hash
    regardless of key order or formatting. Without that, re-saving an
    unchanged strategy would mint a new version every time and the history
    would fill with noise until "which version did I test?" stopped having a
    useful answer.
    """
    import hashlib

    payload = json.dumps(definition, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SaveResult:
    """Outcome of saving a strategy: valid and runnable, or a stored draft."""

    name: str
    status: str                     # 'valid' | 'draft'
    strategy: Strategy | None       # populated only when valid
    errors: tuple[str, ...] = ()
    version: int | None = None      # None for drafts (nothing to version)
    version_id: int | None = None
    created_new_version: bool = False

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"


def _is_missing_table(exc: Exception) -> bool:
    """Whether an error means the table has not been created yet.

    PostgREST words this two ways: a plain Postgres 'relation ... does not
    exist' and its own PGRST205 'Could not find the table ... in the schema
    cache'. A guard that knew only the first one let a missing table abort a
    finished backtest, which is precisely what the guard existed to prevent.
    """
    message = str(exc).lower()
    return "does not exist" in message or "not find the table" in message


class SupabaseStore:
    """All reads/writes the pipeline performs against Supabase."""

    def __init__(self, client: Client) -> None:
        self._client = client

    # -- construction -------------------------------------------------------

    @classmethod
    def connect(cls, settings: Settings) -> "SupabaseStore":
        """Connect using validated Settings (the engines' entry point)."""
        return cls._create(settings.supabase_url, settings.supabase_service_role_key)

    @classmethod
    def from_env(cls) -> "SupabaseStore":
        """Connect using only the two Supabase env vars.

        Used by the self-test and login script so they work before the user
        has set up their Kite keys.
        """
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        missing = [n for n, v in
                   [("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)] if not v]
        if missing:
            raise DatabaseError(
                "Missing environment variable(s): " + ", ".join(missing)
                + ". Copy .env.example to .env and fill them in "
                "(Supabase dashboard -> Project Settings -> API)."
            )
        return cls._create(url, key)

    @classmethod
    def _create(cls, url: str, key: str) -> "SupabaseStore":
        try:
            client = create_client(url, key)
        except Exception as exc:  # supabase-py raises assorted types here
            raise DatabaseError(
                f"Could not create Supabase client for {url!r}: {exc}. "
                "Check SUPABASE_URL (https://<ref>.supabase.co) and the key."
            ) from exc
        return cls(client)

    # -- low-level helpers ---------------------------------------------------

    def _table(self, name: str):
        return self._client.table(name)

    @staticmethod
    def _is_unique_violation(exc: APIError) -> bool:
        return getattr(exc, "code", None) == _PG_UNIQUE_VIOLATION

    @staticmethod
    def _wrap(exc: Exception, doing: str) -> DatabaseError:
        return DatabaseError(
            f"Supabase error while {doing}: {exc}. "
            "If this says a relation does not exist, run sql/001_init.sql in "
            "the Supabase SQL editor. If it mentions JWT/authorization, check "
            "SUPABASE_SERVICE_ROLE_KEY."
        )

    # -- daily token ---------------------------------------------------------

    def save_daily_token(self, token_date: date, access_token: str) -> None:
        """Upsert the day's Kite access token (login.py, each morning)."""
        if not access_token.strip():
            raise ValueError("Refusing to store an empty access token.")
        try:
            self._table("daily_token").upsert(
                {"token_date": token_date.isoformat(), "access_token": access_token},
                on_conflict="token_date",
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "saving the daily token") from exc

    def get_daily_token(self, token_date: date) -> str | None:
        """Return the access token for an IST trading date, or None."""
        try:
            resp = (
                self._table("daily_token")
                .select("access_token")
                .eq("token_date", token_date.isoformat())
                .limit(1)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading the daily token") from exc
        return resp.data[0]["access_token"] if resp.data else None

    # -- strategies snapshot --------------------------------------------------

    # -- strategies as LIVE state (the UI edits these; the engine reads them) --

    def list_strategy_documents(self) -> list[dict[str, Any]]:
        """Return the raw strategy dicts stored in the database."""
        try:
            resp = self._table("strategies").select("*").order("name").execute()
        except APIError as exc:
            raise self._wrap(exc, "listing strategies") from exc
        docs = []
        for row in resp.data:
            doc = dict(row.get("definition") or {})
            doc["name"] = row["name"]
            doc["status"] = row.get("status", "valid")
            doc["raw_source"] = row.get("raw_source")
            doc["validation_errors"] = row.get("validation_errors") or []
            # `enabled` lives in its own column so it can be toggled cheaply;
            # it always wins over any stale copy inside the definition blob.
            # A draft has no enabled value at all, hence the None guard.
            if row.get("enabled") is not None:
                doc["enabled"] = bool(row["enabled"])
            docs.append(doc)
        return docs

    def list_strategies(self) -> list[Strategy]:
        """Load and VALIDATE every stored strategy.

        Validation happens on read (not just on write) so a row edited
        directly in the Supabase table editor can never feed the engine
        something malformed.
        """
        strategies = []
        for doc in self.list_strategy_documents():
            # Drafts are blocked HERE and only here. Every engine loads
            # through this method, so one filter is the whole enforcement.
            if doc.get("status") != "valid":
                continue
            doc = {k: v for k, v in doc.items()
                   if k not in ("status", "raw_source", "validation_errors")}
            try:
                # A v3 document is a state machine, not a condition tree, so
                # it needs its own parser. Both satisfy what the engines read
                # (name, timeframe, sizing, universe/instruments), which is
                # what lets one backtest run mix the two formats.
                if doc.get("version") == CURRENT_V3_VERSION:
                    from strategy.v3 import parse_machine

                    strategies.append(parse_machine(doc))
                else:
                    strategies.append(
                        parse_strategy_dict(doc, where=f"strategy {doc.get('name')!r}")
                    )
            except ValueError as exc:
                raise DatabaseError(
                    f"Stored strategy {doc.get('name')!r} is invalid: {exc}. "
                    "Fix it on the Strategies page (or delete it)."
                ) from exc
        return strategies

    def known_universe_names(self) -> set[str]:
        """Names of every universe currently in symbol_groups."""
        try:
            resp = self._table("symbol_groups").select("name").execute()
        except APIError as exc:
            raise self._wrap(exc, "listing universes") from exc
        return {row["name"] for row in resp.data}

    def universe_members(self, name: str) -> tuple[tuple[str, ...], str | None]:
        """(symbols, constituents_as_of) for a named universe.

        PAGED, and that is load-bearing: PostgREST caps an unpaged select at
        1000 rows, and NIFTY500 has more members than that would return. An
        unpaged read would silently shrink the universe being tested and
        present the result as if it covered the whole index.
        """
        try:
            groups = (
                self._table("symbol_groups")
                .select("id,constituents_as_of")
                .eq("name", name)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, f"reading universe {name}") from exc
        if not groups.data:
            known = ", ".join(sorted(self.known_universe_names())) or "(none)"
            raise UniverseError(
                f"unknown universe {name!r}. Available: {known}. "
                "Create it with scripts/refresh_universes.py."
            )
        group_id = groups.data[0]["id"]
        as_of = groups.data[0].get("constituents_as_of")

        instrument_ids: list[int] = []
        start = 0
        while True:
            page = (
                self._table("symbol_group_members")
                .select("instrument_id")
                .eq("group_id", group_id)
                .order("instrument_id")
                .range(start, start + 999)
                .execute()
            ).data
            instrument_ids += [r["instrument_id"] for r in page]
            if len(page) < 1000:
                break
            start += 1000

        symbols: list[str] = []
        for i in range(0, len(instrument_ids), 200):
            chunk = instrument_ids[i:i + 200]
            rows = (
                self._table("instruments").select("symbol").in_("id", chunk).execute()
            ).data
            symbols += [r["symbol"] for r in rows]
        return tuple(sorted(symbols)), as_of

    def save_custom_universe(
        self, name: str, symbols: list[str]
    ) -> tuple[int, list[str]]:
        """Create or replace a user-defined universe. Returns (stored, unknown).

        Symbols absent from the instruments table are REPORTED, never silently
        dropped: a group that quietly holds four of the five names you typed
        would make every result computed over it subtly wrong, and nothing
        would say so.

        Membership is replaced rather than merged. Editing a group is how you
        remove a symbol, and a merge would make removal impossible.
        """
        from strategy_schema import UNIVERSE_RE

        name = (name or "").strip().upper()
        if not UNIVERSE_RE.match(name):
            raise DatabaseError(
                f"{name!r} is not a valid universe name. Use capitals, digits "
                "and underscores, 2 to 40 characters - e.g. MY_BANKS."
            )

        wanted = []
        for raw in symbols:
            symbol = (raw or "").strip().upper()
            if symbol and symbol not in wanted:
                wanted.append(symbol)
        if not wanted:
            raise DatabaseError(
                "a universe needs at least one symbol. An empty group would "
                "produce a zero-trade backtest indistinguishable from a "
                "strategy that never triggered."
            )

        known: dict[str, int] = {}
        for i in range(0, len(wanted), 200):
            chunk = wanted[i:i + 200]
            try:
                rows = (
                    self._table("instruments").select("id,symbol")
                    .in_("symbol", chunk).execute()
                ).data
            except APIError as exc:
                raise self._wrap(exc, "resolving universe symbols") from exc
            known.update({r["symbol"]: int(r["id"]) for r in rows})

        unknown = [s for s in wanted if s not in known]
        resolved = [s for s in wanted if s in known]
        if not resolved:
            raise DatabaseError(
                f"none of those symbols are in the instruments table: "
                f"{', '.join(wanted[:10])}. Check the EXCHANGE:SYMBOL spelling, "
                "or refresh the instrument list with "
                "backfill.py --refresh-instruments."
            )

        try:
            self._table("symbol_groups").upsert(
                {
                    "name": name,
                    "source": "custom",
                    "is_system": False,
                    "constituents_as_of": _iso(datetime.now(tz=UTC))[:10],
                },
                on_conflict="name",
            ).execute()
            group = (
                self._table("symbol_groups").select("id").eq("name", name).execute()
            ).data
            group_id = int(group[0]["id"])

            self._table("symbol_group_members").delete().eq(
                "group_id", group_id
            ).execute()
            self._table("symbol_group_members").insert(
                [{"group_id": group_id, "instrument_id": known[s]} for s in resolved]
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, f"saving universe {name}") from exc

        return len(resolved), unknown

    def delete_universe(self, name: str) -> None:
        """Remove a CUSTOM universe. System universes are refused.

        A system universe is rebuilt from NSE by refresh_universes.py, so
        deleting one here would be undone silently on the next refresh - and
        would meanwhile break any strategy pointing at it.
        """
        try:
            rows = (
                self._table("symbol_groups").select("id,source")
                .eq("name", name).execute()
            ).data
        except APIError as exc:
            raise self._wrap(exc, f"reading universe {name}") from exc
        if not rows:
            raise DatabaseError(f"no universe named {name!r}.")
        if rows[0].get("source") == "nse":
            raise DatabaseError(
                f"{name!r} comes from NSE and is refreshed by "
                "scripts/refresh_universes.py. Deleting it here would be undone "
                "on the next refresh."
            )
        try:
            self._table("symbol_groups").delete().eq("name", name).execute()
        except APIError as exc:
            raise self._wrap(exc, f"deleting universe {name}") from exc

    def list_universes(self) -> list[dict[str, Any]]:
        """Every universe with its size and provenance."""
        try:
            groups = (
                self._table("symbol_groups")
                .select("id,name,source,constituents_as_of").execute()
            ).data
        except APIError as exc:
            raise self._wrap(exc, "listing universes") from exc

        out = []
        for g in groups:
            try:
                count = (
                    self._table("symbol_group_members")
                    .select("instrument_id", count="exact")
                    .eq("group_id", g["id"]).limit(1).execute()
                ).count or 0
            except APIError:
                count = 0
            out.append({
                "name": g["name"],
                "source": g.get("source") or "custom",
                "as_of": g.get("constituents_as_of"),
                "members": count,
            })
        return sorted(out, key=lambda r: (r["source"] != "custom", r["name"]))

    def save_strategy_document(
        self, doc: Mapping[str, Any], *, raw_source: str | None = None
    ) -> SaveResult:
        """Create or update one strategy, storing it as a draft if invalid.

        A strategy arriving from an external AI tool is EXPECTED to be wrong on
        the first attempt, so rejecting it outright would mean retyping or
        re-prompting from scratch. Drafts are stored with the text they came
        from and their errors, are editable, and are blocked from every engine
        by list_strategies().
        """
        doc = dict(doc)
        name = doc.get("name")
        if not isinstance(name, str) or not name.strip():
            # The one thing a draft cannot be missing: it is the primary key,
            # so there would be nowhere to put the draft.
            raise DatabaseError(
                "a strategy needs a 'name' before it can be saved, even as a "
                "draft - the name is its primary key."
            )
        name = name.strip()

        errors: list[str] = []
        strategy: Any = None
        try:
            # Validated on save AND on read, by the same parser either way.
            # A v3 document goes through the state-machine validator, which is
            # where an unreachable state or a variable nothing sets is caught
            # — all failures that would otherwise produce a clean, plausible
            # backtest of a strategy that never fires.
            if doc.get("version") == CURRENT_V3_VERSION:
                from strategy.v3 import parse_machine

                strategy = parse_machine(doc)
            else:
                strategy = parse_strategy_dict(doc, where="strategy")
        except ValueError as exc:
            errors.append(str(exc))

        # Universe existence needs the database, so it cannot live in the pure
        # parser - validating a pasted strategy has to work offline. It is
        # checked here instead, on the same save.
        if strategy is not None and strategy.universe:
            known = self.known_universe_names()
            if strategy.universe not in known:
                available = ", ".join(sorted(known)) or "(none defined yet)"
                errors.append(
                    f"strategy.universe: unknown universe "
                    f"{strategy.universe!r}. Available: {available}. "
                    "Create it with scripts/refresh_universes.py."
                )
                strategy = None

        now = _iso(datetime.now(tz=UTC))
        if strategy is not None:
            row = {
                "name": name,
                "enabled": strategy.enabled,
                "position_type": strategy.position_type,
                "timeframe": strategy.timeframe,
                "definition": json.loads(json.dumps(doc, default=str)),
                "raw_source": raw_source,
                # The format the definition is actually IN. Stamping every row
                # with CURRENT_VERSION would label a v3 machine as v2, and the
                # read path picks its parser from this.
                "format_version": (
                    CURRENT_V3_VERSION
                    if doc.get("version") == CURRENT_V3_VERSION
                    else CURRENT_VERSION
                ),
                "status": "valid",
                "validation_errors": None,
                "updated_at": now,
            }
        else:
            row = {
                "name": name,
                "enabled": None,
                "position_type": None,
                "timeframe": None,
                "definition": None,
                "raw_source": raw_source,
                "format_version": None,
                "status": "draft",
                "validation_errors": errors,
                "updated_at": now,
            }

        try:
            self._table("strategies").upsert(row, on_conflict="name").execute()
        except APIError as exc:
            raise self._wrap(exc, f"saving strategy {name}") from exc

        version = version_id = None
        created_new = False
        if strategy is not None:
            # Only valid strategies are versioned. A draft has no coherent
            # definition to snapshot, and versioning one would create a
            # history entry nothing could ever reproduce.
            version, version_id, created_new = self._record_version(
                name, row["definition"], row["format_version"]
            )

        return SaveResult(
            name=name, status=row["status"], strategy=strategy,
            errors=tuple(errors), version=version, version_id=version_id,
            created_new_version=created_new,
        )

    def _record_version(
        self, name: str, definition: Mapping[str, Any], format_version: int
    ) -> tuple[int | None, int | None, bool]:
        """Snapshot a definition, reusing the current version if unchanged.

        Returns (version_number, version_id, created_new). Versions are
        immutable: this only ever inserts, never updates an existing one.

        Degrades to (None, None, False) if the strategy_versions table is
        absent, so a database that has not had sql/005 applied keeps saving
        strategies instead of failing outright - the feature is unavailable,
        not the app.
        """
        digest = canonical_definition_hash(definition)
        try:
            existing = (
                self._table("strategy_versions")
                .select("id,version,definition_hash")
                .eq("strategy_name", name)
                .order("version", desc=True)
                .execute()
            ).data
        except APIError as exc:
            if "does not exist" in str(exc).lower():
                return None, None, False
            raise self._wrap(exc, f"reading versions of {name}") from exc

        for row in existing:
            if row.get("definition_hash") == digest:
                # Identical content: reuse rather than duplicate.
                self._point_current_version(name, row["id"])
                return int(row["version"]), int(row["id"]), False

        next_version = (max((int(r["version"]) for r in existing), default=0)) + 1
        try:
            inserted = (
                self._table("strategy_versions")
                .insert({
                    "strategy_name": name,
                    "version": next_version,
                    "definition": to_native(dict(definition)),
                    "definition_hash": digest,
                    "format_version": format_version,
                })
                .execute()
            ).data
        except APIError as exc:
            raise self._wrap(exc, f"versioning strategy {name}") from exc

        version_id = int(inserted[0]["id"]) if inserted else None
        if version_id is not None:
            self._point_current_version(name, version_id)
        return next_version, version_id, True

    def _point_current_version(self, name: str, version_id: int) -> None:
        try:
            self._table("strategies").update(
                {"current_version_id": version_id}
            ).eq("name", name).execute()
        except APIError as exc:
            raise self._wrap(exc, f"setting current version of {name}") from exc

    def strategy_versions(self, name: str) -> list[dict[str, Any]]:
        """Every stored version of one strategy, newest first."""
        try:
            return (
                self._table("strategy_versions")
                .select("id,version,definition,definition_hash,format_version,created_at")
                .eq("strategy_name", name)
                .order("version", desc=True)
                .execute()
            ).data
        except APIError as exc:
            if "does not exist" in str(exc).lower():
                return []
            raise self._wrap(exc, f"reading versions of {name}") from exc

    def strategy_version_by_id(self, version_id: int) -> dict[str, Any] | None:
        """One immutable version snapshot, by id.

        This is what makes a stored result reproducible. Re-running from the
        strategy's CURRENT definition would answer a different question — it
        would test what the strategy says today, not what produced the result
        being reproduced, and the two silently diverge the moment it is edited.
        """
        try:
            rows = (
                self._table("strategy_versions")
                .select("id,strategy_name,version,definition,format_version,created_at")
                .eq("id", version_id)
                .limit(1)
                .execute()
            ).data
        except APIError as exc:
            if "does not exist" in str(exc).lower():
                return None
            raise self._wrap(exc, f"reading strategy version {version_id}") from exc
        return rows[0] if rows else None

    def backtest_run_rows(self, batch_id: str) -> list[dict[str, Any]]:
        """Every run row in one batch — the config a re-run needs.

        A run row already records the window, the universe, the exact symbol
        list, the cost model and the strategy version. That IS the config; it
        just was never read back.
        """
        try:
            return (
                self._table("backtest_runs")
                .select("*")
                .eq("batch_id", batch_id)
                .execute()
            ).data
        except APIError as exc:
            if "does not exist" in str(exc).lower():
                return []
            raise self._wrap(exc, f"reading backtest run {batch_id}") from exc

    def current_version_id(self, name: str) -> int | None:
        """The version id a run should record for this strategy."""
        try:
            rows = (
                self._table("strategies").select("current_version_id")
                .eq("name", name).execute()
            ).data
        except APIError as exc:
            if "does not exist" in str(exc).lower():
                return None
            raise self._wrap(exc, f"reading current version of {name}") from exc
        return rows[0]["current_version_id"] if rows else None

    def save_strategy_text(self, text: str) -> SaveResult:
        """Save a strategy pasted as YAML text.

        Accepts either a bare strategy mapping or a full document with a
        `version:` and `strategies:` list - an external AI tool will produce
        either, and both should just work.
        """
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            # No document at all means no name to key a draft on, so this is
            # the one paste failure that cannot become a draft.
            raise DatabaseError(
                f"the pasted text is not valid YAML: {exc}\n"
                "Common causes: inconsistent indentation, a missing ':', or an "
                "unquoted '>' operator (write operator: \">\")."
            ) from exc

        if is_v3_document(data):
            # A v3 document is already a single strategy. Sending it through
            # the v1->v2 migration would reject it for lacking `entry:`.
            return self.save_strategy_document(data, raw_source=text)

        if isinstance(data, dict) and "strategies" in data:
            try:
                data = migrate_document(data)
            except MigrationError as exc:
                raise DatabaseError(str(exc)) from exc
            entries = data.get("strategies") or []
            if len(entries) != 1:
                raise DatabaseError(
                    f"expected exactly one strategy in the pasted text, "
                    f"found {len(entries)}. Paste them one at a time."
                )
            doc = entries[0]
        else:
            doc = data

        if not isinstance(doc, dict):
            raise DatabaseError(
                "the pasted text is not a strategy: expected a mapping of "
                "key: value lines."
            )
        return self.save_strategy_document(doc, raw_source=text)

    def set_strategy_enabled(self, name: str, enabled: bool) -> None:
        """Flip a strategy live/paused — the 'one-click deploy' action."""
        try:
            self._table("strategies").update(
                {"enabled": enabled, "updated_at": _iso(datetime.now(tz=UTC))}
            ).eq("name", name).execute()
        except APIError as exc:
            raise self._wrap(exc, f"toggling strategy {name}") from exc

    def delete_strategy(self, name: str) -> None:
        """Remove a strategy definition.

        Its historical trades are intentionally KEPT (they are real results);
        they simply no longer have a live parent row.
        """
        try:
            self._table("strategies").delete().eq("name", name).execute()
        except APIError as exc:
            raise self._wrap(exc, f"deleting strategy {name}") from exc

    def seed_strategies_if_empty(self, documents: Sequence[Mapping[str, Any]]) -> int:
        """First-run bootstrap: copy strategies.yaml into the database.

        Does nothing once any strategy exists, so it can never overwrite what
        you have since edited in the UI. Returns how many rows were seeded.
        """
        try:
            resp = self._table("strategies").select("name", count="exact").limit(1).execute()
        except APIError as exc:
            raise self._wrap(exc, "checking for existing strategies") from exc
        if (resp.count or 0) > 0:
            return 0
        for doc in documents:
            self.save_strategy_document(doc)
        return len(documents)

    # -- v3 machine state -----------------------------------------------------

    def get_machine_state(self, strategy_name: str, instrument: str) -> dict[str, Any] | None:
        """Where a state machine was left, or None if it has never run.

        None means "start from the initial state", which is also the right
        answer for a machine that finished a cycle — see clear_machine_state.
        """
        try:
            resp = (
                self._table("machine_state")
                .select("state,variables,bars_in_state,last_candle_ts")
                .eq("strategy_name", strategy_name)
                .eq("instrument", instrument)
                .limit(1)
                .execute()
            )
        except APIError as exc:
            if _is_missing_table(exc):
                raise DatabaseError(
                    "the machine_state table does not exist. Run "
                    "sql/008_machine_state.sql in the Supabase SQL editor "
                    "before paper trading a version 3 strategy."
                ) from exc
            raise self._wrap(exc, "reading machine state") from exc

        if not resp.data:
            return None
        row = resp.data[0]
        return {
            "state": row["state"],
            "variables": dict(row.get("variables") or {}),
            "bars_in_state": int(row.get("bars_in_state") or 0),
            "last_candle_ts": (
                _parse_ts(row["last_candle_ts"]) if row.get("last_candle_ts") else None
            ),
        }

    def save_machine_state(
        self, strategy_name: str, instrument: str, *,
        state: str, variables: Mapping[str, Any], bars_in_state: int,
        last_candle_ts: datetime,
    ) -> None:
        """Persist where a machine is, so the next stateless run resumes it."""
        row = {
            "strategy_name": strategy_name,
            "instrument": instrument,
            "state": state,
            "variables": to_native(dict(variables)),
            "bars_in_state": int(bars_in_state),
            "last_candle_ts": _iso(last_candle_ts),
            "updated_at": _iso(datetime.now(tz=UTC)),
        }
        try:
            self._table("machine_state").upsert(
                row, on_conflict="strategy_name,instrument"
            ).execute()
        except APIError as exc:
            raise self._wrap(exc, "saving machine state") from exc

    def clear_machine_state(self, strategy_name: str, instrument: str) -> None:
        """Forget a machine that is back at its starting point.

        Keeping a row that says "initial state, no variables" would be
        indistinguishable from one that has never run, while making the table
        grow by one row per symbol per strategy forever.
        """
        try:
            (
                self._table("machine_state")
                .delete()
                .eq("strategy_name", strategy_name)
                .eq("instrument", instrument)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "clearing machine state") from exc

    # -- positions ------------------------------------------------------------

    def list_open_positions(self) -> list[OpenPosition]:
        try:
            resp = self._table("positions").select("*").execute()
        except APIError as exc:
            raise self._wrap(exc, "listing open positions") from exc
        return [OpenPosition.from_row(r) for r in resp.data]

    def open_position(
        self,
        *,
        strategy_name: str,
        instrument: str,
        position_type: str,
        quantity: int,
        entry_signal_candle_ts: datetime,
        entry_fill_ts: datetime,
        intended_entry_price: float,
        entry_price: float,
        stop_loss_price: float,
        target_price: float,
    ) -> OpenPosition | None:
        """Insert a new open position.

        Returns the stored position, or None if one is already open for this
        strategy+instrument (unique constraint) — which means a previous run
        already handled this signal. That is normal, not an error.
        """
        row = {
            "strategy_name": strategy_name,
            "instrument": instrument,
            "position_type": position_type,
            "quantity": quantity,
            "entry_signal_candle_ts": _iso(entry_signal_candle_ts),
            "entry_fill_ts": _iso(entry_fill_ts),
            "intended_entry_price": intended_entry_price,
            "entry_price": entry_price,
            "stop_loss_price": stop_loss_price,
            "target_price": target_price,
        }
        try:
            resp = self._table("positions").insert(row).execute()
        except APIError as exc:
            if self._is_unique_violation(exc):
                return None
            raise self._wrap(exc, f"opening position {strategy_name}/{instrument}") from exc
        return OpenPosition.from_row(resp.data[0])

    def close_position(self, position: OpenPosition, trade: ClosedTrade) -> bool:
        """Record the completed round-trip, then delete the open position.

        Order matters for crash safety: the trade is inserted FIRST. If the
        run dies between the two statements, the next run's attempt to insert
        the same trade hits the unique constraint, is treated as already
        recorded, and proceeds straight to deleting the stale position row.

        Returns True if this call recorded the trade, False if a previous run
        already had (idempotent re-run).
        """
        trade_row = {
            "strategy_name": trade.strategy_name,
            "instrument": trade.instrument,
            "position_type": trade.position_type,
            "quantity": trade.quantity,
            "entry_signal_candle_ts": _iso(trade.entry_signal_candle_ts),
            "entry_fill_ts": _iso(trade.entry_fill_ts),
            "intended_entry_price": trade.intended_entry_price,
            "entry_price": trade.entry_price,
            "exit_signal_candle_ts": _iso(trade.exit_signal_candle_ts),
            "exit_fill_ts": _iso(trade.exit_fill_ts),
            "intended_exit_price": trade.intended_exit_price,
            "exit_price": trade.exit_price,
            "exit_reason": trade.exit_reason,
            "gross_pnl": trade.gross_pnl,
            "costs": trade.costs,
            "net_pnl": trade.net_pnl,
        }
        recorded = True
        try:
            self._table("trades").insert(trade_row).execute()
        except APIError as exc:
            if self._is_unique_violation(exc):
                recorded = False  # a previous run already recorded this trade
            else:
                raise self._wrap(
                    exc, f"recording trade {trade.strategy_name}/{trade.instrument}"
                ) from exc
        try:
            self._table("positions").delete().eq("id", position.id).execute()
        except APIError as exc:
            raise self._wrap(exc, f"deleting closed position {position.id}") from exc
        return recorded

    def completed_cycles_between(
        self, strategy_name: str, instrument: str, start_utc: datetime, end_utc: datetime
    ) -> int:
        """Count completed round-trips whose ENTRY fill falls in [start, end).

        The paper engine uses this (plus any open position) to enforce
        max_cycles_per_day, with the window being the IST trading day
        expressed in UTC.
        """
        try:
            resp = (
                self._table("trades")
                .select("id", count="exact")
                .eq("strategy_name", strategy_name)
                .eq("instrument", instrument)
                .gte("entry_fill_ts", _iso(start_utc))
                .lt("entry_fill_ts", _iso(end_utc))
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "counting today's completed cycles") from exc
        return int(resp.count or 0)

    # -- run audit -------------------------------------------------------------

    def write_run_audit(
        self,
        *,
        run_type: str,
        status: str,
        run_started_at: datetime,
        run_finished_at: datetime | None = None,
        candle_ts: datetime | None = None,
        reason: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Append one audit row. Never raises secrets into logs — callers pass
        only counts/reasons here, never tokens or keys."""
        row: dict[str, Any] = {
            "run_type": run_type,
            "status": status,
            "run_started_at": _iso(run_started_at),
            "run_finished_at": _iso(run_finished_at) if run_finished_at else None,
            "candle_ts": _iso(candle_ts) if candle_ts else None,
            "reason": reason,
            "details": dict(details) if details else None,
        }
        try:
            self._table("run_audit").insert(row).execute()
        except APIError as exc:
            raise self._wrap(exc, "writing the run audit row") from exc

    # -- instrument cache --------------------------------------------------------

    def get_cached_instrument_tokens(self, instruments: Sequence[str]) -> dict[str, int]:
        """Return {'NSE:RELIANCE': 738561, ...} for whichever are cached."""
        if not instruments:
            return {}
        try:
            resp = (
                self._table("instrument_cache")
                .select("instrument,instrument_token")
                .in_("instrument", list(instruments))
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, "reading the instrument cache") from exc
        return {r["instrument"]: int(r["instrument_token"]) for r in resp.data}

    def upsert_instrument_tokens(self, tokens: Mapping[str, int], refreshed_on: date) -> None:
        rows = [
            {"instrument": inst, "instrument_token": tok, "refreshed_on": refreshed_on.isoformat()}
            for inst, tok in tokens.items()
        ]
        if not rows:
            return
        try:
            self._table("instrument_cache").upsert(rows, on_conflict="instrument").execute()
        except APIError as exc:
            raise self._wrap(exc, "updating the instrument cache") from exc

    # -- backtest results -----------------------------------------------------------

    # PostgREST reports an unknown column as PGRST204 and rejects the whole
    # batch. A fifty-symbol run takes minutes, so losing it because one
    # migration has not been applied yet is the expensive failure - the
    # columns that DO exist are worth keeping.
    _MISSING_COLUMN = re.compile(r"Could not find the '([^']+)' column")

    def _insert_dropping_unknown_columns(
        self, table: str, payload: list[dict[str, Any]], what: str
    ) -> int:
        dropped: list[str] = []
        while True:
            try:
                self._table(table).insert(payload).execute()
            except APIError as exc:
                match = self._MISSING_COLUMN.search(str(exc))
                if match and any(match.group(1) in row for row in payload):
                    column = match.group(1)
                    dropped.append(column)
                    for row in payload:
                        row.pop(column, None)
                    continue
                raise self._wrap(exc, what) from exc
            if dropped:
                print(
                    f"  note: {table} is missing {', '.join(dropped)} - stored "
                    "without them. Apply the newest file in sql/ to record them."
                )
            return len(payload)

    def insert_backtest_runs(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert strategy-level run rows (shaped by backtest.py)."""
        payload = [to_native(dict(r)) for r in rows]
        if not payload:
            return 0
        return self._insert_dropping_unknown_columns(
            "backtest_runs", payload, "inserting backtest runs"
        )

    def insert_backtest_equity(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert daily equity points.

        Degrades to 0 when the table is absent, so a database without sql/006
        still completes a backtest - the curve is unavailable, the run is not
        lost. Chunked because a multi-year run produces thousands of rows.
        """
        payload = [to_native(dict(r)) for r in rows]
        if not payload:
            return 0
        try:
            for i in range(0, len(payload), 500):
                self._table("backtest_equity").insert(payload[i:i + 500]).execute()
        except APIError as exc:
            if _is_missing_table(exc):
                print(
                    "  note: no backtest_equity table - the run is stored, the "
                    "equity chart is not. Apply the newest file in sql/."
                )
                return 0
            raise self._wrap(exc, "inserting backtest equity") from exc
        return len(payload)

    def insert_backtest_results(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert backtest result rows (shaped by backtest.py). Returns count."""
        payload = [to_native(dict(r)) for r in rows]
        if not payload:
            return 0
        return self._insert_dropping_unknown_columns(
            "backtest_results", payload, "inserting backtest results"
        )


# ---------------------------------------------------------------------------
# Connectivity self-test CLI
# ---------------------------------------------------------------------------

_ALL_TABLES = (
    "strategies", "daily_token", "positions", "trades",
    "run_audit", "instrument_cache", "backtest_results",
)

# Which sql/ file is applied, told by one thing each of them creates.
#
# Postgres itself is the only honest record: the files in sql/ say what SHOULD
# exist, and a database is only as migrated as someone remembered to paste.
# DDL cannot be run through the PostgREST client, so every migration is a
# manual step - and a manual step needs a way to check it happened.
#
# (file, what it adds, probe table, probe column or None for the table itself)
_MIGRATIONS: tuple[tuple[str, str, str, str | None], ...] = (
    ("001_init.sql", "core tables", "strategies", None),
    ("002_data_foundation.sql", "candle store and instruments", "candles", None),
    ("003_strategy_v2_universes.sql", "universes and drafts", "symbol_groups", None),
    ("004_backtest_runs.sql", "strategy-level verdict", "backtest_runs", None),
    ("005_strategy_versions.sql", "immutable strategy versions", "strategy_versions", None),
    ("006_per_symbol_risk.sql", "per-symbol risk and equity curve",
     "backtest_results", "sharpe_daily"),
    ("007_out_of_sample.sql", "in-sample vs out-of-sample verdict",
     "backtest_runs", "oos_passed_kill_rules"),
    ("008_machine_state.sql", "where a v3 machine is between runs",
     "machine_state", "state"),
    # Widened CHECK constraints rather than a new table, so the probe
    # below cannot detect it by selecting a column. Checked by writing
    # nothing and reading the constraint instead — see _one_minute_allowed.
    ("009_one_minute.sql", "the 1-minute timeframe", None, None),
)


def _one_minute_allowed(store: "SupabaseStore") -> bool:
    """Is the 1m timeframe permitted by the stored CHECK constraints?

    Read-only: PostgREST will not report a CHECK definition, so this asks
    the question the cheapest honest way — whether a 1m coverage row can
    be SELECTED. An un-widened database simply has none and returns an
    empty list, so this reports applied only once 1m data actually exists.
    """
    try:
        resp = (
            store._table("candle_coverage")
            .select("instrument_id").eq("timeframe", "1m").limit(1).execute()
        )
    except APIError:
        return False
    return bool(resp.data)


def _migration_status(store: "SupabaseStore") -> list[tuple[str, str, bool]]:
    """(file, what it adds, applied) for every migration, in order."""
    out = []
    for filename, adds, table, column in _MIGRATIONS:
        if table is None:
            # A constraint-only migration. Attempting a 1m coverage read
            # is harmless and tells us whether the widened CHECK is in
            # place, without writing anything.
            out.append((filename, adds, _one_minute_allowed(store)))
            continue
        try:
            store._table(table).select(column or "*").limit(1).execute()
            applied = True
        except APIError as exc:
            # Anything other than "not there yet" is a real problem, and the
            # caller's own error handling explains it better than a bare False.
            if not _is_missing_table(exc) and "does not exist" not in str(exc).lower():
                raise
            applied = False
        out.append((filename, adds, applied))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that Supabase is reachable and every sql/ migration is applied."
    )
    parser.add_argument(
        "--write", action="store_true",
        help="also insert one run_audit row (run_type=selftest) to prove write access",
    )
    args = parser.parse_args(argv)

    try:
        store = SupabaseStore.from_env()
    except DatabaseError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    failures = 0
    for table in _ALL_TABLES:
        try:
            store._table(table).select("*", count="exact").limit(0).execute()
            print(f"  OK    {table}")
        except APIError as exc:
            failures += 1
            print(f"  FAIL  {table}: {exc}", file=sys.stderr)

    if failures:
        print(
            f"\nFAILED: {failures} table(s) missing or unreadable. "
            "Run sql/001_init.sql in the Supabase SQL editor, then retry.",
            file=sys.stderr,
        )
        return 1

    print("\nMigrations:")
    pending = []
    for filename, adds, applied in _migration_status(store):
        print(f"  {'OK   ' if applied else 'TODO '} sql/{filename}  ({adds})")
        if not applied:
            pending.append((filename, adds))

    if args.write:
        now = datetime.now(tz=UTC)
        store.write_run_audit(
            run_type="selftest", status="ok",
            run_started_at=now, run_finished_at=now,
            reason="db.py --write self-test",
            details={"note": str(uuid.uuid4())},
        )
        print("  OK    wrote one run_audit row (run_type=selftest)")

    if pending:
        print(
            "\n"
            + "\n".join(
                f"NOT APPLIED: sql/{f} - {adds} is unavailable until it is."
                for f, adds in pending
            )
            + "\n\nOpen the Supabase SQL editor, paste the file's whole "
            "contents, click Run. Everything else above is working; the "
            "pipeline degrades around a missing migration rather than failing."
        )
        return 1

    print("\nAll checks passed. Supabase is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
