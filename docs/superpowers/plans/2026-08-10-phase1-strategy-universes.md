# Phase 1 — Strategy Format v2 and Symbol Universes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a strategy point at a named universe (`universe: NIFTY100`) instead of a hand-typed symbol list, express trade size in rupees so cost drag is comparable across symbols, and live in the database so it can be pasted in or built from a form.

**Architecture:** `strategy_schema.py` splits into a focused `strategy/` package (`vocabulary` ← `parse` ← `migrate`) and keeps working as a re-export shim so nothing downstream changes. A new top-level `universes.py` owns NSE constituent lists — fetch, dated snapshot fallback, and resolution against the `instruments` table — and is imported by callers at run time, never by the parser, which stays pure and offline. Supabase becomes the owner of a strategy definition; invalid ones are stored as drafts that cannot run.

**Tech Stack:** Python 3.11+, pandas, PyYAML, `requests`, Supabase (`supabase-py`), pytest, Streamlit.

---

## Ground rules for every task

**Run tests with the project venv on Windows:**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

**The suite must be green at every commit.** This plan is sequenced so it always is. Where a v2 change would break an existing caller, the same task either adds a back-compatible accessor or updates the caller — never leaves the repo red between commits.

**Read before writing.** Several tasks move existing code verbatim. Open the file and move the real lines; do not retype them from memory.

---

## File structure

**Create:**

| Path | Responsibility |
|---|---|
| `strategy/__init__.py` | Public API re-exports; the only import surface other modules need |
| `strategy/vocabulary.py` | What is legal: indicators, operators, sizing/stop types. Pure data, no logic |
| `strategy/parse.py` | dict → validated `Strategy`; owns every error message |
| `strategy/migrate.py` | v1 document → v2 document |
| `universes.py` | NSE constituent fetch, snapshot load, resolution, storage projection |
| `scripts/refresh_universes.py` | CLI to refresh universes and write snapshots |
| `scripts/gen_strategy_format_doc.py` | Generates `docs/STRATEGY_FORMAT.md` from `vocabulary.py` |
| `sql/003_strategy_v2_universes.sql` | Additive migration: draft columns, widened timeframe check, universe provenance |
| `docs/STRATEGY_FORMAT.md` | The page you paste into ChatGPT before asking for a strategy |
| `data/universes/` | Committed dated constituent snapshots |
| `tests/test_strategy_parse.py` | v2 field validation, asserting on message content |
| `tests/test_strategy_migrate.py` | v1 → v2 migration |
| `tests/test_universes.py` | Snapshot parsing, fallback, partial resolution, projection |
| `tests/test_strategy_format_doc.py` | The generated doc matches the validator |

**Modify:**

| Path | Change |
|---|---|
| `strategy_schema.py` | Becomes a thin re-export shim; keeps its CLI |
| `db.py:228-348` | Draft support; `sync_strategies` removed |
| `backtest.py:117-250` | Notional quantity, ATR stops, trailing stop, session rules |
| `strategies.yaml` | Migrated to `version: 2` |
| `app_pages/strategies.py` | Paste box alongside the existing form |
| `sql/001_init.sql` | Left untouched — 003 carries all changes |

---

## Task 1: Create the `strategy/` package with vocabulary extracted

Pure refactor. No behaviour change, no new validation. The suite proves it.

**Files:**
- Create: `strategy/__init__.py`, `strategy/vocabulary.py`
- Modify: `strategy_schema.py:1-85` (remove the moved constants, import them back)
- Test: existing `tests/test_strategy_schema.py` (must stay green untouched)

- [ ] **Step 1: Run the suite and record the baseline count**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: all pass. Write the number down — every later task compares against it.

- [ ] **Step 2: Create `strategy/vocabulary.py`**

Move these definitions **verbatim** out of `strategy_schema.py:38-85`: `PRICE_SOURCES`, `INDICATOR_PARAMS`, `INDICATOR_OUTPUTS`, `DEFAULT_OUTPUT`, `SOURCE_ALLOWED_FOR`, `COMPARISON_OPERATORS`, `CROSS_OPERATORS`, `ALL_OPERATORS`, `POSITION_TYPES`, `SIZING_TYPES`, `INSTRUMENT_RE`.

Header for the new file:

```python
"""The vocabulary a strategy document may use — the single source of truth.

Pure data, no logic and no imports beyond the standard library, so it can be
read by the parser, by the migration, and by the format-doc generator without
any risk of a circular import.

Anything added here becomes legal in strategies.yaml AND appears in
docs/STRATEGY_FORMAT.md automatically, because that document is generated from
this module. A test asserts the two agree — see tests/test_strategy_format_doc.py.
"""

from __future__ import annotations

import re
```

Then the moved constants, unchanged.

- [ ] **Step 3: Create `strategy/__init__.py`**

```python
"""Strategy authoring: vocabulary, validation, and version migration.

Import from here rather than from the submodules::

    from strategy import Strategy, StrategyConfigError, parse_strategy_dict
"""

from __future__ import annotations

from strategy.vocabulary import (
    ALL_OPERATORS,
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)

__all__ = [
    "ALL_OPERATORS",
    "COMPARISON_OPERATORS",
    "CROSS_OPERATORS",
    "DEFAULT_OUTPUT",
    "INDICATOR_OUTPUTS",
    "INDICATOR_PARAMS",
    "INSTRUMENT_RE",
    "POSITION_TYPES",
    "PRICE_SOURCES",
    "SIZING_TYPES",
    "SOURCE_ALLOWED_FOR",
]
```

- [ ] **Step 4: Point `strategy_schema.py` at the new module**

Delete the moved constant block from `strategy_schema.py` and replace it with:

```python
from strategy.vocabulary import (
    ALL_OPERATORS,
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)
```

Keep the `from config import SUPPORTED_TIMEFRAMES` import where it is.

- [ ] **Step 5: Run the suite — the count must be identical to Step 1**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS, same number of tests. A pure move changes nothing.

- [ ] **Step 6: Verify the CLI still works**

```bash
.\.venv\Scripts\python.exe strategy_schema.py
```

Expected: `OK: strategies.yaml is valid. 1 strategy defined:` followed by the summary line.

- [ ] **Step 7: Commit**

```bash
git add strategy/ strategy_schema.py
git commit -m "refactor(strategy): extract the document vocabulary into its own module

Pure move ahead of format v2. strategy_schema.py is already 22KB and v2 adds
universes, notional sizing, ATR and trailing stops, session rules and a
migration on top of it. Splitting vocabulary out first keeps each later change
reviewable.

No behaviour change: same tests, same count, same CLI output.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Move the parser into `strategy/parse.py`

Still a pure refactor. `strategy_schema.py` becomes the shim described in the spec.

**Files:**
- Create: `strategy/parse.py`
- Modify: `strategy_schema.py` (reduced to a shim), `strategy/__init__.py`
- Test: existing `tests/test_strategy_schema.py` (green, untouched)

- [ ] **Step 1: Create `strategy/parse.py`**

Move **verbatim** from `strategy_schema.py`: `StrategyConfigError`, the dataclasses (`Operand`, `Condition`, `ConditionGroup`, `RiskConfig`, `SizingConfig`, `Strategy`), every `_`-prefixed helper (`_fail` through `_parse_strategy`), and the public functions `parse_strategies`, `parse_strategy_dict`, `strategy_to_raw`, `load_strategy_documents`, `load_strategies`.

Leave `main()` and the `if __name__` block behind in `strategy_schema.py`.

File header:

```python
"""Validation: a raw strategy document becomes a checked Strategy object.

Deliberately strict and deliberately hand-rolled. Every failure names the exact
location (e.g. ``strategies[0].entry.all[1].operator``) and says how to fix it.

Those messages are not a nicety — strategies now arrive pasted in from external
AI tools and are EXPECTED to be wrong on the first attempt, so the correction
loop is the common path rather than the rare one. That is also why this module
is not built on pydantic or jsonschema: their errors are cryptic to a reader who
is not a Python developer.

PURE MODULE: no network, no database, no clock. Validating a pasted strategy
must work offline. In particular, a `universe:` name is validated for SHAPE
here; whether that universe actually exists is checked at save time by the
caller, which has database access (see db.SupabaseStore.save_strategy_document).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

import yaml

from config import SUPPORTED_TIMEFRAMES
from strategy.vocabulary import (
    ALL_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)
```

- [ ] **Step 2: Reduce `strategy_schema.py` to a shim**

Replace the whole file with:

```python
"""Backwards-compatible entry point for strategy validation.

The implementation moved to the `strategy/` package. This module re-exports the
public API so existing imports (backtest.py, paper_engine.py, db.py, app_pages/)
and the CLI keep working unchanged::

    .venv\\Scripts\\python.exe strategy_schema.py [path/to/strategies.yaml]

New code should import from `strategy` directly.
"""

from __future__ import annotations

import sys

from strategy.parse import (
    Condition,
    ConditionGroup,
    Operand,
    RiskConfig,
    SizingConfig,
    Strategy,
    StrategyConfigError,
    load_strategies,
    load_strategy_documents,
    parse_strategies,
    parse_strategy_dict,
    strategy_to_raw,
)
from strategy.vocabulary import (
    ALL_OPERATORS,
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)

__all__ = [
    "ALL_OPERATORS", "COMPARISON_OPERATORS", "CROSS_OPERATORS",
    "Condition", "ConditionGroup", "DEFAULT_OUTPUT", "INDICATOR_OUTPUTS",
    "INDICATOR_PARAMS", "INSTRUMENT_RE", "Operand", "POSITION_TYPES",
    "PRICE_SOURCES", "RiskConfig", "SIZING_TYPES", "SOURCE_ALLOWED_FOR",
    "SizingConfig", "Strategy", "StrategyConfigError", "load_strategies",
    "load_strategy_documents", "parse_strategies", "parse_strategy_dict",
    "strategy_to_raw",
]


def main(argv: list[str]) -> int:
    """CLI entry point: validate a strategies file and print a summary."""
    path = argv[1] if len(argv) > 1 else "strategies.yaml"
    try:
        strategies = load_strategies(path)
    except (StrategyConfigError, FileNotFoundError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {path} is valid. {len(strategies)} strateg{'y' if len(strategies) == 1 else 'ies'} defined:")
    for s in strategies:
        state = "enabled" if s.enabled else "DISABLED"
        print(
            f"  - {s.name} [{state}] {s.position_type} {s.timeframe} "
            f"on {len(s.instruments)} instrument(s), "
            f"SL {s.risk.stop_loss_pct}% / target {s.risk.target_pct}%, "
            f"max {s.max_cycles_per_day} cycle(s)/day"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 3: Extend `strategy/__init__.py` re-exports**

Add to the imports and `__all__` in `strategy/__init__.py`:

```python
from strategy.parse import (
    Condition,
    ConditionGroup,
    Operand,
    RiskConfig,
    SizingConfig,
    Strategy,
    StrategyConfigError,
    load_strategies,
    load_strategy_documents,
    parse_strategies,
    parse_strategy_dict,
    strategy_to_raw,
)
```

- [ ] **Step 4: Run the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS, identical count to Task 1.

- [ ] **Step 5: Verify the CLI and every downstream import**

```bash
.\.venv\Scripts\python.exe strategy_schema.py
.\.venv\Scripts\python.exe -c "import backtest, paper_engine, db, seed_demo; print('imports ok')"
```

Expected: the CLI summary, then `imports ok`.

- [ ] **Step 6: Commit**

```bash
git add strategy/ strategy_schema.py
git commit -m "refactor(strategy): move validation into strategy/parse.py

strategy_schema.py becomes a re-export shim so backtest.py, paper_engine.py,
db.py and app_pages/ are untouched, and the CLI keeps working.

No behaviour change.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: v2 risk block — percent and ATR stops, optional trailing

The first real v2 change. `RiskConfig` gains structure while keeping
`stop_loss_pct` / `target_pct` as read-only properties, so `backtest.py:192-196`
and the CLI keep working untouched. Those properties are removed in Task 17
once the engine reads the specs directly.

**Files:**
- Modify: `strategy/vocabulary.py`, `strategy/parse.py`
- Test: `tests/test_strategy_parse.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_parse.py`:

```python
"""Tests for format v2 validation. Pure — no network, no database.

These assert on MESSAGE CONTENT, not merely that an error was raised. The
messages are the interface for correcting AI-generated strategies, so a message
that stops naming the offending key is a real regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.parse import StrategyConfigError, parse_strategy_dict  # noqa: E402


def valid_v2() -> dict:
    """Smallest valid v2 strategy; each test breaks exactly one thing."""
    return {
        "name": "t",
        "enabled": True,
        "position_type": "long",
        "timeframe": "15m",
        "instruments": ["NSE:RELIANCE"],
        "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                           "operator": ">", "value": 50}]},
        "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                          "operator": "<", "value": 40}]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 0.7},
            "target": {"type": "percent", "value": 1.5},
        },
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }


def test_percent_stop_parses():
    s = parse_strategy_dict(valid_v2())
    assert s.risk.stop_loss.type == "percent"
    assert s.risk.stop_loss.value == 0.7
    assert s.risk.trailing_stop is None


def test_percent_properties_stay_available_for_the_engine():
    s = parse_strategy_dict(valid_v2())
    assert s.risk.stop_loss_pct == 0.7
    assert s.risk.target_pct == 1.5


def test_atr_stop_parses():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": 14, "multiplier": 1.5}
    s = parse_strategy_dict(doc)
    assert s.risk.stop_loss.type == "atr"
    assert s.risk.stop_loss.period == 14
    assert s.risk.stop_loss.multiplier == 1.5


def test_trailing_stop_is_optional_and_parses():
    doc = valid_v2()
    doc["risk"]["trailing_stop"] = {"type": "percent", "value": 0.5}
    s = parse_strategy_dict(doc)
    assert s.risk.trailing_stop.value == 0.5


def test_atr_stop_rejects_percent_keys():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "value": 1.5}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "risk.stop_loss" in msg
    assert "period" in msg and "multiplier" in msg


def test_percent_stop_rejects_atr_keys():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "percent", "period": 14, "multiplier": 2}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "value" in str(exc.value)


def test_unknown_stop_type_lists_the_allowed_ones():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "trailing", "value": 1}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "trailing" in msg
    assert "percent" in msg and "atr" in msg


def test_percent_out_of_range_is_rejected():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "percent", "value": 70}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "between 0 and 50" in str(exc.value)
```

- [ ] **Step 2: Run them to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: FAIL — the current `_parse_risk` requires `stop_loss_pct`/`target_pct`, so `valid_v2()` is rejected.

- [ ] **Step 3: Add the stop vocabulary**

Append to `strategy/vocabulary.py`:

```python
# Stop/target specifications. 'percent' is a flat move from entry; 'atr' scales
# with the symbol's own volatility, which matters across a universe where one
# fixed percentage is too tight for volatile names and too loose for calm ones.
STOP_TYPES: frozenset[str] = frozenset({"percent", "atr"})

