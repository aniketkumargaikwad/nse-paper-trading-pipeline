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

    def select(self, *_args, **_kwargs):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def eq(self, column, value):
        self._filters.append((column, value))
        return self

    def upsert(self, row, on_conflict=None):
        rows = row if isinstance(row, list) else [row]
        for r in rows:
            self._table.rows = [
                x for x in self._table.rows if x.get("name") != r.get("name")
            ]
            self._table.rows.append(dict(r))
            self._table.upserts.append(dict(r))
        return self

    def execute(self):
        rows = self._table.rows
        for column, value in self._filters:
            rows = [r for r in rows if r.get(column) == value]
        return _Result(sorted(rows, key=lambda r: r.get("name") or ""), len(rows))


class _Table:
    def __init__(self):
        self.rows: list[dict] = []
        self.upserts: list[dict] = []


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
