"""Draft storage: invalid strategies are kept, but can never run.

A strategy pasted from an external AI tool is EXPECTED to be wrong on the
first attempt, so the correction loop is the common path rather than the rare
one. Rejecting the paste outright would mean retyping or re-prompting from
scratch. These tests pin the two halves of that bargain: the draft is stored
with everything needed to fix it, and no engine can ever load it.

Pure: a hand-rolled fake stands in for the Supabase client, so no network and
no credentials.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from postgrest.exceptions import APIError  # noqa: E402

from db import DatabaseError, SupabaseStore  # noqa: E402


# ---------------------------------------------------------------------------
# A fake Supabase client: just enough of the fluent API that db.py uses.
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data, count=None):
        self.data = data
        self.count = count


class _Query:
    def __init__(self, table: "_Table"):
        self._table = table
        self._filters: list[tuple[str, object]] = []
        self._order: tuple[str, bool] | None = None
        self._pending: list[dict] | None = None   # rows an insert/upsert made

    def select(self, *_args, **_kwargs):
        return self

    def order(self, column, desc=False, **_kwargs):
        self._order = (column, desc)
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def range(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def in_(self, column, values):
        self._filters.append((column, list(values)))
        return self

    def insert(self, rows):
        payload = rows if isinstance(rows, list) else [rows]
        made = []
        for r in payload:
            row = dict(r)
            # Real Postgres assigns bigserial ids; code reads them back.
            row.setdefault("id", self._table.next_id())
            self._table.rows.append(row)
            self._table.inserts.append(row)
            made.append(row)
        self._pending = made
        return self

    def update(self, changes):
        self._pending = []
        for row in self._table.rows:
            if all(row.get(c) == v for c, v in self._filters):
                row.update(changes)
                self._pending.append(row)
        return self

    def upsert(self, row, on_conflict=None):
        rows = row if isinstance(row, list) else [row]
        made = []
        for r in rows:
            self._table.rows = [
                x for x in self._table.rows if x.get("name") != r.get("name")
            ]
            new = dict(r)
            new.setdefault("id", self._table.next_id())
            self._table.rows.append(new)
            self._table.upserts.append(new)
            made.append(new)
        self._pending = made
        return self

    def execute(self):
        if self._pending is not None:
            return _Result(self._pending, len(self._pending))
        rows = self._table.rows
        for column, value in self._filters:
            if isinstance(value, list):
                rows = [r for r in rows if r.get(column) in value]
            else:
                rows = [r for r in rows if r.get(column) == value]
        if self._order:
            column, desc = self._order
            rows = sorted(rows, key=lambda r: (r.get(column) is None, r.get(column)),
                          reverse=desc)
        else:
            rows = sorted(rows, key=lambda r: r.get("name") or "")
        return _Result(rows, len(rows))


class _Table:
    def __init__(self):
        self.rows: list[dict] = []
        self.upserts: list[dict] = []
        self.inserts: list[dict] = []
        self._next = 0

    def next_id(self) -> int:
        self._next += 1
        return self._next


class FakeClient:
    def __init__(self):
        self.tables: dict[str, _Table] = {}

    def table(self, name):
        return _Query(self.tables.setdefault(name, _Table()))


@pytest.fixture
def store():
    return SupabaseStore(FakeClient())


def last_upsert(store: SupabaseStore, table: str = "strategies") -> dict:
    return store._client.tables[table].upserts[-1]


def set_universes(store: SupabaseStore, names: list[str]) -> None:
    tbl = store._client.tables.setdefault("symbol_groups", _Table())
    tbl.rows = [{"name": n} for n in names]


def seed_rows(store: SupabaseStore, rows: list[dict]) -> None:
    store._client.tables.setdefault("strategies", _Table()).rows = rows


def valid_doc(**overrides) -> dict:
    doc = {
        "name": "ok-strategy",
        "enabled": True,
        "position_type": "long",
        "timeframe": "15m",
        "instruments": ["NSE:RELIANCE"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        },
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 2,
    }
    doc.update(overrides)
    return doc


# ---------------------------------------------------------------------------
# Saving
# ---------------------------------------------------------------------------


def test_a_valid_document_is_saved_with_typed_columns(store):
    result = store.save_strategy_document(valid_doc())
    assert result.is_valid and result.status == "valid"
    row = last_upsert(store)
    assert row["status"] == "valid"
    assert row["timeframe"] == "15m"
    assert row["validation_errors"] is None
    assert row["format_version"] == 2


def test_an_invalid_document_is_saved_as_a_draft_not_rejected(store):
    result = store.save_strategy_document(valid_doc(timeframe="7m"))
    assert result.status == "draft"
    row = last_upsert(store)
    assert row["status"] == "draft"
    # Typed columns stay null: a draft may contain anything, and the DB CHECK
    # constraints would reject exactly the rows a draft needs to store.
    assert row["timeframe"] is None
    assert row["enabled"] is None
    assert row["definition"] is None
    assert "7m" in str(row["validation_errors"])


def test_a_draft_keeps_the_text_it_was_pasted_from(store):
    text = (
        "name: broken-one\n"
        "enabled: true\n"
        "position_type: long\n"
        "timeframe: 7m\n"
    )
    result = store.save_strategy_text(text)
    assert result.status == "draft"
    assert last_upsert(store)["raw_source"] == text


def test_a_full_document_with_a_version_header_is_accepted(store):
    text = (
        "version: 2\n"
        "strategies:\n"
        "  - name: from-chatgpt\n"
        "    enabled: true\n"
        "    position_type: long\n"
        "    timeframe: 15m\n"
        "    instruments: [NSE:RELIANCE]\n"
        "    entry: {all: [{indicator: close, operator: \">\", value: 1}]}\n"
        "    exit: {any: [{indicator: close, operator: \"<\", value: 1}]}\n"
        "    risk:\n"
        "      stop_loss: {type: percent, value: 1.0}\n"
        "      target: {type: percent, value: 2.0}\n"
        "    sizing: {type: notional, notional_per_trade: 100000}\n"
    )
    result = store.save_strategy_text(text)
    assert result.is_valid, result.errors
    assert result.name == "from-chatgpt"


def test_a_v1_document_pasted_in_is_migrated_on_the_way_through(store):
    text = (
        "version: 1\n"
        "strategies:\n"
        "  - name: old-one\n"
        "    enabled: true\n"
        "    position_type: long\n"
        "    timeframe: 15m\n"
        "    instruments: [NSE:RELIANCE]\n"
        "    entry: {all: [{indicator: close, operator: \">\", value: 1}]}\n"
        "    exit: {any: [{indicator: close, operator: \"<\", value: 1}]}\n"
        "    risk: {stop_loss_pct: 0.7, target_pct: 1.5}\n"
        "    sizing: {type: fixed_quantity, quantity: 1}\n"
    )
    result = store.save_strategy_text(text)
    assert result.is_valid, result.errors
    assert result.strategy.risk.stop_loss.value == 0.7


def test_unparseable_yaml_names_what_to_fix(store):
    with pytest.raises(DatabaseError) as exc:
        store.save_strategy_text("name: x\n  bad: indent\n")
    assert "not valid YAML" in str(exc.value)


def test_a_draft_must_still_have_a_usable_name(store):
    # Without a name there is no primary key, so there is nowhere to put it.
    with pytest.raises(DatabaseError) as exc:
        store.save_strategy_text("enabled: true\ntimeframe: 7m\n")
    assert "name" in str(exc.value).lower()


def test_pasting_several_strategies_at_once_is_refused_clearly(store):
    text = (
        "version: 2\n"
        "strategies:\n"
        "  - name: one\n"
        "  - name: two\n"
    )
    with pytest.raises(DatabaseError) as exc:
        store.save_strategy_text(text)
    assert "one at a time" in str(exc.value)


# ---------------------------------------------------------------------------
# Universe existence — checked at save, because the parser must stay offline
# ---------------------------------------------------------------------------


def test_an_unknown_universe_makes_it_a_draft(store):
    set_universes(store, ["NIFTY50"])
    doc = valid_doc(universe="NIFTY_NOPE")
    del doc["instruments"]
    result = store.save_strategy_document(doc)
    assert result.status == "draft"
    joined = " ".join(result.errors)
    assert "NIFTY_NOPE" in joined
    assert "NIFTY50" in joined          # says what IS available


def test_a_known_universe_saves_as_valid(store):
    set_universes(store, ["NIFTY50"])
    doc = valid_doc(universe="NIFTY50")
    del doc["instruments"]
    assert store.save_strategy_document(doc).is_valid


def test_the_error_is_helpful_when_no_universes_exist_at_all(store):
    set_universes(store, [])
    doc = valid_doc(universe="NIFTY50")
    del doc["instruments"]
    result = store.save_strategy_document(doc)
    joined = " ".join(result.errors)
    assert "none defined yet" in joined
    assert "refresh_universes" in joined


# ---------------------------------------------------------------------------
# Drafts can never run — the single filter that enforces it
# ---------------------------------------------------------------------------


def test_list_strategies_excludes_drafts(store):
    seed_rows(store, [
        {"name": "good", "status": "valid", "enabled": True,
         "definition": valid_doc(name="good")},
        {"name": "bad", "status": "draft", "enabled": None, "definition": None,
         "validation_errors": ["timeframe: unsupported"]},
    ])
    assert [s.name for s in store.list_strategies()] == ["good"]


def test_list_strategy_documents_still_shows_drafts_for_the_editor(store):
    seed_rows(store, [
        {"name": "good", "status": "valid", "enabled": True,
         "definition": valid_doc(name="good")},
        {"name": "bad", "status": "draft", "enabled": None, "definition": None,
         "validation_errors": ["timeframe: unsupported"]},
    ])
    docs = store.list_strategy_documents()
    assert sorted(d["name"] for d in docs) == ["bad", "good"]
    draft = next(d for d in docs if d["name"] == "bad")
    assert draft["status"] == "draft"
    assert draft["validation_errors"] == ["timeframe: unsupported"]


def test_a_row_without_a_status_is_treated_as_valid(store):
    """Rows predating migration 003 have no status column value."""
    seed_rows(store, [
        {"name": "legacy", "enabled": True, "definition": valid_doc(name="legacy")},
    ])
    assert [s.name for s in store.list_strategies()] == ["legacy"]


def test_sync_strategies_is_gone(store):
    """It disabled every strategy absent from strategies.yaml.

    With the database owning strategy definitions, that would silently disable
    everything authored in the app on the next engine run.
    """
    assert not hasattr(store, "sync_strategies")


def test_seeding_does_not_touch_existing_strategies(store):
    seed_rows(store, [
        {"name": "mine", "status": "valid", "enabled": True,
         "definition": valid_doc(name="mine")},
    ])
    assert store.seed_strategies_if_empty([valid_doc()]) == 0
    assert [s.name for s in store.list_strategies()] == ["mine"]


# ---------------------------------------------------------------------------
# numpy at the database boundary.
#
# The simulator reads prices out of numpy arrays, so a trade's net_pnl is a
# numpy.float64 and every comparison built from it — `net_pnl > 0` in the
# kill-rule flags — is a numpy.bool_. Neither survives JSON encoding, and a
# 50-symbol backtest died at the insert having already computed everything,
# losing all of it.
# ---------------------------------------------------------------------------


def test_numpy_scalars_are_converted_before_insert(store):
    import json

    import numpy as np

    from db import to_native

    row = {
        "batch_id": "b", "strategy_name": "s",
        "net_pnl": np.float64(-440446.12),
        "total_trades": np.int64(3298),
        "passed_kill_rules": np.bool_(False),
        "kill_rule_flags": {"net_positive_after_costs": {"passed": np.bool_(False)}},
        "symbols": ["NSE:TCS"],
        "constituents_as_of": None,
    }
    native = to_native(row)
    json.dumps(native)          # the operation that used to raise

    assert type(native["passed_kill_rules"]) is bool
    assert type(native["net_pnl"]) is float
    assert type(native["total_trades"]) is int
    assert type(native["kill_rule_flags"]["net_positive_after_costs"]["passed"]) is bool
    # Plain values must pass through untouched.
    assert native["symbols"] == ["NSE:TCS"]
    assert native["constituents_as_of"] is None


def test_backtest_inserts_survive_numpy_values(store):
    """The end-to-end guard: the insert path itself, not just the helper."""
    import numpy as np

    assert store.insert_backtest_results([
        {"strategy_name": "s", "net_pnl": np.float64(1.5),
         "passed_kill_rules": np.bool_(True)},
    ]) == 1
    assert store.insert_backtest_runs([
        {"strategy_name": "s", "sharpe_daily": np.float64(-5.27),
         "passed_kill_rules": np.bool_(False)},
    ]) == 1


# ---------------------------------------------------------------------------
# Strategy versioning.
#
# The problem this solves: strategies were keyed by name and saving overwrote
# in place, so a stored backtest result pointed at a definition that could be
# silently rewritten afterwards. That made every result irreproducible, every
# comparison unsound, and deployment a matter of deploying a NAME rather than
# a set of rules.
# ---------------------------------------------------------------------------


def test_saving_a_valid_strategy_creates_version_1(store):
    result = store.save_strategy_document(valid_doc())
    assert result.version == 1
    assert result.created_new_version is True
    assert result.version_id is not None


def test_saving_identical_content_does_not_mint_a_new_version(store):
    """Re-saving unchanged rules must not fill the history with noise, or
    'which version did I test?' stops having a useful answer."""
    first = store.save_strategy_document(valid_doc())
    again = store.save_strategy_document(valid_doc())
    assert again.version == first.version == 1
    assert again.created_new_version is False
    assert len(store.strategy_versions("ok-strategy")) == 1


def test_key_order_does_not_count_as_a_change(store):
    """The hash is over canonical JSON, so formatting cannot fake a change."""
    doc = valid_doc()
    store.save_strategy_document(doc)
    reordered = {k: doc[k] for k in reversed(list(doc))}
    again = store.save_strategy_document(reordered)
    assert again.created_new_version is False
    assert again.version == 1


def test_changing_the_rules_creates_version_2(store):
    store.save_strategy_document(valid_doc())
    changed = store.save_strategy_document(
        valid_doc(risk={"stop_loss": {"type": "percent", "value": 2.5},
                        "target": {"type": "percent", "value": 5.0}})
    )
    assert changed.version == 2
    assert changed.created_new_version is True
    assert len(store.strategy_versions("ok-strategy")) == 2


def test_an_older_version_is_never_altered(store):
    """Immutability is the whole point: a result referencing v1 must still be
    able to recover exactly what v1 said, forever."""
    store.save_strategy_document(valid_doc())
    v1 = [v for v in store.strategy_versions("ok-strategy") if v["version"] == 1][0]
    original_stop = v1["definition"]["risk"]["stop_loss"]["value"]

    store.save_strategy_document(
        valid_doc(risk={"stop_loss": {"type": "percent", "value": 9.9},
                        "target": {"type": "percent", "value": 12.0}})
    )
    v1_again = [v for v in store.strategy_versions("ok-strategy") if v["version"] == 1][0]
    assert v1_again["definition"]["risk"]["stop_loss"]["value"] == original_stop == 1.0


def test_reverting_to_earlier_rules_reuses_that_version(store):
    """Content-addressed, so going back to v1's rules is v1, not v3."""
    store.save_strategy_document(valid_doc())                      # v1
    store.save_strategy_document(valid_doc(max_cycles_per_day=9))  # v2
    back = store.save_strategy_document(valid_doc())               # == v1
    assert back.version == 1
    assert back.created_new_version is False
    assert len(store.strategy_versions("ok-strategy")) == 2


def test_a_draft_is_not_versioned(store):
    """A draft has no coherent definition to snapshot; versioning one would
    create history nothing could reproduce."""
    result = store.save_strategy_document(valid_doc(timeframe="7m"))
    assert result.status == "draft"
    assert result.version is None
    assert result.version_id is None
    assert store.strategy_versions("ok-strategy") == []


def test_the_current_version_pointer_follows_the_latest_save(store):
    store.save_strategy_document(valid_doc())
    v2 = store.save_strategy_document(valid_doc(max_cycles_per_day=7))
    assert store.current_version_id("ok-strategy") == v2.version_id


def test_versioning_degrades_gracefully_without_the_migration(store):
    """A database without sql/005 must keep saving strategies. The feature is
    unavailable; the app is not."""
    class NoVersionTable(FakeClient):
        def table(self, name):
            if name == "strategy_versions":
                raise APIError({"message": 'relation "strategy_versions" does not exist'})
            return super().table(name)

    from db import SupabaseStore
    degraded = SupabaseStore(NoVersionTable())
    result = degraded.save_strategy_document(valid_doc())
    assert result.is_valid          # the save still worked
    assert result.version is None   # but nothing was versioned