# Required keys per stop type. Keys outside these are rejected, so mixing the
# two forms (e.g. {type: atr, value: 1.5}) fails loudly instead of silently
# ignoring the key that does not apply.
STOP_TYPE_KEYS: dict[str, frozenset[str]] = {
    "percent": frozenset({"value"}),
    "atr": frozenset({"period", "multiplier"}),
}

# An intraday stop wider than this is almost certainly a typo (70 for 0.7).
MAX_STOP_PERCENT = 50.0
```

- [ ] **Step 4: Replace `RiskConfig` and `_parse_risk` in `strategy/parse.py`**

Replace the existing `RiskConfig` dataclass with:

```python
@dataclass(frozen=True)
class StopSpec:
    """A stop or target level: either a flat percent or an ATR multiple.

    Exactly one form is populated. `percent` uses `value`; `atr` uses `period`
    and `multiplier`.
    """

    type: str
    value: float | None = None
    period: int | None = None
    multiplier: float | None = None


@dataclass(frozen=True)
class RiskConfig:
    stop_loss: StopSpec
    target: StopSpec
    trailing_stop: StopSpec | None = None

    # Convenience for percent-only callers (the engine before Task 17 and the
    # CLI summary). Raises rather than guessing when the stop is ATR-based:
    # silently reporting 0.0% for an ATR stop would misdescribe the strategy.
    @property
    def stop_loss_pct(self) -> float:
        if self.stop_loss.type != "percent":
            raise ValueError(
                f"stop_loss is {self.stop_loss.type!r}, not a percent — "
                "read risk.stop_loss directly"
            )
        return float(self.stop_loss.value)

    @property
    def target_pct(self) -> float:
        if self.target.type != "percent":
            raise ValueError(
                f"target is {self.target.type!r}, not a percent — "
                "read risk.target directly"
            )
        return float(self.target.value)
```

Replace `_parse_risk` with:

```python
def _parse_stop_spec(node: Any, where: str) -> StopSpec:
    node = _require_mapping(node, where)
    if "type" not in node:
        _fail(where, f"missing required key: type. Allowed: {', '.join(sorted(STOP_TYPES))}")

    stype = node["type"]
    if stype not in STOP_TYPES:
        _fail(
            f"{where}.type",
            f"unknown stop type {stype!r}. Allowed: {', '.join(sorted(STOP_TYPES))}",
        )

    # Reject the other form's keys explicitly — {type: atr, value: 1.5} is a
    # very natural mistake and must not silently use a default multiplier.
    _require_keys(node, where, required={"type"} | set(STOP_TYPE_KEYS[stype]), optional=set())

    if stype == "percent":
        raw = node["value"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(f"{where}.value", f"expected a number, got {raw!r}")
        if not 0 < raw <= MAX_STOP_PERCENT:
            _fail(
                f"{where}.value",
                f"must be between 0 and {MAX_STOP_PERCENT:g} (percent), got {raw}",
            )
        return StopSpec(type="percent", value=float(raw))

    period = node["period"]
    if isinstance(period, bool) or not isinstance(period, int) or period < 1:
        _fail(f"{where}.period", f"expected a whole number >= 1, got {period!r}")
    mult = node["multiplier"]
    if isinstance(mult, bool) or not isinstance(mult, (int, float)) or mult <= 0:
        _fail(f"{where}.multiplier", f"expected a number > 0, got {mult!r}")
    return StopSpec(type="atr", period=period, multiplier=float(mult))


def _parse_risk(node: Any, where: str) -> RiskConfig:
    node = _require_mapping(node, where)
    _require_keys(
        node, where,
        required={"stop_loss", "target"},
        optional={"trailing_stop"},
    )
    trailing = (
        _parse_stop_spec(node["trailing_stop"], f"{where}.trailing_stop")
        if "trailing_stop" in node
        else None
    )
    return RiskConfig(
        stop_loss=_parse_stop_spec(node["stop_loss"], f"{where}.stop_loss"),
        target=_parse_stop_spec(node["target"], f"{where}.target"),
        trailing_stop=trailing,
    )
```

Add to the `strategy/vocabulary` import block in `strategy/parse.py`: `MAX_STOP_PERCENT`, `STOP_TYPES`, `STOP_TYPE_KEYS`.

- [ ] **Step 5: Run the new tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: PASS.

- [ ] **Step 6: Run the full suite — old v1 tests now fail, and that is expected**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: failures in `tests/test_strategy_schema.py` and any fixture using `stop_loss_pct:`. Do **not** fix them by loosening the parser. Task 7 adds the v1→v2 migration; the fixtures are updated in Step 7 below to the v2 shape.

- [ ] **Step 7: Update the v1 fixtures in `tests/test_strategy_schema.py`**

In `valid_doc()` and every test that builds a `risk:` block, replace:

```python
"risk": {"stop_loss_pct": 0.7, "target_pct": 1.5},
```

with:

```python
"risk": {
    "stop_loss": {"type": "percent", "value": 0.7},
    "target": {"type": "percent", "value": 1.5},
},
```

Any test asserting on the old error text (`"stop_loss_pct"`) is asserting v1 behaviour that Task 7 covers properly — retarget it at `risk.stop_loss` instead of deleting it.

- [ ] **Step 8: Update `strategies.yaml` risk block so the CLI stays green**

```yaml
    risk:
      stop_loss: { type: percent, value: 0.7 }
      target:    { type: percent, value: 1.5 }
```

- [ ] **Step 9: Run the full suite and the CLI**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe strategy_schema.py
```

Expected: all PASS; CLI prints the summary with `SL 0.7% / target 1.5%`.

- [ ] **Step 10: Commit**

```bash
git add strategy/ tests/ strategies.yaml
git commit -m "feat(strategy): structured stops — percent or ATR, plus optional trailing

A flat percentage stop is the wrong instrument across a universe: it is too
tight for volatile names and too loose for calm ones. ATR stops scale with each
symbol's own volatility.

Mixing the two forms ({type: atr, value: 1.5}) is rejected explicitly rather
than defaulted, because a silently-defaulted multiplier would backtest a
different strategy from the one written.

RiskConfig keeps stop_loss_pct/target_pct as properties so the engine is
untouched for now; they raise rather than guess when the stop is ATR-based.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: v2 sizing — rupee notional, with quantity resolution

**Files:**
- Modify: `strategy/vocabulary.py`, `strategy/parse.py`
- Test: `tests/test_strategy_parse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_strategy_parse.py`:

```python
from strategy.parse import resolve_quantity  # noqa: E402  (add to the imports at top)


def test_notional_sizing_parses():
    s = parse_strategy_dict(valid_v2())
    assert s.sizing.type == "notional"
    assert s.sizing.notional_per_trade == 100000.0
    assert s.sizing.quantity is None


def test_notional_is_required_with_no_default():
    doc = valid_v2()
    doc["sizing"] = {"type": "notional"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "notional_per_trade" in str(exc.value)


def test_legacy_fixed_quantity_still_parses():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 5}
    s = parse_strategy_dict(doc)
    assert s.sizing.type == "fixed_quantity"
    assert s.sizing.quantity == 5


def test_sizing_is_required_in_v2():
    doc = valid_v2()
    del doc["sizing"]
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "sizing" in str(exc.value)


def test_resolve_quantity_floors_the_notional():
    s = parse_strategy_dict(valid_v2())          # notional 100000
    assert resolve_quantity(s.sizing, price=1500.0) == 66   # 66.67 -> 66


def test_resolve_quantity_returns_zero_when_a_share_costs_more_than_the_notional():
    doc = valid_v2()
    doc["sizing"] = {"type": "notional", "notional_per_trade": 100}
    s = parse_strategy_dict(doc)
    assert resolve_quantity(s.sizing, price=1500.0) == 0


def test_resolve_quantity_ignores_price_for_fixed_quantity():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 3}
    s = parse_strategy_dict(doc)
    assert resolve_quantity(s.sizing, price=99999.0) == 3
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: FAIL — `resolve_quantity` does not exist and `notional` is not a known sizing type.

- [ ] **Step 3: Update the sizing vocabulary**

In `strategy/vocabulary.py`, replace `SIZING_TYPES` with:

```python
# 'notional' is the recommended mode: a rupee amount per trade, so cost drag is
# identical on a Rs 200 stock and a Rs 4,000 one and results stay comparable
# across a universe. 'fixed_quantity' is retained for v1 strategies and is
# documented as discouraged — at a fixed share count, ranking a universe partly
# ranks it by share price.
SIZING_TYPES: frozenset[str] = frozenset({"notional", "fixed_quantity"})

SIZING_TYPE_KEYS: dict[str, frozenset[str]] = {
    "notional": frozenset({"notional_per_trade"}),
    "fixed_quantity": frozenset({"quantity"}),
}
```

- [ ] **Step 4: Replace `SizingConfig` and `_parse_sizing` in `strategy/parse.py`**

```python
@dataclass(frozen=True)
class SizingConfig:
    """How big a trade is. Exactly one of the two fields is populated."""

    type: str
    notional_per_trade: float | None = None
    quantity: int | None = None


def resolve_quantity(sizing: SizingConfig, price: float) -> int:
    """Shares to trade at `price`. Zero means the trade must be SKIPPED.

    Zero is a real, expected outcome — a Rs 3,000 share against a Rs 1,000
    notional cannot be traded at all. Callers must record the skip rather than
    proceed, because a quantity-0 trade would post a P&L of exactly 0 and land
    in the results as a flat trade that never happened.
    """
    if sizing.type == "fixed_quantity":
        return int(sizing.quantity)
    if price <= 0:
        return 0
    return int(sizing.notional_per_trade // price)


def _parse_sizing(node: Any, where: str) -> SizingConfig:
    node = _require_mapping(node, where)
    if "type" not in node:
        _fail(where, f"missing required key: type. Allowed: {', '.join(sorted(SIZING_TYPES))}")

    stype = node["type"]
    if stype not in SIZING_TYPES:
        _fail(
            f"{where}.type",
            f"unknown sizing type {stype!r}. Allowed: {', '.join(sorted(SIZING_TYPES))}",
        )
    _require_keys(node, where, required={"type"} | set(SIZING_TYPE_KEYS[stype]), optional=set())

    if stype == "notional":
        raw = node["notional_per_trade"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0:
            _fail(f"{where}.notional_per_trade", f"expected a number > 0, got {raw!r}")
        return SizingConfig(type="notional", notional_per_trade=float(raw))

    qty = node["quantity"]
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        _fail(f"{where}.quantity", f"expected a whole number >= 1, got {qty!r}")
    return SizingConfig(type="fixed_quantity", quantity=qty)
```

In `_parse_strategy`, move `"sizing"` from `optional=` to `required=` and delete the `SizingConfig(type="fixed_quantity", quantity=1)` default branch, leaving:

```python
    sizing = _parse_sizing(node["sizing"], f"{where}.sizing")
```

Add `SIZING_TYPE_KEYS` to the vocabulary import block.

- [ ] **Step 5: Update `strategy_to_raw` so round-tripping keeps working**

In `strategy/parse.py`, replace the `"sizing"` line of the returned dict with:

```python
        "sizing": (
            {"type": "notional", "notional_per_trade": strategy.sizing.notional_per_trade}
            if strategy.sizing.type == "notional"
            else {"type": "fixed_quantity", "quantity": strategy.sizing.quantity}
        ),
```

- [ ] **Step 6: Run the tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: PASS.

- [ ] **Step 7: Fix v1 fixtures that omit `sizing`**

`sizing` is now required. In `tests/test_strategy_schema.py`, add to `valid_doc()`:

```python
                "sizing": {"type": "fixed_quantity", "quantity": 1},
```

Any test asserting the old "sizing defaults to quantity 1" behaviour should be replaced by a test asserting the omission is now an error, naming `sizing`.

- [ ] **Step 8: Run the full suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add strategy/ tests/
git commit -m "feat(strategy): rupee-notional sizing; make sizing explicit

At a fixed share count a ~Rs 30 round trip is 15% of a 1.5% move on a Rs 200
stock and 0.75% on a Rs 4,000 one. Ranking 100 symbols that way substantially
ranks them by share price, so this is a measurement defect and belongs in the
format rather than in a later sizing feature.

notional_per_trade is required with no default: a default would be a silent
opinion about acceptable cost drag. resolve_quantity() returns 0 when a share
costs more than the notional, and callers must record that as a skip — a
quantity-0 trade would post as a flat trade that never happened.

fixed_quantity is retained for v1 strategies and documented as discouraged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: v2 `session` block — entry window and square-off

**Files:**
- Modify: `strategy/vocabulary.py`, `strategy/parse.py`
- Test: `tests/test_strategy_parse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_strategy_parse.py`:

```python
from datetime import time  # noqa: E402  (add to the imports at top)


def test_session_is_optional_and_defaults_to_empty():
    s = parse_strategy_dict(valid_v2())
    assert s.session.no_entry_before is None
    assert s.session.no_entry_after is None
    assert s.session.square_off is None


def test_session_times_parse():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "09:30", "no_entry_after": "14:30",
                      "square_off": "15:15"}
    s = parse_strategy_dict(doc)
    assert s.session.no_entry_before == time(9, 30)
    assert s.session.square_off == time(15, 15)


def test_session_rejects_a_non_time_string():
    doc = valid_v2()
    doc["session"] = {"square_off": "quarter past three"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "session.square_off" in msg
    assert "HH:MM" in msg


def test_session_rejects_a_time_outside_the_nse_session():
    doc = valid_v2()
    doc["session"] = {"square_off": "17:00"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "09:15" in str(exc.value) and "15:30" in str(exc.value)


def test_entry_window_must_not_be_inverted():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "14:30", "no_entry_after": "09:30"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "no_entry_before" in str(exc.value)


def test_square_off_must_not_precede_the_entry_window():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "14:00", "square_off": "13:00"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "square_off" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: FAIL — `Strategy` has no `session`.

- [ ] **Step 3: Add session bounds to `strategy/vocabulary.py`**

```python
# NSE cash session, IST. Session keys are validated against these bounds: a
# square_off of 17:00 would silently never trigger, leaving an "intraday"
# strategy holding overnight — exactly the failure the key exists to prevent.
SESSION_OPEN_HHMM = "09:15"
SESSION_CLOSE_HHMM = "15:30"
SESSION_KEYS: frozenset[str] = frozenset(
    {"no_entry_before", "no_entry_after", "square_off"}
)
```

- [ ] **Step 4: Add `SessionConfig` and `_parse_session` to `strategy/parse.py`**

```python
@dataclass(frozen=True)
class SessionConfig:
    """Intraday timing rules. All times are IST; every field is optional.

    `no_entry_before` / `no_entry_after` bound the FILL candle, not the signal
    candle — the fill is when the position actually opens.

    `square_off` closes any open position at the open of the first candle
    starting at or after it, and blocks entry fills from that time. A strategy
    with square_off set never holds overnight.
    """

    no_entry_before: time | None = None
    no_entry_after: time | None = None
    square_off: time | None = None


