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


@dataclass(frozen=True)
class SaveResult:
    """Outcome of saving a strategy: valid and runnable, or a stored draft."""

    name: str
    status: str                     # 'valid' | 'draft'
    strategy: Strategy | None       # populated only when valid
    errors: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return self.status == "valid"


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
                strategies.append(parse_strategy_dict(doc, where=f"strategy {doc.get('name')!r}"))
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
        strategy: Strategy | None = None
        try:
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
                "format_version": CURRENT_VERSION,
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

        return SaveResult(
            name=name, status=row["status"], strategy=strategy,
            errors=tuple(errors),
        )

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

    def insert_backtest_runs(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert strategy-level run rows (shaped by backtest.py)."""
        payload = [to_native(dict(r)) for r in rows]
        if not payload:
            return 0
        try:
            self._table("backtest_runs").insert(payload).execute()
        except APIError as exc:
            raise self._wrap(exc, "inserting backtest runs") from exc
        return len(payload)

    def insert_backtest_results(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert backtest result rows (shaped by backtest.py). Returns count."""
        payload = [to_native(dict(r)) for r in rows]
        if not payload:
            return 0
        try:
            self._table("backtest_results").insert(payload).execute()
        except APIError as exc:
            raise self._wrap(exc, "inserting backtest results") from exc
        return len(payload)


# ---------------------------------------------------------------------------
# Connectivity self-test CLI
# ---------------------------------------------------------------------------

_ALL_TABLES = (
    "strategies", "daily_token", "positions", "trades",
    "run_audit", "instrument_cache", "backtest_results",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that Supabase is reachable and sql/001_init.sql was applied."
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

    if args.write:
        now = datetime.now(tz=UTC)
        store.write_run_audit(
            run_type="selftest", status="ok",
            run_started_at=now, run_finished_at=now,
            reason="db.py --write self-test",
            details={"note": str(uuid.uuid4())},
        )
        print("  OK    wrote one run_audit row (run_type=selftest)")

    print("\nAll checks passed. Supabase is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