_SESSION_OPEN = time.fromisoformat(SESSION_OPEN_HHMM)
_SESSION_CLOSE = time.fromisoformat(SESSION_CLOSE_HHMM)


def _parse_time(raw: Any, where: str) -> time:
    if not isinstance(raw, str):
        _fail(
            where,
            f"expected a quoted HH:MM time like \"15:15\", got {raw!r}. "
            "Quote it in YAML — an unquoted 15:15 is not a string.",
        )
    try:
        parsed = time.fromisoformat(raw)
    except ValueError:
        _fail(where, f"expected HH:MM in 24-hour IST, got {raw!r}")
    if parsed.second or parsed.microsecond:
        _fail(where, f"expected HH:MM with no seconds, got {raw!r}")
    if not _SESSION_OPEN <= parsed <= _SESSION_CLOSE:
        _fail(
            where,
            f"{raw} is outside the NSE session "
            f"({SESSION_OPEN_HHMM}-{SESSION_CLOSE_HHMM} IST); it would never trigger",
        )
    return parsed


def _parse_session(node: Any, where: str) -> SessionConfig:
    node = _require_mapping(node, where)
    _require_keys(node, where, required=set(), optional=set(SESSION_KEYS))

    times = {
        key: _parse_time(node[key], f"{where}.{key}")
        for key in SESSION_KEYS
        if key in node
    }
    before, after = times.get("no_entry_before"), times.get("no_entry_after")
    square_off = times.get("square_off")

    if before and after and before >= after:
        _fail(
            where,
            f"no_entry_before ({before:%H:%M}) must be earlier than "
            f"no_entry_after ({after:%H:%M}); as written no entry could ever fill",
        )
    earliest_entry = before or _SESSION_OPEN
    if square_off and square_off <= earliest_entry:
        _fail(
            f"{where}.square_off",
            f"square_off ({square_off:%H:%M}) is at or before the earliest "
            f"possible entry ({earliest_entry:%H:%M}); every position would be "
            "closed on the candle it opened",
        )
    return SessionConfig(
        no_entry_before=before, no_entry_after=after, square_off=square_off
    )
```

Add `from datetime import time` to the imports, and `SESSION_CLOSE_HHMM`, `SESSION_KEYS`, `SESSION_OPEN_HHMM` to the vocabulary import block.

- [ ] **Step 5: Wire it into `Strategy` and `_parse_strategy`**

Add to the `Strategy` dataclass, after `sizing`:

```python
    session: SessionConfig
```

In `_parse_strategy`, add `"session"` to `optional=`, and before the `return`:

```python
    session = (
        _parse_session(node["session"], f"{where}.session")
        if "session" in node
        else SessionConfig()
    )
```

Pass `session=session` in the `Strategy(...)` construction.

- [ ] **Step 6: Emit it from `strategy_to_raw`**

Before the `return` in `strategy_to_raw`, build the block and include it only when non-empty, so a strategy without session rules round-trips to a document without a `session:` key:

```python
    session_raw = {
        key: f"{value:%H:%M}"
        for key, value in (
            ("no_entry_before", strategy.session.no_entry_before),
            ("no_entry_after", strategy.session.no_entry_after),
            ("square_off", strategy.session.square_off),
        )
        if value is not None
    }
```

Then add to the returned dict:

```python
        **({"session": session_raw} if session_raw else {}),
```

- [ ] **Step 7: Run the tests**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: PASS.

- [ ] **Step 8: Run the full suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS — `session` is optional, so v1-shaped fixtures are unaffected.

- [ ] **Step 9: Commit**

```bash
git add strategy/ tests/
git commit -m "feat(strategy): session rules — entry window and intraday square-off

Without square_off a 15-minute strategy with a 0.7% stop can hold overnight and
across weekends, so the backtest quietly includes gap risk the stop never
protected against. That is a different strategy from the one written.

Times are validated against the NSE session: a square_off of 17:00 would never
trigger, which is precisely the silent failure the key exists to prevent. An
inverted entry window and a square_off at or before the earliest possible entry
are both rejected rather than producing a strategy that can never trade.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: `universe:` as an alternative to `instruments:`

**Files:**
- Modify: `strategy/vocabulary.py`, `strategy/parse.py`
- Test: `tests/test_strategy_parse.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_strategy_parse.py`:

```python
def test_universe_replaces_instruments():
    doc = valid_v2()
    del doc["instruments"]
    doc["universe"] = "NIFTY100"
    s = parse_strategy_dict(doc)
    assert s.universe == "NIFTY100"
    assert s.instruments == ()


def test_instruments_still_supported():
    s = parse_strategy_dict(valid_v2())
    assert s.universe is None
    assert s.instruments == ("NSE:RELIANCE",)


def test_both_universe_and_instruments_is_rejected():
    doc = valid_v2()
    doc["universe"] = "NIFTY100"
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "exactly ONE" in str(exc.value)


def test_neither_universe_nor_instruments_is_rejected():
    doc = valid_v2()
    del doc["instruments"]
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "exactly ONE" in str(exc.value)


def test_universe_name_shape_is_validated():
    doc = valid_v2()
    del doc["instruments"]
    doc["universe"] = "nifty 100!"
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "universe" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
```

Expected: FAIL — `universe` is an unknown key.

- [ ] **Step 3: Add the universe name pattern to `strategy/vocabulary.py`**

```python
# A universe name: capitals, digits and underscores. Matches how NSE index
# names are written (NIFTY50, NIFTY_MIDCAP_100) and keeps custom group names
# free of the spaces and punctuation that make them awkward to reference.
UNIVERSE_RE = re.compile(r"^[A-Z0-9_]{2,40}$")
```

- [ ] **Step 4: Update `_parse_strategy` in `strategy/parse.py`**

Change the required/optional sets so `instruments` is no longer unconditionally required:

```python
    _require_keys(
        node, where,
        required={
            "name", "enabled", "position_type", "timeframe",
            "entry", "exit", "risk", "sizing",
        },
        optional={"instruments", "universe", "session", "max_cycles_per_day"},
    )
```

Replace the instruments-parsing block with:

```python
    has_universe = "universe" in node
    has_instruments = "instruments" in node
    if has_universe == has_instruments:
        _fail(
            where,
            "a strategy needs exactly ONE of 'universe' (a named symbol group "
            "like NIFTY100) or 'instruments' (an explicit list like "
            "[NSE:RELIANCE])",
        )

    universe: str | None = None
    instruments: list[str] = []

    if has_universe:
        universe = node["universe"]
        if not isinstance(universe, str) or not UNIVERSE_RE.match(universe):
            _fail(
                f"{where}.universe",
                f"expected a universe name in capitals, digits or underscores "
                f"(e.g. NIFTY100), got {universe!r}",
            )
    else:
        raw_instruments = node["instruments"]
        if not isinstance(raw_instruments, list) or not raw_instruments:
            _fail(f"{where}.instruments", "expected a non-empty list like [NSE:RELIANCE]")
        for i, inst in enumerate(raw_instruments):
            if not isinstance(inst, str) or not INSTRUMENT_RE.match(inst):
                _fail(
                    f"{where}.instruments[{i}]",
                    f"expected 'EXCHANGE:TRADINGSYMBOL' in capitals "
                    f"(e.g. NSE:RELIANCE), got {inst!r}",
                )
            if inst in instruments:
                _fail(f"{where}.instruments[{i}]", f"duplicate instrument {inst!r}")
            instruments.append(inst)
```

Add `universe: str | None` to the `Strategy` dataclass (after `instruments`) with a default of `None`, and pass `universe=universe` in the construction.

- [ ] **Step 5: Update `strategy_to_raw` to emit whichever form is set**

Replace the `"instruments"` entry of the returned dict with:

```python
        **(
            {"universe": strategy.universe}
            if strategy.universe
            else {"instruments": list(strategy.instruments)}
        ),
```

- [ ] **Step 6: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_parse.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 7: Commit**

```bash
git add strategy/ tests/
git commit -m "feat(strategy): accept 'universe:' as an alternative to 'instruments:'

Exactly one is required. Parsing stays pure: only the SHAPE of the name is
checked here, never whether the universe exists — that needs the database, and
validating a pasted strategy has to work offline. Existence is checked at save
time (Task 14).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: `version: 2` and the v1 → v2 migration

**Files:**
- Create: `strategy/migrate.py`, `tests/test_strategy_migrate.py`
- Modify: `strategy/parse.py` (`parse_strategies` version gate), `strategy/__init__.py`
- Test: `tests/test_strategy_migrate.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_migrate.py`:

```python
"""Tests for the v1 -> v2 strategy document migration. Pure."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.migrate import MigrationError, migrate_document  # noqa: E402
from strategy.parse import parse_strategies  # noqa: E402


def v1_doc() -> dict:
    return {
        "version": 1,
        "strategies": [
            {
                "name": "old",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                                   "operator": ">", "value": 50}]},
                "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                                  "operator": "<", "value": 40}]},
                "risk": {"stop_loss_pct": 0.7, "target_pct": 1.5},
                "sizing": {"type": "fixed_quantity", "quantity": 2},
                "max_cycles_per_day": 2,
            }
        ],
    }


def test_migrated_document_is_version_2_and_valid():
    out = migrate_document(v1_doc())
    assert out["version"] == 2
    parse_strategies(out)          # must not raise


def test_risk_keys_become_stop_specs():
    out = migrate_document(v1_doc())
    risk = out["strategies"][0]["risk"]
    assert risk["stop_loss"] == {"type": "percent", "value": 0.7}
    assert risk["target"] == {"type": "percent", "value": 1.5}
    assert "stop_loss_pct" not in risk


def test_fixed_quantity_is_carried_over_unchanged():
    out = migrate_document(v1_doc())
    assert out["strategies"][0]["sizing"] == {"type": "fixed_quantity", "quantity": 2}


def test_sizing_absent_in_v1_becomes_the_v1_default_of_one_share():
    doc = v1_doc()
    del doc["strategies"][0]["sizing"]
    out = migrate_document(doc)
    assert out["strategies"][0]["sizing"] == {"type": "fixed_quantity", "quantity": 1}


def test_no_session_block_is_added():
    # v1 had no square-off, so adding one would change behaviour silently.
    out = migrate_document(v1_doc())
    assert "session" not in out["strategies"][0]


def test_instruments_are_untouched():
    out = migrate_document(v1_doc())
    assert out["strategies"][0]["instruments"] == ["NSE:RELIANCE"]


def test_a_v2_document_passes_through_unchanged():
    out = migrate_document(v1_doc())
    assert migrate_document(out) == out


def test_an_unknown_version_is_rejected_by_name():
    with pytest.raises(MigrationError) as exc:
        migrate_document({"version": 7, "strategies": []})
    assert "7" in str(exc.value)


def test_the_input_document_is_not_mutated():
    original = v1_doc()
    migrate_document(original)
    assert original["version"] == 1
    assert original["strategies"][0]["risk"] == {"stop_loss_pct": 0.7, "target_pct": 1.5}
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_migrate.py -q
```

Expected: FAIL — `strategy.migrate` does not exist.

- [ ] **Step 3: Create `strategy/migrate.py`**

```python
"""One-way migration of a strategy document from format v1 to v2.

Mechanical and lossless. The rule that governs every choice here: a migrated v1
strategy must backtest IDENTICALLY to the way it did before. That is why no
`session:` block is invented — v1 had no square-off, so adding one would change
the strategy while claiming to preserve it — and why `fixed_quantity` is carried
over rather than converted to a notional. Converting a share count to a rupee
amount needs a price, and any price picked here would be a fabrication.

Migration never writes in place: it returns a new document, so the caller
decides whether to persist it.
"""

from __future__ import annotations

import copy
from typing import Any

CURRENT_VERSION = 2


class MigrationError(ValueError):
    """A document is in a version this code cannot migrate."""


def _migrate_strategy_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(raw)

    risk = out.get("risk")
    if isinstance(risk, dict) and "stop_loss_pct" in risk:
        out["risk"] = {
            "stop_loss": {"type": "percent", "value": float(risk["stop_loss_pct"])},
            "target": {"type": "percent", "value": float(risk["target_pct"])},
        }

    # v1 defaulted an absent sizing block to one share; make that explicit so
    # v2's required-sizing rule is satisfied without changing behaviour.
    if "sizing" not in out:
        out["sizing"] = {"type": "fixed_quantity", "quantity": 1}

    return out


def migrate_document(document: Any) -> dict[str, Any]:
    """Return `document` at the current format version.

    A document already at the current version is returned as an unchanged copy,
    so callers can migrate unconditionally on read.
    """
    if not isinstance(document, dict) or "version" not in document:
        raise MigrationError(
            "not a strategy document: expected a mapping with a 'version' key"
        )

    version = document["version"]
    if version == CURRENT_VERSION:
        return copy.deepcopy(document)

    if version != 1:
        raise MigrationError(
            f"unsupported strategy format version {version!r}; "
            f"this code understands 1 and {CURRENT_VERSION}"
        )

    out = copy.deepcopy(document)
    out["version"] = CURRENT_VERSION
    out["strategies"] = [
        _migrate_strategy_v1_to_v2(raw) if isinstance(raw, dict) else raw
        for raw in document.get("strategies", [])
    ]
    return out
```

- [ ] **Step 4: Accept version 2 in `parse_strategies`**

In `strategy/parse.py`, replace the version check with:

```python
    if root["version"] != CURRENT_VERSION:
        _fail(
            "version",
            f"unsupported version {root['version']!r}; this code understands "
            f"version {CURRENT_VERSION}. A version 1 file must be migrated "
            "first — see strategy.migrate.migrate_document().",
        )
```

Add `from strategy.migrate import CURRENT_VERSION` to `strategy/parse.py`.

> Import direction check: `migrate.py` imports nothing from `parse.py`, so this
> stays acyclic. `CURRENT_VERSION` lives in `migrate` because the migration owns
> what "current" means.

- [ ] **Step 5: Make `load_strategies` migrate on read**

In `strategy/parse.py`, inside `load_strategies` and `load_strategy_documents`, migrate the loaded data before validating:

```python
    data = migrate_document(data)
```

Place it immediately after `yaml.safe_load(...)` in both functions, and add `migrate_document` to the `strategy.migrate` import. In `load_strategy_documents`, return `list(data["strategies"])` from the **migrated** data so seeding stores v2 documents.

- [ ] **Step 6: Re-export from `strategy/__init__.py`**

Add `CURRENT_VERSION`, `MigrationError`, `migrate_document` to the imports and `__all__`.

- [ ] **Step 7: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_migrate.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS. `tests/test_strategy_schema.py` fixtures still say `version: 1` and now migrate on the way through — which is exactly the behaviour being asserted.

- [ ] **Step 8: Commit**

```bash
git add strategy/ tests/
git commit -m "feat(strategy): add format v2 with a v1 migration

Migration is deliberately conservative: no session block is invented, because
v1 had no square-off and adding one would change the strategy while claiming to
preserve it; and fixed_quantity is carried over rather than converted to a
notional, because converting a share count to rupees needs a price and any
price chosen here would be fabricated.

Documents migrate on read, never in place — the caller decides what to persist.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Migrate `strategies.yaml` to v2 and prove it backtests identically

The spec's strongest migration claim ("a migrated v1 strategy backtests
identically") is asserted here, against the real file.

**Files:**
- Modify: `strategies.yaml`
- Test: `tests/test_strategy_migrate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_strategy_migrate.py`:

```python
import yaml  # noqa: E402  (add to the imports at top)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_the_shipped_strategies_file_is_already_v2():
    raw = yaml.safe_load((REPO_ROOT / "strategies.yaml").read_text(encoding="utf-8"))
    assert raw["version"] == 2
    parse_strategies(raw)


def test_migrating_the_shipped_file_is_a_no_op():
    raw = yaml.safe_load((REPO_ROOT / "strategies.yaml").read_text(encoding="utf-8"))
    assert migrate_document(raw) == raw
```

And in `tests/test_backtest.py`, add a test that a v1 document and its migrated
v2 form produce the same trades. Use whatever candle-frame helper that file
already provides; if it builds frames inline, mirror that construction:

```python
def test_migrated_v1_strategy_produces_identical_trades(...):
    """The migration promise, asserted rather than assumed."""
    from strategy.migrate import migrate_document
    from strategy.parse import parse_strategies

    v1 = {...}                      # the v1 document shape from test_strategy_migrate
    v2 = migrate_document(v1)

    # v1 can no longer be parsed directly (parse_strategies requires version 2),
    # so compare the migrated strategy against one hand-written in v2 form.
    hand_written_v2 = {...}         # same strategy, v2 keys
    assert parse_strategies(v2)[0] == parse_strategies(hand_written_v2)[0]
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_migrate.py -q
```

Expected: FAIL — `strategies.yaml` still says `version: 1`.

- [ ] **Step 3: Migrate the file with the code that will maintain it**

```bash
.\.venv\Scripts\python.exe -c "import yaml; from strategy.migrate import migrate_document; d=yaml.safe_load(open('strategies.yaml',encoding='utf-8')); print(yaml.safe_dump(migrate_document(d), sort_keys=False))"
```

Use the output to update `strategies.yaml` by hand, **keeping the file's comment
header**. Set `version: 2`, keep the risk block from Task 3, and update the
header's shape reference to describe v2 — `universe`, the stop specs, `sizing`
types, and `session`.

Then switch the example to notional sizing, since the file is what people copy:

```yaml
    sizing:
      type: notional
      notional_per_trade: 100000
```

- [ ] **Step 4: Run the tests and the CLI**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe strategy_schema.py
```

Expected: PASS, and the CLI reports the strategy as valid.

- [ ] **Step 5: Commit**

```bash
git add strategies.yaml tests/
git commit -m "feat(strategy): migrate strategies.yaml to format v2

Switches the shipped example to notional sizing — this file is what people
copy, so leaving it at quantity: 1 would keep propagating the cost trap.

Adds the test the migration promise rests on: a migrated v1 strategy parses to
exactly the same Strategy as the equivalent hand-written v2 one.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Universe snapshot parsing and resolution (pure)

`universes.py` starts pure and offline. Fetching arrives in Task 10.

**Files:**
- Create: `universes.py`, `tests/test_universes.py`, `data/universes/.gitkeep`
- Test: `tests/test_universes.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_universes.py`:

```python
"""Tests for symbol universes. Pure: no network, no database."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from universes import (  # noqa: E402
    UniverseError,
    parse_constituent_csv,
    resolve_universe,
    snapshot_filename,
)

# The real header NSE publishes on its index constituent CSVs.
HEADER = '"Company Name","Industry","Symbol","Series","ISIN Code"'


def csv_text(*rows: str) -> str:
    return "\n".join((HEADER, *rows)) + "\n"


def test_parses_symbols_into_our_notation():
    text = csv_text(
        '"Reliance Industries Ltd.","Oil Gas","RELIANCE","EQ","INE002A01018"',
        '"Tata Consultancy Services Ltd.","IT","TCS","EQ","INE467B01029"',
    )
    assert parse_constituent_csv(text) == ("NSE:RELIANCE", "NSE:TCS")


def test_symbols_are_deduplicated_and_ordered():
    text = csv_text(
        '"B Ltd.","X","BBB","EQ","INE2"',
        '"A Ltd.","X","AAA","EQ","INE1"',
        '"B Ltd.","X","BBB","EQ","INE2"',
    )
    assert parse_constituent_csv(text) == ("NSE:AAA", "NSE:BBB")


def test_non_eq_series_rows_are_skipped():
    text = csv_text(
        '"Good Ltd.","X","GOOD","EQ","INE1"',
        '"Bond 2033","X","757GS2033","GS","INE2"',
    )
    assert parse_constituent_csv(text) == ("NSE:GOOD",)


def test_a_missing_symbol_column_fails_loudly():
    text = '"Company Name","Industry","Series"\n"A","X","EQ"\n'
    with pytest.raises(UniverseError) as exc:
        parse_constituent_csv(text)
    assert "Symbol" in str(exc.value)


def test_an_empty_list_is_rejected_rather_than_returned():
    # An empty universe produces a zero-trade backtest that reads exactly like
    # "no signals found", so it must never be returned as a valid result.
    with pytest.raises(UniverseError) as exc:
        parse_constituent_csv(csv_text())
    assert "no constituents" in str(exc.value).lower()


def test_resolution_reports_symbols_missing_from_the_instruments_table():
    result = resolve_universe(
        name="NIFTY3",
        listed=("NSE:AAA", "NSE:BBB", "NSE:CCC"),
        known={"NSE:AAA", "NSE:CCC"},
        as_of=date(2026, 8, 10),
        source="nse",
    )
    assert result.symbols == ("NSE:AAA", "NSE:CCC")
    assert result.missing == ("NSE:BBB",)
    assert result.is_complete is False
    assert "2 of 3" in result.summary()
    assert "NSE:BBB" in result.summary()


def test_a_fully_resolved_universe_is_complete():
    result = resolve_universe(
        name="NIFTY2",
        listed=("NSE:AAA", "NSE:BBB"),
        known={"NSE:AAA", "NSE:BBB"},
        as_of=date(2026, 8, 10),
        source="nse",
    )
    assert result.is_complete is True
    assert result.missing == ()


def test_resolution_with_nothing_known_fails_rather_than_returning_empty():
    with pytest.raises(UniverseError) as exc:
        resolve_universe(
            name="NIFTY2",
            listed=("NSE:AAA", "NSE:BBB"),
            known=set(),
            as_of=date(2026, 8, 10),
            source="nse",
        )
    assert "NIFTY2" in str(exc.value)
    assert "refresh-instruments" in str(exc.value)


def test_snapshot_filename_is_dated_and_sorts_chronologically():
    assert snapshot_filename("NIFTY50", date(2026, 8, 10)) == "NIFTY50-2026-08-10.csv"
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
```

Expected: FAIL — no module named `universes`.

- [ ] **Step 3: Create `universes.py` (pure parts only)**

```python
"""Named symbol universes: NIFTY50/100/500 and custom groups.

A strategy stores a universe NAME; this module turns that name into symbols.
Resolution is deliberately separate from parsing, so validating a pasted
strategy never needs a network call or a database.

Two honesty rules drive the design, both inherited from the Phase 0 data work:

* An empty universe is never returned. It would produce a zero-trade backtest
  that reads exactly like "no signals found" — indistinguishable from a
  strategy that simply never triggered.
* A partial resolution is never silently narrowed. If NIFTY100 resolves 97
  names, the run says 97 of 100 and names the three, rather than reporting a
  NIFTY100 result computed over 97 stocks.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "universes"

# NSE publishes constituents with these headers. Only Symbol and Series are
# load-bearing; the rest are carried in the file but unused.
COLUMN_SYMBOL = "Symbol"
COLUMN_SERIES = "Series"

# Only ordinary cash equity. NSE index files are overwhelmingly 'EQ', but the
# filter mirrors instruments.py so a bond or SME scrip can never enter a
# universe as though it were a tradable stock.
EQUITY_SERIES = frozenset({"EQ", "BE"})

# The system universes refreshed by scripts/refresh_universes.py.
NSE_INDEX_URLS: dict[str, str] = {
    "NIFTY50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
}


class UniverseError(ValueError):
    """A universe could not be parsed, found, or resolved."""


@dataclass(frozen=True)
class UniverseResolution:
    """The outcome of turning a universe name into tradable symbols."""

    name: str
    as_of: date
    source: str                    # 'nse' | 'snapshot' | 'custom'
    symbols: tuple[str, ...]       # present in the instruments table
    missing: tuple[str, ...]       # listed but not in the instruments table
    listed_count: int

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def summary(self) -> str:
        """One line stating exactly what was resolved. Always logged."""
        head = (
            f"{self.name}: {len(self.symbols)} of {self.listed_count} symbols "
            f"(list dated {self.as_of.isoformat()}, source {self.source})"
        )
        if self.is_complete:
            return head
        return f"{head}; NOT FOUND in instruments: {', '.join(self.missing)}"


def parse_constituent_csv(text: str) -> tuple[str, ...]:
    """Parse an NSE constituent CSV into sorted 'NSE:SYMBOL' notation."""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
    for required in (COLUMN_SYMBOL, COLUMN_SERIES):
        if required not in fieldnames:
            raise UniverseError(
                f"constituent CSV is missing the {required!r} column. "
                f"Found: {', '.join(fieldnames) or '(no header)'}. "
                "NSE may have changed the file format."
            )

    symbols: set[str] = set()
    for row in reader:
        series = (row.get(COLUMN_SERIES) or "").strip().upper()
        symbol = (row.get(COLUMN_SYMBOL) or "").strip().upper()
        if not symbol or series not in EQUITY_SERIES:
            continue
        symbols.add(f"NSE:{symbol}")

    if not symbols:
        raise UniverseError(
            "constituent CSV contained no constituents. Refusing to return an "
            "empty universe: it would produce a zero-trade backtest that looks "
            "identical to a strategy that never triggered."
        )
    return tuple(sorted(symbols))


def resolve_universe(
    *,
    name: str,
    listed: tuple[str, ...],
    known: set[str],
    as_of: date,
    source: str,
) -> UniverseResolution:
    """Intersect a constituent list with the symbols we actually have.

    `known` is the set of symbols present in the `instruments` table. Symbols
    listed by the index but absent from it are reported, never dropped.
    """
    present = tuple(s for s in listed if s in known)
    missing = tuple(s for s in listed if s not in known)

    if not present:
        raise UniverseError(
            f"universe {name!r} resolved to zero symbols: none of its "
            f"{len(listed)} constituents are in the instruments table. "
            "Populate it first with:  "
            ".venv\\Scripts\\python.exe backfill.py --refresh-instruments"
        )

    return UniverseResolution(
        name=name,
        as_of=as_of,
        source=source,
        symbols=present,
        missing=missing,
        listed_count=len(listed),
    )


def snapshot_filename(name: str, as_of: date) -> str:
    """Dated snapshot filename; the ISO date makes them sort chronologically."""
    return f"{name}-{as_of.isoformat()}.csv"
```

- [ ] **Step 4: Create the snapshot directory**

```bash
mkdir -p data/universes && touch data/universes/.gitkeep
```

- [ ] **Step 5: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add universes.py tests/test_universes.py data/universes/.gitkeep
git commit -m "feat(universes): parse NSE constituent lists and resolve them honestly

Two refusals are the point of this module. An empty universe is never returned,
because a zero-trade backtest is indistinguishable from a strategy that never
triggered. And a partial resolution is reported as '97 of 100' with the three
missing names, never silently narrowed to a NIFTY100 result computed over 97
stocks.

Series filtering mirrors instruments.py so a bond or SME scrip cannot enter a
universe as though it were a tradable stock.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 10: Fetch from NSE with dated-snapshot fallback

**Files:**
- Modify: `universes.py`
- Test: `tests/test_universes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universes.py`:

```python
from universes import ConstituentList, load_constituents, newest_snapshot  # noqa: E402


def test_newest_snapshot_picks_the_latest_date(tmp_path):
    (tmp_path / "NIFTY50-2026-01-01.csv").write_text("x", encoding="utf-8")
    (tmp_path / "NIFTY50-2026-08-10.csv").write_text("y", encoding="utf-8")
    (tmp_path / "NIFTY100-2026-12-01.csv").write_text("z", encoding="utf-8")
    path, as_of = newest_snapshot("NIFTY50", snapshot_dir=tmp_path)
    assert path.name == "NIFTY50-2026-08-10.csv"
    assert as_of == date(2026, 8, 10)


def test_newest_snapshot_raises_when_there_is_none(tmp_path):
    with pytest.raises(UniverseError) as exc:
        newest_snapshot("NIFTY50", snapshot_dir=tmp_path)
    assert "NIFTY50" in str(exc.value)


def test_load_constituents_prefers_the_live_fetch(tmp_path):
    (tmp_path / "NIFTY50-2020-01-01.csv").write_text(
        csv_text('"Old Ltd.","X","OLD","EQ","INE0"'), encoding="utf-8"
    )
    live = csv_text('"New Ltd.","X","NEW","EQ","INE1"')
    result = load_constituents(
        "NIFTY50", snapshot_dir=tmp_path, fetcher=lambda url: live
    )
    assert result.symbols == ("NSE:NEW",)
    assert result.source == "nse"
    assert result.warning is None


def test_load_constituents_falls_back_to_the_snapshot_and_warns(tmp_path):
    (tmp_path / "NIFTY50-2026-01-05.csv").write_text(
        csv_text('"Old Ltd.","X","OLD","EQ","INE0"'), encoding="utf-8"
    )

    def broken(url):
        raise OSError("connection reset")

    result = load_constituents("NIFTY50", snapshot_dir=tmp_path, fetcher=broken)
    assert result.symbols == ("NSE:OLD",)
    assert result.source == "snapshot"
    assert result.as_of == date(2026, 1, 5)
    assert "2026-01-05" in result.warning
    assert "connection reset" in result.warning


def test_load_constituents_fails_when_the_fetch_breaks_and_no_snapshot_exists(tmp_path):
    def broken(url):
        raise OSError("connection reset")

    with pytest.raises(UniverseError) as exc:
        load_constituents("NIFTY50", snapshot_dir=tmp_path, fetcher=broken)
    assert "NIFTY50" in str(exc.value)


def test_load_constituents_rejects_an_unknown_index_name(tmp_path):
    with pytest.raises(UniverseError) as exc:
        load_constituents("NIFTY_MADE_UP", snapshot_dir=tmp_path, fetcher=lambda u: "")
    assert "NIFTY_MADE_UP" in str(exc.value)
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
```

Expected: FAIL — `load_constituents` does not exist.

- [ ] **Step 3: Add fetching and fallback to `universes.py`**

Add to the imports:

```python
import re
from typing import Callable
```

Then append:

```python
# Browser-like headers. NSE's archive host rejects default client user agents,
# which is exactly why every list is also kept as a committed snapshot.
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
_FETCH_TIMEOUT_SECONDS = 30

_SNAPSHOT_RE = re.compile(r"^(?P<name>[A-Z0-9_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.csv$")


@dataclass(frozen=True)
class ConstituentList:
    """A universe's membership, with its provenance stated."""

    name: str
    symbols: tuple[str, ...]
    as_of: date
    source: str                 # 'nse' | 'snapshot'
    raw_csv: str
    warning: str | None = None  # set when the live fetch failed


def fetch_constituent_csv(url: str) -> str:
    """Download one NSE constituent CSV. Raises on any non-200."""
    import requests  # imported lazily so the pure paths stay import-light

    response = requests.get(url, headers=_FETCH_HEADERS, timeout=_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def newest_snapshot(name: str, *, snapshot_dir: Path | None = None) -> tuple[Path, date]:
    """Return the most recent committed snapshot for `name`, and its date."""
    directory = snapshot_dir or SNAPSHOT_DIR
    candidates: list[tuple[date, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            match = _SNAPSHOT_RE.match(path.name)
            if match and match.group("name") == name:
                candidates.append((date.fromisoformat(match.group("date")), path))

    if not candidates:
        raise UniverseError(
            f"no committed snapshot for universe {name!r} in {directory}. "
            "Create one with:  "
            ".venv\\Scripts\\python.exe scripts/refresh_universes.py"
        )
    as_of, path = max(candidates)
    return path, as_of


def load_constituents(
    name: str,
    *,
    snapshot_dir: Path | None = None,
    fetcher: Callable[[str], str] | None = None,
    today: date | None = None,
) -> ConstituentList:
    """Get a universe's membership: live from NSE, or the newest snapshot.

    NSE's endpoints are unofficial and bot-hostile, so a failure here is
    expected rather than exceptional. Falling back to the snapshot keeps
    backtests runnable; the warning makes the staleness impossible to miss.
    """
    if name not in NSE_INDEX_URLS:
        raise UniverseError(
            f"unknown system universe {name!r}. "
            f"Known: {', '.join(sorted(NSE_INDEX_URLS))}. "
            "Custom universes are read from the database, not from NSE."
        )

    fetch = fetcher or fetch_constituent_csv
    try:
        text = fetch(NSE_INDEX_URLS[name])
        symbols = parse_constituent_csv(text)
    except Exception as exc:            # noqa: BLE001 - any failure falls back
        try:
            path, as_of = newest_snapshot(name, snapshot_dir=snapshot_dir)
        except UniverseError as snapshot_exc:
            raise UniverseError(
                f"could not fetch {name} from NSE ({exc}) and there is no "
                f"committed snapshot to fall back on. {snapshot_exc}"
            ) from exc
        text = path.read_text(encoding="utf-8")
        return ConstituentList(
            name=name,
            symbols=parse_constituent_csv(text),
            as_of=as_of,
            source="snapshot",
            raw_csv=text,
            warning=(
                f"NSE fetch for {name} failed ({exc}); using the committed "
                f"snapshot dated {as_of.isoformat()}. Membership may be stale — "
                "re-run scripts/refresh_universes.py when NSE is reachable."
            ),
        )

    return ConstituentList(
        name=name,
        symbols=symbols,
        as_of=today or date.today(),
        source="nse",
        raw_csv=text,
    )
```

- [ ] **Step 4: Confirm `requests` is already a dependency**

```bash
grep -n "requests" requirements.txt
```

Expected: a `requests` line. If absent, add `requests>=2.31` and note it in the commit.

- [ ] **Step 5: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add universes.py tests/test_universes.py requirements.txt
git commit -m "feat(universes): fetch from NSE with a dated-snapshot fallback

NSE's archive endpoints are unofficial and reject default client user agents,
so a fetch failure is expected rather than exceptional. Live-only fetching
would mean universes stop resolving whenever NSE changes or blocks — and an
unresolvable universe produces a zero-trade backtest that reads like 'no
signals found'.

Falling back keeps backtests runnable; the warning names the snapshot date so
staleness cannot pass unnoticed.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 11: Storage projection before a large backfill

**Files:**
- Modify: `universes.py`
- Test: `tests/test_universes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_universes.py`:

```python
from universes import project_storage  # noqa: E402


def test_projection_matches_the_spec_figures_for_nifty500():
    projection = project_storage(symbol_count=500, years=2.0)
    assert 18_000_000 < projection.rows < 20_000_000
    assert 2.0 < projection.gigabytes < 2.5
    assert projection.exceeds_free_tier is True


def test_projection_for_nifty50_fits_the_free_tier():
    projection = project_storage(symbol_count=50, years=2.0)
    assert projection.exceeds_free_tier is False


def test_projection_summary_names_the_free_tier_when_exceeded():
    text = project_storage(symbol_count=500, years=2.0).summary()
    assert "500 MB" in text and "free tier" in text.lower()
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
```

Expected: FAIL — `project_storage` does not exist.

- [ ] **Step 3: Add the projection to `universes.py`**

```python
# Storage arithmetic, from the Phase 0 design (section 2.2): ~120 bytes per
# row including index overhead, 75 five-minute candles per trading session, and
# ~250 trading sessions a year.
BYTES_PER_ROW = 120
CANDLES_PER_SESSION = 75
SESSIONS_PER_YEAR = 250
SUPABASE_FREE_TIER_MB = 500


@dataclass(frozen=True)
class StorageProjection:
    """What backfilling a universe will cost, before it is started."""

    symbol_count: int
    years: float
    rows: int
    megabytes: float

    @property
    def gigabytes(self) -> float:
        return self.megabytes / 1024

    @property
    def exceeds_free_tier(self) -> bool:
        return self.megabytes > SUPABASE_FREE_TIER_MB

    def summary(self) -> str:
        head = (
            f"{self.symbol_count} symbols x {self.years:g} years at 5m "
            f"= ~{self.rows:,} rows (~{self.megabytes:,.0f} MB)"
        )
        if not self.exceeds_free_tier:
            return head
        return (
            f"{head}. That exceeds the Supabase free tier "
            f"({SUPABASE_FREE_TIER_MB} MB) — Pro is $25/mo for 8 GB. The "
            "backfill will also take a while: Dhan serves 90 days per request."
        )


def project_storage(*, symbol_count: int, years: float) -> StorageProjection:
    """Projected rows and size for backfilling `symbol_count` at the 5m base."""
    rows = int(symbol_count * years * SESSIONS_PER_YEAR * CANDLES_PER_SESSION)
    megabytes = rows * BYTES_PER_ROW / (1024 * 1024)
    return StorageProjection(
        symbol_count=symbol_count, years=years, rows=rows, megabytes=megabytes
    )
```

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_universes.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add universes.py tests/test_universes.py
git commit -m "feat(universes): project storage cost before a large backfill

NIFTY500 at the 5m base is ~19M rows (~2.2 GB), past the Supabase free tier and
a long paged pull from Dhan. Pointing a strategy at it is a decision with a bill
attached, so the number is stated up front rather than discovered midway.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 12: `scripts/refresh_universes.py`

**Files:**
- Create: `scripts/refresh_universes.py`
- Modify: `supabase_candle_backend.py` (add group upsert helpers)
- Test: manual run (this task is I/O against Supabase and NSE)

- [ ] **Step 1: Read the existing backend to match its conventions**

```bash
.\.venv\Scripts\python.exe -c "print(open('supabase_candle_backend.py',encoding='utf-8').read())" | head -80
```

Note how it obtains a client and wraps errors; mirror that exactly below.

- [ ] **Step 2: Add group helpers to `supabase_candle_backend.py`**

Append to the backend class, following its existing error-wrapping style:

```python
    def upsert_symbol_group(
        self, name: str, *, source: str, as_of: date, description: str | None = None
    ) -> int:
        """Create or update a universe row; returns its id."""
        row = {
            "name": name,
            "source": source,
            "constituents_as_of": as_of.isoformat(),
            "is_system": source == "nse",
        }
        if description is not None:
            row["description"] = description
        self._client.table("symbol_groups").upsert(row, on_conflict="name").execute()
        resp = self._client.table("symbol_groups").select("id").eq("name", name).execute()
        if not resp.data:
            raise RuntimeError(f"symbol_group {name!r} vanished immediately after upsert")
        return int(resp.data[0]["id"])

    def replace_group_members(self, group_id: int, instrument_ids: list[int]) -> None:
        """Set a universe's membership to exactly `instrument_ids`.

        Delete-then-insert rather than upsert: a rebalance REMOVES names, and an
        upsert would leave dropped constituents in the group forever.
        """
        self._client.table("symbol_group_members").delete().eq(
            "group_id", group_id
        ).execute()
        if instrument_ids:
            self._client.table("symbol_group_members").insert(
                [{"group_id": group_id, "instrument_id": i} for i in instrument_ids]
            ).execute()

    def known_symbols(self) -> dict[str, int]:
        """Every symbol in the instruments table, mapped to its id."""
        resp = self._client.table("instruments").select("symbol,id").execute()
        return {row["symbol"]: int(row["id"]) for row in resp.data}
```

Add `from datetime import date` to that module's imports if it is not already there.

- [ ] **Step 3: Create `scripts/refresh_universes.py`**

```python
"""Refresh symbol universes from NSE and write dated snapshots.

Usage (from the project folder):

    .venv\\Scripts\\python.exe scripts/refresh_universes.py
    .venv\\Scripts\\python.exe scripts/refresh_universes.py --universes NIFTY50
    .venv\\Scripts\\python.exe scripts/refresh_universes.py --dry-run

Run this after an index rebalance (roughly twice a year). Snapshots written to
data/universes/ are meant to be COMMITTED: they are what keeps universes
resolvable when NSE blocks or changes its endpoints.

Requires the instruments table to be populated:

    .venv\\Scripts\\python.exe backfill.py --refresh-instruments
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings  # noqa: E402
from universes import (  # noqa: E402
    NSE_INDEX_URLS,
    SNAPSHOT_DIR,
    UniverseError,
    load_constituents,
    project_storage,
    resolve_universe,
    snapshot_filename,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Refresh symbol universes from NSE and write snapshots."
    )
    parser.add_argument(
        "--universes",
        help=f"comma-separated names (default: all of {', '.join(sorted(NSE_INDEX_URLS))})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and report, but write neither snapshots nor the database",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    names = (
        [n.strip().upper() for n in args.universes.split(",") if n.strip()]
        if args.universes
        else sorted(NSE_INDEX_URLS)
    )

    # Same construction path as backfill.py:128-136 — SupabaseStore owns the
    # connection and credential checks; the backend wraps its raw client.
    from db import SupabaseStore
    from supabase_candle_backend import SupabaseCandleBackend

    try:
        store = SupabaseStore.connect(get_settings())
    except Exception as exc:                        # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    backend = SupabaseCandleBackend(store._client)
    known = backend.known_symbols()
    if not known:
        print(
            "instruments table is empty. Populate it first:\n"
            "    .venv\\Scripts\\python.exe backfill.py --refresh-instruments",
            file=sys.stderr,
        )
        return 1

    exit_code = 0
    for name in names:
        try:
            constituents = load_constituents(name)
        except UniverseError as exc:
            print(f"{name}: FAILED — {exc}", file=sys.stderr)
            exit_code = 1
            continue

        if constituents.warning:
            print(f"WARNING: {constituents.warning}", file=sys.stderr)

        try:
            resolution = resolve_universe(
                name=name,
                listed=constituents.symbols,
                known=set(known),
                as_of=constituents.as_of,
                source=constituents.source,
            )
        except UniverseError as exc:
            print(f"{name}: FAILED — {exc}", file=sys.stderr)
            exit_code = 1
            continue

        print(resolution.summary())
        print(f"  {project_storage(symbol_count=len(resolution.symbols), years=2.0).summary()}")

        if args.dry_run:
            continue

        if constituents.source == "nse":
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot = SNAPSHOT_DIR / snapshot_filename(name, constituents.as_of)
            snapshot.write_text(constituents.raw_csv, encoding="utf-8")
            print(f"  snapshot written: {snapshot} (commit this)")

        group_id = backend.upsert_symbol_group(
            name, source=constituents.source if constituents.source == "custom" else "nse",
            as_of=constituents.as_of,
        )
        backend.replace_group_members(
            group_id, [known[s] for s in resolution.symbols]
        )
        print(f"  stored: group {group_id}, {len(resolution.symbols)} members")

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Dry-run it (needs network; database untouched)**

Run **after** Task 13's migration is applied, since `symbol_groups.source` does not exist yet. If running now, expect the DB write to fail and use `--dry-run`:

```bash
.\.venv\Scripts\python.exe scripts/refresh_universes.py --dry-run
```

Expected: for each of NIFTY50/100/500, a resolution summary line and a storage projection. If NSE blocks, a warning naming the snapshot fallback — or a clear failure if no snapshot exists yet, which is correct on a first run.

- [ ] **Step 5: Commit**

```bash
git add scripts/refresh_universes.py supabase_candle_backend.py
git commit -m "feat(universes): add the refresh CLI and Supabase group storage

Membership is replaced rather than upserted: a rebalance REMOVES names, and an
upsert would leave dropped constituents in the group forever.

Prints the storage projection alongside each resolution so pointing a strategy
at NIFTY500 is a knowing decision rather than a surprise mid-backfill.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 13: SQL migration 003

**Files:**
- Create: `sql/003_strategy_v2_universes.sql`
- Test: applied against Supabase, then verified by `db.py`'s self-check

- [ ] **Step 1: Create `sql/003_strategy_v2_universes.sql`**

```sql
-- ---------------------------------------------------------------------------
-- Phase 1: strategy format v2 and symbol universes.
--
-- Additive only. No existing row is rewritten: strategies already stored are
-- valid by definition, so `status` defaults to 'valid'.
--
-- Apply in the Supabase SQL editor, or via the MCP apply_migration tool.
-- ---------------------------------------------------------------------------

-- --- strategies: draft support ---------------------------------------------
-- A draft may contain ANYTHING: an invented timeframe, no position_type at
-- all. That is the point — a strategy pasted from an external AI tool is
-- expected to be wrong on the first attempt, and it has to be storable so it
-- can be corrected. The typed columns therefore become nullable and are
-- populated only for valid rows.
alter table strategies
    add column if not exists raw_source        text,
    add column if not exists format_version    integer,
    add column if not exists status            text not null default 'valid',
    add column if not exists validation_errors jsonb;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.strategies'::regclass
          and conname  = 'strategies_status_check'
    ) then
        alter table strategies add constraint strategies_status_check
            check (status in ('valid', 'draft'));
    end if;
end $$;

alter table strategies alter column enabled       drop not null;
alter table strategies alter column position_type drop not null;
alter table strategies alter column timeframe     drop not null;
alter table strategies alter column definition    drop not null;

-- Phase 0 added the 5m base and 25m; the original CHECK predates both.
-- Validity is decided by the validator, not here — this constraint exists only
-- to stop a hand-edit in the Supabase table editor storing nonsense.
alter table strategies drop constraint if exists strategies_timeframe_check;
alter table strategies add  constraint strategies_timeframe_check
    check (timeframe is null or timeframe in ('5m','15m','25m','30m','60m','day'));

comment on column strategies.raw_source is
    'Exactly what was pasted, kept so a draft can be corrected verbatim.';
comment on column strategies.status is
    'valid = runnable; draft = stored but blocked from every engine.';

comment on table strategies is
    'Strategy definitions. THE source of truth (strategies.yaml is only a '
    'first-run seed). Engines read status = ''valid'' and enabled.';

-- --- symbol_groups: provenance ---------------------------------------------
alter table symbol_groups
    add column if not exists source             text not null default 'custom',
    add column if not exists constituents_as_of date;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.symbol_groups'::regclass
          and conname  = 'symbol_groups_source_check'
    ) then
        alter table symbol_groups add constraint symbol_groups_source_check
            check (source in ('nse', 'custom'));
    end if;
end $$;

comment on column symbol_groups.constituents_as_of is
    'Date of the constituent list this membership came from. A resolved '
    'universe can always state how old it is.';

-- Engines filter on this on every run.
create index if not exists strategies_runnable_idx
    on strategies (status) where status = 'valid';
```

- [ ] **Step 2: Apply it**

Paste into the Supabase SQL editor and run, or use the MCP `apply_migration` tool with the file contents.

- [ ] **Step 3: Verify the schema changed**

```bash
.\.venv\Scripts\python.exe db.py
```

Expected: the existing self-check passes (it verifies the tables it knows about are reachable).

Then confirm the new columns exist:

```bash
.\.venv\Scripts\python.exe -c "from config import get_settings; from db import SupabaseStore; s=SupabaseStore.connect(get_settings()); print(s._table('strategies').select('name,status,raw_source,format_version').limit(1).execute().data)"
```

Expected: a row (or `[]`) with no column error. Existing rows show `status: 'valid'`.

- [ ] **Step 4: Commit**

```bash
git add sql/003_strategy_v2_universes.sql
git commit -m "feat(db): migration 003 — draft strategies and universe provenance

Typed strategy columns become nullable because a draft may contain anything;
validity is decided by the validator, not by CHECK constraints that would
disagree with it. Existing rows default to status 'valid' and are not rewritten.

Also widens the timeframe check, which still predated Phase 0's 5m base and 25m.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 14: `db.py` — draft storage, universe existence check, drafts blocked

**Files:**
- Modify: `db.py:228-348`
- Test: `tests/test_strategy_storage.py`

- [ ] **Step 1: Read the existing storage tests**

```bash
.\.venv\Scripts\python.exe -c "print(open('tests/test_strategy_storage.py',encoding='utf-8').read())"
```

Note how it fakes the Supabase client; the new tests must use the same fake.

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_strategy_storage.py`, using that file's existing fake-store fixture:

```python
def test_a_valid_document_is_saved_with_typed_columns(fake_store):
    result = fake_store.save_strategy_document(valid_v2_doc())
    assert result.status == "valid"
    row = fake_store.last_upsert("strategies")
    assert row["status"] == "valid"
    assert row["timeframe"] == "15m"
    assert row["validation_errors"] is None
    assert row["format_version"] == 2


def test_an_invalid_document_is_saved_as_a_draft_not_rejected(fake_store):
    doc = valid_v2_doc()
    doc["timeframe"] = "7m"
    result = fake_store.save_strategy_document(doc)
    assert result.status == "draft"
    row = fake_store.last_upsert("strategies")
    assert row["status"] == "draft"
    assert row["timeframe"] is None
    assert row["enabled"] is None
    assert "7m" in str(row["validation_errors"])


def test_a_draft_keeps_the_text_it_was_pasted_from(fake_store):
    text = "version: 2\nstrategies:\n  - name: broken\n"
    result = fake_store.save_strategy_text(text)
    assert result.status == "draft"
    assert fake_store.last_upsert("strategies")["raw_source"] == text


def test_unparseable_yaml_becomes_a_draft_naming_the_line(fake_store):
    result = fake_store.save_strategy_text("name: x\n  bad: indent\n")
    assert result.status == "draft"
    assert "line" in " ".join(result.errors).lower()


def test_a_draft_must_still_have_a_usable_name(fake_store):
    # Without a name there is no primary key, so this is the one thing a draft
    # cannot be missing.
    with pytest.raises(DatabaseError) as exc:
        fake_store.save_strategy_text("version: 2\nstrategies: []\n")
    assert "name" in str(exc.value).lower()


def test_an_unknown_universe_makes_it_a_draft(fake_store):
    fake_store.set_known_universes(["NIFTY50"])
    doc = valid_v2_doc()
    del doc["instruments"]
    doc["universe"] = "NIFTY_NOPE"
    result = fake_store.save_strategy_document(doc)
    assert result.status == "draft"
    assert "NIFTY_NOPE" in " ".join(result.errors)
    assert "NIFTY50" in " ".join(result.errors)   # says what IS available


def test_a_known_universe_saves_as_valid(fake_store):
    fake_store.set_known_universes(["NIFTY50"])
    doc = valid_v2_doc()
    del doc["instruments"]
    doc["universe"] = "NIFTY50"
    assert fake_store.save_strategy_document(doc).status == "valid"


def test_list_strategies_excludes_drafts(fake_store):
    fake_store.seed_rows([
        {"name": "good", "status": "valid", "enabled": True, "definition": valid_v2_doc()},
        {"name": "bad", "status": "draft", "enabled": None, "definition": None},
    ])
    assert [s.name for s in fake_store.list_strategies()] == ["good"]


def test_list_strategy_documents_includes_drafts_for_the_editor(fake_store):
    fake_store.seed_rows([
        {"name": "good", "status": "valid", "enabled": True, "definition": valid_v2_doc()},
        {"name": "bad", "status": "draft", "enabled": None, "definition": None},
    ])
    names = [d["name"] for d in fake_store.list_strategy_documents()]
    assert names == ["bad", "good"]
```

- [ ] **Step 3: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_storage.py -q
```

Expected: FAIL — `save_strategy_text`, `SaveResult.status` and the draft path do not exist.

- [ ] **Step 4: Implement draft storage in `db.py`**

Add near the other dataclasses:

```python
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
```

Replace `save_strategy_document` with:

```python
    def known_universe_names(self) -> set[str]:
        """Names of every universe currently in symbol_groups."""
        try:
            resp = self._table("symbol_groups").select("name").execute()
        except APIError as exc:
            raise self._wrap(exc, "listing universes") from exc
        return {row["name"] for row in resp.data}

    def save_strategy_document(
        self, doc: Mapping[str, Any], *, raw_source: str | None = None
    ) -> SaveResult:
        """Create or update one strategy, storing it as a draft if invalid.

        A strategy arriving from an external AI tool is EXPECTED to be wrong on
        the first attempt, so rejecting it outright would mean retyping or
        re-prompting from scratch. Drafts are stored, editable, and blocked from
        every engine by list_strategies().
        """
        doc = dict(doc)
        name = doc.get("name")
        if not isinstance(name, str) or not name.strip():
            # The one thing a draft cannot be missing: it is the primary key.
            raise DatabaseError(
                "a strategy needs a 'name' before it can be saved, even as a "
                "draft — the name is its primary key."
            )
        name = name.strip()

        errors: list[str] = []
        strategy: Strategy | None = None
        try:
            strategy = parse_strategy_dict(doc, where="strategy")
        except ValueError as exc:
            errors.append(str(exc))

        # Universe existence needs the database, so it cannot live in the pure
        # parser — it is checked here instead, on the same save.
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
            name=name,
            status=row["status"],
            strategy=strategy,
            errors=tuple(errors),
        )

    def save_strategy_text(self, text: str) -> SaveResult:
        """Save a strategy pasted as YAML text.

        Accepts either a bare strategy mapping or a full document with a
        `version:` and `strategies:` list — an external AI tool will produce
        either, and both should just work.
        """
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
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
```

Note the YAML-error case raises rather than drafting: with unparseable text
there is no document at all, so there is no `name` to key a draft on. The
message says exactly what to fix.

- [ ] **Step 5: Filter drafts out of `list_strategies`**

Replace the body of `list_strategy_documents` and `list_strategies`:

```python
    def list_strategy_documents(self) -> list[dict[str, Any]]:
        """Every stored strategy INCLUDING drafts — for the editor UI."""
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
            if row.get("enabled") is not None:
                doc["enabled"] = bool(row["enabled"])
            docs.append(doc)
        return docs

    def list_strategies(self) -> list[Strategy]:
        """Load and VALIDATE every RUNNABLE strategy.

        Drafts are excluded here, and this is the only place that decision is
        made — every engine loads through this method.

        Validation still happens on read, not just on write, so a row edited
        directly in the Supabase table editor can never feed the engine
        something malformed.
        """
        strategies = []
        for doc in self.list_strategy_documents():
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
```

Add to `db.py`'s imports:

```python
import yaml

from strategy.migrate import CURRENT_VERSION, MigrationError, migrate_document
```

- [ ] **Step 6: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_storage.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS. `backtest.py:467` and `paper_engine.py:382` call
`seed_strategies_if_empty`, which calls `save_strategy_document` — now returning
`SaveResult` rather than `Strategy`. Check `seed_strategies_if_empty` does not
use the return value; if it does, update it to read `.strategy`.

- [ ] **Step 7: Commit**

```bash
git add db.py tests/test_strategy_storage.py
git commit -m "feat(db): store invalid strategies as drafts; block them from engines

A strategy pasted from an external AI tool is expected to be wrong on the first
attempt, so the correction loop is the common path. Rejecting the paste would
mean retyping or re-prompting from scratch.

Drafts keep the exact text pasted and their error list, and are excluded in
list_strategies() — one filter, in the one place every engine loads from.

Universe existence is checked here rather than in the parser: it needs the
database, and validating a pasted strategy has to work offline.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 15: Remove `sync_strategies`

**Files:**
- Modify: `db.py:322-348`
- Test: `tests/test_strategy_storage.py`

- [ ] **Step 1: Confirm it has no callers**

```bash
grep -rn "sync_strategies" --include=*.py .
```

Expected: only the definition in `db.py`. If anything else appears, stop and update that caller first.

- [ ] **Step 2: Write the failing test**

Append to `tests/test_strategy_storage.py`:

```python
def test_sync_strategies_is_gone(fake_store):
    """It disabled every strategy absent from strategies.yaml.

    With the database owning strategy definitions, that would silently disable
    everything authored in the app on the next engine run.
    """
    assert not hasattr(fake_store, "sync_strategies")


def test_seeding_does_not_touch_existing_strategies(fake_store):
    fake_store.seed_rows([
        {"name": "mine", "status": "valid", "enabled": True, "definition": valid_v2_doc()},
    ])
    assert fake_store.seed_strategies_if_empty([valid_v2_doc()]) == 0
    assert [s.name for s in fake_store.list_strategies()] == ["mine"]
```

- [ ] **Step 3: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_storage.py -q
```

Expected: FAIL on `test_sync_strategies_is_gone`.

- [ ] **Step 4: Delete the method**

Remove `sync_strategies` from `db.py` entirely, along with the now-unused
`asdict` import if nothing else uses it:

```bash
grep -n "asdict" db.py
```

- [ ] **Step 5: Run the tests and confirm the engines still import**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe -c "import backtest, paper_engine; print('imports ok')"
```

Expected: PASS, then `imports ok`.

- [ ] **Step 6: Commit**

```bash
git add db.py tests/test_strategy_storage.py
git commit -m "feat(db): remove sync_strategies

It mirrored strategies.yaml into the table and disabled every strategy absent
from the file. With the database owning definitions, that would silently
disable everything authored in the app on the next engine run.

seed_strategies_if_empty covers the legitimate bootstrap case: it does nothing
once any strategy exists.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 16: Engine — notional quantity per symbol, with skips recorded

**Files:**
- Modify: `backtest.py:117-200`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest.py`, reusing that file's existing frame helper:

```python
def test_notional_sizing_derives_quantity_from_the_entry_price():
    """100000 notional at a ~1000 entry fill buys 99 shares, not 1."""
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 100000})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades[0].quantity == 100


def test_a_share_dearer_than_the_notional_produces_no_trade():
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 500})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades == []


def test_a_skipped_entry_is_reported_not_silently_dropped():
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 500})
    result = simulate_with_skips(frame_with_one_round_trip(entry_open=1000.0),
                                 strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert result.trades == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "notional_below_price"
    assert result.skipped[0].price == 1000.0


def test_fixed_quantity_is_unaffected():
    strategy = strategy_with(sizing={"type": "fixed_quantity", "quantity": 7})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades[0].quantity == 7
```

Add the helpers that file needs, matching its existing style:

```python
def strategy_with(**overrides) -> Strategy:
    """A minimal v2 strategy with one field group replaced."""
    doc = {
        "name": "t", "enabled": True, "position_type": "long", "timeframe": "15m",
        "instruments": ["NSE:TEST"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 0}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
        "risk": {"stop_loss": {"type": "percent", "value": 0.7},
                 "target": {"type": "percent", "value": 1.5}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
    }
    doc.update(overrides)
    return parse_strategy_dict(doc)
```

`frame_with_one_round_trip(entry_open=...)` must build a candle frame whose
entry fills at `entry_open` and which then hits the target. Follow the frame
construction already used in `tests/test_backtest.py`.

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
```

Expected: FAIL — `simulate_with_skips` does not exist and quantity is still `sizing.quantity`.

- [ ] **Step 3: Add the skip record and the sizing call to `backtest.py`**

Add near `SimTrade`:

```python
@dataclass(frozen=True)
class SkippedEntry:
    """An entry signal that could not become a trade.

    Recorded rather than dropped: a signal that never became a position is a
    real fact about the strategy, and a silently-dropped one would make the
    strategy look more selective than it is.
    """

    signal_ts: pd.Timestamp
    price: float
    reason: str          # 'notional_below_price'


@dataclass(frozen=True)
class SimResult:
    trades: list[SimTrade]
    skipped: list[SkippedEntry]
```

Replace `qty = strategy.sizing.quantity` (`backtest.py:148`) with nothing — the
quantity is now per-trade. In the entry branch (`backtest.py:186-199`), after
`e_price = entry_fill(opens[i])`, insert:

```python
                qty = resolve_quantity(strategy.sizing, e_price)
                if qty < 1:
                    skipped.append(
                        SkippedEntry(
                            signal_ts=index[pending_entry_from],
                            price=float(opens[i]),
                            reason="notional_below_price",
                        )
                    )
                    pending_entry_from = None
                    continue
```

Declare `skipped: list[SkippedEntry] = []` beside `trades`, and hold `qty` in
the open-position state alongside `e_price` so `close_position` uses the
trade's own quantity rather than a module-level one. Change `close_position`'s
`quantity=qty` to read that state.

Rename the existing function to `simulate_with_skips` returning `SimResult`, and
keep `simulate` as a thin wrapper so existing callers are untouched:

```python
def simulate(
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    slippage_pct: float,
    cost_per_trade_inr: float,
) -> list[SimTrade]:
    """Completed trades only. See simulate_with_skips for skipped entries."""
    return simulate_with_skips(
        df, strategy,
        slippage_pct=slippage_pct,
        cost_per_trade_inr=cost_per_trade_inr,
    ).trades
```

Add `resolve_quantity` to the `strategy_schema` (or `strategy.parse`) import in `backtest.py`.

- [ ] **Step 4: Surface skips in the run summary**

Find where `backtest.py` prints its per-symbol result line and append the skip
count when non-zero, so it appears in the run output rather than only in the
return value:

```python
        if result.skipped:
            print(
                f"    {len(result.skipped)} entry signal(s) skipped: "
                f"notional_per_trade is below the share price"
            )
```

- [ ] **Step 5: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 6: Verify end to end against the real cached candles**

```bash
.\.venv\Scripts\python.exe backtest.py --no-db --years 2
```

Expected: runs against the Phase 0 cache and reports quantities well above 1.

- [ ] **Step 7: Commit**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "feat(backtest): derive quantity from the notional, per symbol

Quantity is now per-trade rather than per-strategy, because floor(notional /
price) differs on every symbol — which is the whole point: cost drag becomes
identical across a universe instead of 20x heavier on a cheap stock.

An entry that cannot be sized is recorded as a skip, not dropped. A
quantity-0 trade would post a P&L of exactly 0 and land in the results as a
flat trade that never happened.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 17: Engine — ATR stops

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_atr_stop_is_set_from_the_candle_before_the_fill():
    """1.5 x ATR below the entry fill, using the last CLOSED candle's ATR."""
    strategy = strategy_with(risk={
        "stop_loss": {"type": "atr", "period": 2, "multiplier": 1.5},
        "target": {"type": "percent", "value": 1.5},
    })
    df = frame_with_known_atr(entry_open=1000.0, atr_at_signal=10.0)
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    # stop = 1000 - 1.5*10 = 985
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].intended_exit_price == pytest.approx(985.0)


def test_atr_stop_for_a_short_is_above_the_entry():
    strategy = strategy_with(
        position_type="short",
        risk={"stop_loss": {"type": "atr", "period": 2, "multiplier": 1.5},
              "target": {"type": "percent", "value": 1.5}},
    )
    df = frame_with_known_atr_short(entry_open=1000.0, atr_at_signal=10.0)
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].intended_exit_price == pytest.approx(1015.0)


def test_insufficient_history_for_the_atr_period_is_a_hard_error():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "atr", "period": 500, "multiplier": 1.5},
        "target": {"type": "percent", "value": 1.5},
    })
    with pytest.raises(BacktestError) as exc:
        simulate(frame_with_one_round_trip(entry_open=1000.0),
                 strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    msg = str(exc.value)
    assert "500" in msg          # candles needed
    assert "atr" in msg.lower()
```

`frame_with_known_atr` must build a frame whose ATR at the signal candle is
exactly `atr_at_signal` and whose next candles trade down through the resulting
stop. Compute the expected ATR with `indicators.atr` while building it rather
than hand-deriving true ranges.

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
```

Expected: FAIL — ATR stops are not implemented; `RiskConfig.stop_loss_pct` raises.

- [ ] **Step 3: Implement stop levels from `StopSpec`**

In `backtest.py`, before the main loop, precompute the ATR series once when any
spec needs it:

```python
    atr_periods = {
        spec.period
        for spec in (strategy.risk.stop_loss, strategy.risk.target,
                     strategy.risk.trailing_stop)
        if spec is not None and spec.type == "atr"
    }
    atr_series: dict[int, np.ndarray] = {}
    for period in atr_periods:
        if len(df) <= period:
            raise BacktestError(
                f"strategy {strategy.name!r} uses an ATR({period}) stop but only "
                f"{len(df)} candles are available; at least {period + 1} are "
                "needed. Backfill more history, or use a shorter ATR period."
            )
        atr_series[period] = indicators.atr(df, period).to_numpy()
```

Add a `_level_from_spec` helper:

```python
def _level_from_spec(
    spec: StopSpec,
    entry_price: float,
    signal_idx: int,
    atr_series: dict[int, np.ndarray],
    *,
    favourable: bool,
    is_long: bool,
) -> float:
    """Absolute price for a stop or target.

    `favourable` marks a target (moves in the position's favour); a stop moves
    against it. ATR is read at the SIGNAL candle — the last closed candle before
    the fill — so the level never depends on data the fill could not have seen.
    """
    if spec.type == "percent":
        distance = entry_price * spec.value / 100.0
    else:
        atr_value = float(atr_series[spec.period][signal_idx])
        if not atr_value > 0 or atr_value != atr_value:  # zero or NaN
            raise BacktestError(
                f"ATR({spec.period}) is not available at the entry candle; "
                "the series is still warming up. Backfill more history."
            )
        distance = atr_value * spec.multiplier

    moves_up = favourable if is_long else not favourable
    return entry_price + distance if moves_up else entry_price - distance
```

Replace the `sl_price` / `tgt_price` assignment in the entry branch with:

```python
                sl_price = _level_from_spec(
                    strategy.risk.stop_loss, e_price, pending_entry_from,
                    atr_series, favourable=False, is_long=is_long,
                )
                tgt_price = _level_from_spec(
                    strategy.risk.target, e_price, pending_entry_from,
                    atr_series, favourable=True, is_long=is_long,
                )
```

Define `BacktestError(RuntimeError)` in `backtest.py` if it does not exist, and
import `StopSpec` and `numpy as np` as needed.

- [ ] **Step 4: Remove the compatibility properties**

Now that the engine reads `StopSpec` directly, delete `stop_loss_pct` and
`target_pct` from `RiskConfig` in `strategy/parse.py`, and update the CLI
summary in `strategy_schema.py` to describe either form:

```python
        stop = s.risk.stop_loss
        stop_text = (
            f"{stop.value}%" if stop.type == "percent"
            else f"{stop.multiplier}x ATR({stop.period})"
        )
        target = s.risk.target
        target_text = (
            f"{target.value}%" if target.type == "percent"
            else f"{target.multiplier}x ATR({target.period})"
        )
```

and use `SL {stop_text} / target {target_text}` in the printed line. Remove the
two tests from Task 3 that asserted the properties existed
(`test_percent_properties_stay_available_for_the_engine`).

- [ ] **Step 5: Run the tests, then the suite and the CLI**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe strategy_schema.py
```

Expected: PASS; the CLI prints `SL 0.7% / target 1.5%`.

- [ ] **Step 6: Commit**

```bash
git add backtest.py strategy/ strategy_schema.py tests/
git commit -m "feat(backtest): ATR-based stops and targets

ATR is read at the SIGNAL candle — the last closed candle before the fill — so
a level can never depend on data the fill could not have seen.

Insufficient history is a hard error naming the period and the candle count,
not a silent zero-trade result: a strategy that produced no trades because its
indicator never warmed up looks identical to one whose edge does not exist.

Removes RiskConfig's percent-only compatibility properties now the engine reads
the stop specs directly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 18: Engine — trailing stop

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_trailing_stop_follows_the_high_and_exits_on_the_pullback():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},      # far, so trailing wins
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    # Entry at 1000; highs 1010 then 1020; then a drop through 1020*0.99 = 1009.8
    trades = simulate(frame_trailing(entry_open=1000.0, highs=[1010, 1020], drop_to=1000),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "trailing_stop"
    assert trades[0].intended_exit_price == pytest.approx(1009.8)


def test_trailing_stop_does_not_use_the_same_candle_it_was_set_from():
    """The look-ahead case.

    A candle that makes a new high AND falls back through the level implied by
    that same high must NOT exit at it — at the time the low happened, the high
    had not been observed yet.
    """
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    trades = simulate(frame_high_and_reversal_in_one_candle(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason != "trailing_stop"


def test_trailing_stop_never_loosens():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    # High 1020 then a lower high 1005: the level must stay at 1009.8.
    trades = simulate(frame_trailing(entry_open=1000.0, highs=[1020, 1005], drop_to=1000),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].intended_exit_price == pytest.approx(1009.8)


def test_the_tighter_of_fixed_and_trailing_wins():
    """Early in a trade the fixed stop is tighter and must still apply."""
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 0.5},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 5.0},
    })
    trades = simulate(frame_immediate_drop(entry_open=1000.0, to=990.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].intended_exit_price == pytest.approx(995.0)
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
```

Expected: FAIL — trailing stops are not implemented.

- [ ] **Step 3: Implement trailing in `backtest.py`**

Add to the open-position state, set on entry:

```python
                best_price = e_price      # highest high (long) / lowest low (short)
                trail_price = None        # active trailing level, or None
```

In the intra-candle exit block, check the trailing level **before** the fixed
stop only if it is tighter — implemented as a single effective stop:

```python
        if in_pos:
            effective_stop = sl_price
            reason = "stop_loss"
            if trail_price is not None:
                tighter = max(sl_price, trail_price) if is_long else min(sl_price, trail_price)
                if tighter != sl_price:
                    effective_stop, reason = tighter, "trailing_stop"
```

then use `effective_stop` and `reason` in place of `sl_price` / `"stop_loss"` in
the four existing comparisons.

**After** the exit checks for candle `i`, and only while still in the position,
update the trail from this candle's extreme so it applies from candle `i+1`:

```python
        if in_pos and strategy.risk.trailing_stop is not None:
            # Updated AFTER this candle's exit checks: the high that sets the
            # level was not observable when this candle's low happened, so
            # using it here would be look-ahead.
            best_price = max(best_price, highs[i]) if is_long else min(best_price, lows[i])
            candidate = _level_from_spec(
                strategy.risk.trailing_stop, best_price, i,
                atr_series, favourable=False, is_long=is_long,
            )
            # Never loosens.
            trail_price = (
                candidate if trail_price is None
                else (max(trail_price, candidate) if is_long else min(trail_price, candidate))
            )
```

Reset `best_price` and `trail_price` on every entry, and make sure
`close_position` accepts the new `reason` value.

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "feat(backtest): trailing stop, updated at close and applied next candle

The ordering is the whole correctness question. A candle that makes a new high
AND falls back through the level implied by that same high must not exit at it:
when the low happened, the high had not been observed. So the trail is updated
only AFTER the candle's exit checks, and applies from the next candle.

The level never loosens, and the tighter of fixed and trailing always wins.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 19: Engine — entry window and square-off

**Files:**
- Modify: `backtest.py`
- Test: `tests/test_backtest.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_no_entry_before_blocks_the_early_fill():
    strategy = strategy_with(session={"no_entry_before": "10:00"})
    trades = simulate(frame_signalling_at(ist_times=["09:20", "10:05"]),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].entry_fill_ts.tz_convert(IST).strftime("%H:%M") == "10:05"


def test_no_entry_after_blocks_the_late_fill():
    strategy = strategy_with(session={"no_entry_after": "14:00"})
    trades = simulate(frame_signalling_at(ist_times=["14:30"]),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades == []


def test_the_window_applies_to_the_fill_candle_not_the_signal_candle():
    """Signal at 09:59 fills at 10:00 and is allowed."""
    strategy = strategy_with(session={"no_entry_before": "10:00"})
    trades = simulate(frame_signal_at_then_fill_at("09:59", "10:00"),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert len(trades) == 1


def test_square_off_closes_at_the_open_of_the_first_candle_at_or_after_it():
    strategy = strategy_with(session={"square_off": "15:15"})
    df = frame_open_position_through("15:20")
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "square_off"
    assert trades[0].exit_fill_ts.tz_convert(IST).strftime("%H:%M") == "15:15"


def test_square_off_means_no_position_survives_the_day():
    strategy = strategy_with(session={"square_off": "15:15"})
    df = frame_spanning_two_days()
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    for trade in trades:
        entry_day = trade.entry_fill_ts.tz_convert(IST).date()
        exit_day = trade.exit_fill_ts.tz_convert(IST).date()
        assert entry_day == exit_day


def test_no_session_block_leaves_v1_behaviour_unchanged():
    strategy = strategy_with()          # no session key
    df = frame_open_position_through("15:20")
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert all(t.exit_reason != "square_off" for t in trades)
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
```

Expected: FAIL — session rules are not implemented.

- [ ] **Step 3: Implement the session rules in `backtest.py`**

Precompute each candle's IST time once, before the loop:

```python
    ist_times = [ts.astimezone(IST).time() for ts in index]
    session = strategy.session
```

In the entry branch, guard the fill:

```python
                fill_time = ist_times[i]
                if session.no_entry_before and fill_time < session.no_entry_before:
                    pending_entry_from = None
                    continue
                if session.no_entry_after and fill_time > session.no_entry_after:
                    pending_entry_from = None
                    continue
                if session.square_off and fill_time >= session.square_off:
                    pending_entry_from = None
                    continue
```

Add the square-off exit **after** the stop/target checks, so it is last in the
worst-case ordering:

```python
        if in_pos and session.square_off and ist_times[i] >= session.square_off:
            close_position(i, opens[i], "square_off")
```

Import `IST` from `config` in `backtest.py` if it is not already imported.

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add backtest.py tests/test_backtest.py
git commit -m "feat(backtest): entry window and intraday square-off

The window applies to the FILL candle, not the signal candle — the fill is when
the position actually opens.

Square-off is checked after the stop and target so it stays last in the engine's
existing worst-case ordering, and it also blocks entry fills from that time: an
entry that would be closed on the candle it opened is not a trade.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 20: `docs/STRATEGY_FORMAT.md`, generated from the vocabulary

**Files:**
- Create: `scripts/gen_strategy_format_doc.py`, `docs/STRATEGY_FORMAT.md`, `tests/test_strategy_format_doc.py`
- Test: `tests/test_strategy_format_doc.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_strategy_format_doc.py`:

```python
"""The format reference must match the validator.

A reference that drifts from what the parser accepts sends you in circles
debugging strategies that were never going to validate — and this document's
whole job is to be pasted into an external AI tool as ground truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from gen_strategy_format_doc import render_document  # noqa: E402
from strategy.parse import parse_strategies  # noqa: E402

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_FORMAT.md"


def test_the_committed_document_is_up_to_date():
    assert DOC_PATH.read_text(encoding="utf-8") == render_document(), (
        "docs/STRATEGY_FORMAT.md is stale. Regenerate it:\n"
        "    .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py"
    )


def test_every_indicator_in_the_vocabulary_is_documented():
    from strategy.vocabulary import INDICATOR_PARAMS

    text = render_document()
    for indicator in INDICATOR_PARAMS:
        assert f"`{indicator}`" in text, f"{indicator} missing from the format doc"


def test_every_operator_is_documented():
    from strategy.vocabulary import ALL_OPERATORS

    text = render_document()
    for operator in ALL_OPERATORS:
        assert operator in text


def test_the_worked_example_in_the_document_actually_validates():
    """The example is what an AI tool will imitate, so it must be correct."""
    import re

    import yaml

    text = render_document()
    blocks = re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)
    assert blocks, "the format doc has no YAML example"
    parse_strategies(yaml.safe_load(blocks[0]))
```

- [ ] **Step 2: Run to verify failure**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_format_doc.py -q
```

Expected: FAIL — the generator does not exist.

- [ ] **Step 3: Create `scripts/gen_strategy_format_doc.py`**

```python
"""Generate docs/STRATEGY_FORMAT.md from the strategy vocabulary.

    .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py

The generated page is meant to be pasted into an external AI tool (ChatGPT or
similar) BEFORE asking it for a strategy, so the tool emits something this
system actually accepts. Generating it rather than hand-writing it is what
stops the reference drifting from the validator; a test asserts they agree.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUPPORTED_TIMEFRAMES  # noqa: E402
from strategy.migrate import CURRENT_VERSION  # noqa: E402
from strategy.vocabulary import (  # noqa: E402
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    MAX_STOP_PERCENT,
    POSITION_TYPES,
    PRICE_SOURCES,
    SESSION_CLOSE_HHMM,
    SESSION_OPEN_HHMM,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
    STOP_TYPES,
)

DOC_PATH = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_FORMAT.md"

EXAMPLE = """version: 2

strategies:
  - name: TF-EMA-RSI-15m-v2
    enabled: true
    position_type: long
    timeframe: 15m

    universe: NIFTY100

    entry:
      all:
        - indicator: ema
          params: { period: 9 }
          operator: crosses_above
          compare_to: { indicator: ema, params: { period: 21 } }
        - indicator: rsi
          params: { period: 14 }
          operator: ">"
          value: 50

    exit:
      any:
        - indicator: ema
          params: { period: 9 }
          operator: crosses_below
          compare_to: { indicator: ema, params: { period: 21 } }

    sizing:
      type: notional
      notional_per_trade: 100000

    risk:
      stop_loss: { type: percent, value: 0.7 }
      target:    { type: percent, value: 1.5 }
      trailing_stop: { type: percent, value: 0.5 }

    session:
      no_entry_before: "09:30"
      square_off: "15:15"

    max_cycles_per_day: 2
"""


def _indicator_rows() -> str:
    rows = []
    for name in sorted(INDICATOR_PARAMS):
        params = INDICATOR_PARAMS[name]
        params_text = ", ".join(f"`{p}`" for p in sorted(params)) or "—"
        outputs = INDICATOR_OUTPUTS.get(name)
        outputs_text = ", ".join(f"`{o}`" for o in outputs) if outputs else "—"
        rows.append(f"| `{name}` | {params_text} | {outputs_text} |")
    return "\n".join(rows)


def render_document() -> str:
    """Build the whole page. Pure — returns text, writes nothing."""
    return f"""# Strategy format v{CURRENT_VERSION}

<!-- GENERATED FILE — do not edit by hand.
     Regenerate: .venv\\\\Scripts\\\\python.exe scripts/gen_strategy_format_doc.py -->

Paste this whole page into ChatGPT (or any similar tool) before asking it to
write a strategy. It describes exactly what this system accepts, so what comes
back will validate on the first attempt more often than not.

If a strategy does not validate, the error names the exact location — for
example `strategies[0].entry.all[1].operator` — and lists what is allowed
there. Paste that error back to the tool and it will usually correct itself.

## Worked example

```yaml
{EXAMPLE}```

## Rules

* `version` must be `{CURRENT_VERSION}`.
* Every strategy needs **exactly one** of `universe:` (a named group like
  `NIFTY100`) or `instruments:` (an explicit list like `[NSE:RELIANCE]`).
* `position_type`: {", ".join(f"`{p}`" for p in sorted(POSITION_TYPES))}.
* `timeframe`: {", ".join(f"`{t}`" for t in SUPPORTED_TIMEFRAMES)}.
* Conditions are grouped with `all:` (AND) or `any:` (OR); groups may nest.
* Every condition compares a left operand against **either** a fixed `value`
  **or** another operand under `compare_to` — exactly one of the two.
* Unknown keys are rejected rather than ignored, so a typo like `perod: 14` is
  reported instead of silently changing what the strategy does.

## Indicators

| indicator | required params | `output` options |
|---|---|---|
{_indicator_rows()}

Raw price and volume series ({", ".join(f"`{s}`" for s in sorted(PRICE_SOURCES))})
take no params.

`source:` selects which series a moving average is computed over and is allowed
only on {", ".join(f"`{s}`" for s in sorted(SOURCE_ALLOWED_FOR))}. A volume
average is written `{{indicator: sma, source: volume, params: {{period: 20}}}}`.

## Operators

Comparison: {", ".join(f"`{o}`" for o in sorted(COMPARISON_OPERATORS))} —
**quote these in YAML**, e.g. `operator: ">"`.

Crossing: {", ".join(f"`{o}`" for o in sorted(CROSS_OPERATORS))} — true when the
value was on the other side on the PREVIOUS closed candle and is on this side
on the current one. Never evaluated on a forming candle.

## Sizing

`sizing` is required, with one of these types:
{", ".join(f"`{s}`" for s in sorted(SIZING_TYPES))}.

```yaml
sizing: {{ type: notional, notional_per_trade: 100000 }}   # recommended
sizing: {{ type: fixed_quantity, quantity: 5 }}            # legacy, discouraged
```

Prefer `notional`. Quantity is derived per symbol as
`floor(notional_per_trade / entry price)`, so trading costs weigh the same on a
cheap stock as an expensive one — with a fixed share count they do not, and a
comparison across many symbols partly becomes a comparison of share prices.

## Risk

`stop_loss` and `target` are required; `trailing_stop` is optional. Each is one
of these types: {", ".join(f"`{s}`" for s in sorted(STOP_TYPES))}.

```yaml
risk:
  stop_loss: {{ type: percent, value: 0.7 }}            # 0.7% from entry
  target:    {{ type: atr, period: 14, multiplier: 3 }} # 3 x ATR(14)
  trailing_stop: {{ type: percent, value: 0.5 }}
```

Percent values must be between 0 and {MAX_STOP_PERCENT:g}. Do not mix the two
forms — `{{type: atr, value: 1.5}}` is rejected.

The trailing stop follows the candle high (long) or low (short), updates at the
candle close, applies from the next candle, and never loosens. The tighter of
the fixed and trailing stop always applies.

## Session

Every key is optional; all times are IST, quoted, between
`{SESSION_OPEN_HHMM}` and `{SESSION_CLOSE_HHMM}`.

```yaml
session:
  no_entry_before: "09:30"
  no_entry_after:  "14:30"
  square_off:      "15:15"
```

`no_entry_before` / `no_entry_after` bound the candle the entry **fills** on.
`square_off` closes any open position at the open of the first candle at or
after that time, and blocks entries from then — so a strategy with `square_off`
never holds overnight.

## Order of exits within one candle

Signal exit → stop loss (including trailing) → target → square-off. A price gap
through a level fills at the candle open, not at the level.
"""


def main() -> int:
    DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
    DOC_PATH.write_text(render_document(), encoding="utf-8")
    print(f"wrote {DOC_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Generate the document**

```bash
.\.venv\Scripts\python.exe scripts/gen_strategy_format_doc.py
```

Expected: `wrote .../docs/STRATEGY_FORMAT.md`.

- [ ] **Step 5: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_strategy_format_doc.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS. If `test_the_worked_example_in_the_document_actually_validates`
fails, the example is wrong — fix `EXAMPLE`, not the test.

- [ ] **Step 6: Commit**

```bash
git add scripts/gen_strategy_format_doc.py docs/STRATEGY_FORMAT.md tests/test_strategy_format_doc.py
git commit -m "docs: generate STRATEGY_FORMAT.md from the vocabulary

This page is what you paste into ChatGPT before asking it for a strategy, so it
has to be ground truth. Generating it from vocabulary.py — and testing that the
committed copy matches, and that its worked example actually validates — is what
stops it drifting from the parser.

A stale reference would send you in circles debugging strategies that were never
going to validate.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 21: Paste box in the Strategies page

**Files:**
- Modify: `app_pages/strategies.py`
- Test: manual (Streamlit UI)

- [ ] **Step 1: Read the existing page**

```bash
.\.venv\Scripts\python.exe -c "print(open('app_pages/strategies.py',encoding='utf-8').read())"
```

Note how it renders the form near line 222 and how it reports save errors.
Match those conventions rather than inventing new ones.

- [ ] **Step 2: Add a paste tab above the existing form**

```python
def render_paste_box(ctx) -> None:
    """Paste a strategy generated elsewhere — the Pine-Script-style workflow."""
    st.subheader("Paste a strategy")
    st.caption(
        "Generated one in ChatGPT? Paste the YAML here. Copy the format "
        "reference below into the chat first so it knows what this system "
        "accepts."
    )

    with st.expander("Format reference (copy this into ChatGPT first)"):
        format_doc = Path("docs/STRATEGY_FORMAT.md").read_text(encoding="utf-8")
        st.code(format_doc, language="markdown")

    text = st.text_area("Strategy YAML", height=320, key="paste_yaml")
    if not st.button("Validate and save", key="paste_save"):
        return
    if not text.strip():
        st.warning("Nothing to save yet.")
        return

    try:
        result = ctx.store().save_strategy_text(text)
    except DatabaseError as exc:
        st.error(str(exc))
        return

    if result.is_valid:
        st.success(f"Saved '{result.name}'. It is ready to backtest.")
        return

    st.warning(
        f"Saved '{result.name}' as a **draft**. It will not run until these "
        "are fixed:"
    )
    for message in result.errors:
        st.markdown(f"- `{message}`")
    st.caption(
        "Paste those messages back into ChatGPT and it will usually correct "
        "itself."
    )
```

Call `render_paste_box(ctx)` from the page's main render function, above the
existing builder form. Add `from pathlib import Path` and the `DatabaseError`
import if they are missing.

- [ ] **Step 3: Mark drafts in the strategy list**

Wherever the page lists existing strategies, show the draft state — a stored
strategy that cannot run must never look identical to one that can:

```python
        if doc.get("status") == "draft":
            st.markdown(f"**{doc['name']}** :orange[draft — will not run]")
        else:
            st.markdown(f"**{doc['name']}**")
```

- [ ] **Step 4: Boot the dashboard and exercise both paths**

```bash
.\.venv\Scripts\python.exe -m streamlit run dashboard.py
```

Check in the browser:
1. Paste the worked example from `docs/STRATEGY_FORMAT.md` → saves as valid.
2. Change `timeframe: 15m` to `timeframe: 7m` and paste → saves as a draft, and
   the error names `timeframe` and lists the allowed values.
3. The draft appears in the list marked as a draft.
4. `universe: NIFTY_NOPE` → draft, and the error lists the universes that exist.

- [ ] **Step 5: Commit**

```bash
git add app_pages/strategies.py
git commit -m "feat(ui): paste a strategy generated by an external AI tool

Carries the format reference inline so the workflow is self-contained: copy it
into ChatGPT, paste the result back here. Invalid strategies save as drafts with
their errors listed, and the errors are written to be pasted straight back into
the chat.

Drafts are labelled in the list — a strategy that cannot run must never look
identical to one that can.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Full suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

- [ ] **Real backtest against the Phase 0 cache**

```bash
.\.venv\Scripts\python.exe backtest.py --no-db --years 2
```

Expected: runs offline, quantities derived from the notional.

- [ ] **Universe refresh end to end**

```bash
.\.venv\Scripts\python.exe scripts/refresh_universes.py --universes NIFTY50
```

Expected: a resolution summary, a storage projection, a written snapshot, and
stored membership. Commit the snapshot.

- [ ] **Check the definition of done**

Walk section 10 of the spec and confirm each box. The two that need deliberate
checking rather than a test run:
- Reading a universe with NSE unreachable (temporarily point `NSE_INDEX_URLS`
  at an invalid host and confirm the snapshot warning appears).
- App-authored strategies surviving an engine run (create one in the UI, run
  `paper_engine.py`, confirm it is still enabled).

---

## Self-review notes

Checked against the spec:

- §2.1 DB ownership → Tasks 14, 15
- §2.2 drafts → Tasks 13, 14, 21
- §2.3 universe reference → Task 6; **per-run snapshot recording is Phase 2's**
  results table, and is explicitly out of scope here — the spec places the
  column in Phase 2. `UniverseResolution` carries everything Phase 2 needs to
  record it.
- §2.4 NSE + snapshot → Tasks 9, 10, 12
- §2.5 notional sizing → Tasks 4, 16
- §2.6 storage projection → Tasks 11, 12
- §3.1 module table → Tasks 1, 2, 9
- §4 format v2 → Tasks 3, 4, 5, 6
- §4.1 semantics → Tasks 16 (quantity), 17 (ATR), 18 (trailing), 19 (session)
- §4.2 migration → Tasks 7, 8
- §5 data model → Task 13
- §6 error handling → distributed; each row has a named test
- §7 ChatGPT handoff → Task 20
- §8 testing → each task's tests

One deviation worth flagging at execution time: Task 17 removes the
`stop_loss_pct` / `target_pct` compatibility properties added in Task 3. They
exist only so the suite stays green between those tasks — if Tasks 3 and 17 are
executed in one sitting, the properties can be skipped entirely.
