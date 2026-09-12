# Research Loop — Piece 3: The AI Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command runs a whole research day: Opus proposes a strategy, the tool checks and tests it on training years, Opus reviews the result and decides what to try next, up to 7 versions — then the pick and locked year are evaluated and the run is stored, with a journal note for tomorrow.

**Architecture:** Five new pure modules (`summary`, `checker`, `prompts`, `loop`, `journal`) and one impure one (`brain`, which shells out to `claude -p`). `loop.py` takes the brain and the sweep as parameters, so the entire decision logic is testable with fakes and no network. `run_day.py` wires them to the existing piece 1 and 2 machinery.

**Tech Stack:** Python 3.11, Claude Code CLI (`claude -p`, Opus 5 on the Pro plan), Supabase, pytest 8.3.4.

**Spec:** `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` §2.3, §2.4, §3, §4, §5.2, §5.6, §8 — build piece 3 of 4.

---

## Context for the implementer

**What already exists (pieces 1 and 2, merged to `main`):**
- `research/sweep.py` — `run_sweep(...)` over 1,213 combinations, `ComboResult(symbol, timeframe, is_index, trades, skipped_reason, first_candle)`, `count_results`.
- `research/windows.py` — `ResearchWindows`, `universe_data_end`, `training(...)`, `training_days(..., data_from=)`, `LOCKED_DAYS`.
- `research/picker.py` — `pick_best(results, cost_model, window_days_for=...)` → `Pick(result, training)`.
- `research/lakh.py` — `compound`, `just_holding`, `equity_series`, `passed`.
- `research/records.py` — `run_row`, `combo_rows`, `locked_trade_rows`, `equity_rows`.
- `research/store.py` — `save_run(client, run=..., combos=..., locked_trades=..., equity=...)`, `ResearchStoreError`.
- `research/evaluate.py` — the hand-run command that does all of the above for ONE existing strategy. `run_day.py` is its autonomous sibling and should reuse, not duplicate, its sequence.
- Tables from `sql/011_research.sql`, including `research_versions` and `research_notes`, both still unwritten.

**Measured facts that must not be contradicted:** DATA_END 2026-07-31; locked year 2025-08-01 → 2026-07-31; a full sweep is 1,213 combinations (1,177 testable) and takes about 929 s on 8 workers, roughly 15 minutes.

**The rule that matters most (§2.4):** Opus must never see locked-year numbers. Every AI input is built from `TrainingSummary` objects, which have no locked-year fields; the locked year is evaluated after the last AI call. Task 3 includes the test that enforces this.

**How Claude is called (verified against the CLI docs, 2026-09-12):**
```
claude -p "<instruction>" --safe-mode --disallowed-tools "*" \
  --permission-mode dontAsk --permission-prompts none \
  --model opus --output-format json --json-schema '<schema>' --max-turns 1
```
- `--disallowed-tools "*"` removes every tool, so Claude cannot read files, run commands or reach the network. This is what makes §2.4 structural.
- `--safe-mode` disables CLAUDE.md, skills, plugins, hooks and MCP servers **but preserves authentication**, so the Pro subscription still works. `--bare` must NOT be used: it refuses OAuth credentials and demands an API key.
- `--json-schema` makes the model return a shape; the reply is in the envelope's `structured_output` field, with `result` holding the text and `total_cost_usd` an estimate.
- Large context goes on **stdin** (capped at 10 MB); the `-p` argument carries the instruction.
- Exit code is non-zero when the run fails. A usage-limit or auth failure is printed as the result on stdout.

**Environment:** Windows 11, Bash tool with Git Bash syntax, `./.venv/Scripts/python.exe` from the repo root. Branch `feat/research-piece3`. Tests are flat files in `tests/`, each inserting the repo root on `sys.path`.

**Cost discipline:** every live call spends the owner's Pro allowance, which is shared with his own Claude use and has been exhausted once already today. Live testing uses `--max-versions 2` until the very last step.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `research/sweep.py` | modify | record each combination's buy-and-hold return |
| `research/summary.py` | create | `TrainingSummary` (§5.2), built from training results only |
| `research/checker.py` | create | proposal JSON → validated strategy document, research rules (§5.6) |
| `research/prompts.py` | create | build the propose and review prompts |
| `research/brain.py` | create | call `claude -p`, parse, classify failures |
| `research/loop.py` | create | the version loop, limits and stop decisions |
| `research/journal.py` | create | the daily notes file and `research_notes` rows |
| `research/store.py` | modify | save a research strategy and its version rows |
| `research/run_day.py` | create | the whole day, end to end |
| `tests/test_research_summary.py` | create | including the no-locked-year guard |
| `tests/test_research_checker.py` | create | |
| `tests/test_research_prompts.py` | create | including the no-locked-year guard |
| `tests/test_research_brain.py` | create | fake runner; command shape |
| `tests/test_research_loop.py` | create | limits, decisions, budget |
| `tests/test_research_journal.py` | create | |

---

### Task 1: Record buy-and-hold per combination

Opus must be able to tell an edge from a rising market (§5.2), which needs each combination's hold return beside its strategy return.

**Files:**
- Modify: `research/sweep.py`
- Test: `tests/test_research_sweep.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_research_sweep.py`:
```python
def test_a_result_records_what_simply_holding_would_have_returned(tmp_path):
    result = run(Combo("NSE:ABC", "day", False), reader(tmp_path))
    first, last = CLOSES[0], CLOSES[-1]
    assert result.hold_return_pct == pytest.approx(100 * (last - first) / first, abs=0.01)


def test_a_skipped_combination_has_no_hold_return(tmp_path):
    assert run(Combo("NSE:EMPTY", "day", False), reader(tmp_path)).hold_return_pct is None
```
Add `import pytest` to that file's imports if absent.

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_sweep.py -q -p no:cacheprovider`
Expected: `AttributeError: 'ComboResult' object has no attribute 'hold_return_pct'`.

- [ ] **Step 3: Implement**

In `research/sweep.py`, add the field to `ComboResult`, directly after `first_candle`:
```python
    # What simply holding this symbol over the same window would have returned,
    # in percent. Without it a rising market looks like an edge.
    hold_return_pct: float | None = None
```

In `run_combo`, replace the final return with:
```python
    first_close = float(frame["close"].iloc[0])
    last_close = float(frame["close"].iloc[-1])
    return ComboResult(
        combo.symbol, combo.timeframe, combo.is_index, tuple(result.trades),
        first_candle=frame.index[0].to_pydatetime(),
        hold_return_pct=(
            round(100 * (last_close - first_close) / first_close, 4) if first_close else None
        ),
    )
```

- [ ] **Step 4: Run to verify pass, then the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_sweep.py -q -p no:cacheprovider` then the full suite.
Expected: 0 failed.

- [ ] **Step 5: Commit**

```bash
git add research/sweep.py tests/test_research_sweep.py
git commit -m "feat(research): record buy-and-hold beside every combination

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: The training summary

**Files:**
- Create: `research/summary.py`
- Test: `tests/test_research_summary.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_summary.py`:
```python
"""What Opus is shown after a version is tested: training years only."""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.summary import TrainingSummary, build_summary  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

LOCKED_WORDS = ("locked", "lakh", "hold_end", "verdict", "beat_holding")


def winner(symbol, timeframe, n=3, pct=5.0, hold=2.0):
    trades = tuple(
        trade(entry=ist(2024, 1, 2 + i, 10), exit_=ist(2024, 1, 2 + i, 14),
              entry_price=100.0, exit_price=100.0 * (1 + pct / 100))
        for i in range(n)
    )
    return ComboResult(symbol, timeframe, False, trades, hold_return_pct=hold)


def loser(symbol, timeframe, n=2, pct=-4.0, hold=1.0):
    return winner(symbol, timeframe, n=n, pct=pct, hold=hold)


def test_totals_count_every_tested_combination():
    got = build_summary([winner("A", "day"), loser("B", "day")], FREE, window_days_for=lambda r: 365)
    assert got.combos_tested == 2
    assert got.total_trades == 5


def test_fees_are_reported_beside_the_gross(): 
    got = build_summary([winner("A", "day")], FREE, window_days_for=lambda r: 365)
    assert got.net_pnl == pytest.approx(got.gross_pnl - got.fees_paid, abs=0.01)


def test_per_timeframe_shows_where_the_edge_was():
    got = build_summary(
        [winner("A", "day"), loser("B", "60m")], FREE, window_days_for=lambda r: 365
    )
    frames = {row["timeframe"]: row for row in got.per_timeframe}
    assert frames["day"]["net_pnl"] > 0 and frames["60m"]["net_pnl"] < 0
    assert frames["day"]["symbols_profitable_pct"] == 100.0


def test_holding_is_reported_per_timeframe():
    """So Opus can tell an edge from a rising market."""
    got = build_summary([winner("A", "day", hold=7.5)], FREE, window_days_for=lambda r: 365)
    assert got.per_timeframe[0]["avg_hold_return_pct"] == pytest.approx(7.5)


def test_top_and_bottom_are_capped_and_ordered():
    combos = [winner(f"S{i}", "day", pct=float(i)) for i in range(1, 21)]
    got = build_summary(combos, FREE, window_days_for=lambda r: 365)
    assert len(got.top) == 15 and len(got.bottom) == 15
    assert got.top[0]["net_pnl"] >= got.top[-1]["net_pnl"]
    assert got.bottom[0]["net_pnl"] <= got.bottom[-1]["net_pnl"]


def test_skips_are_counted_by_reason():
    combos = [
        winner("A", "day"),
        ComboResult("B", "day", False, (), "no candles in window"),
        ComboResult("C", "day", False, (), "no candles in window"),
    ]
    got = build_summary(combos, FREE, window_days_for=lambda r: 365)
    assert got.skipped == {"no candles in window": 2}
    assert got.combos_tested == 1


def test_a_summary_with_nothing_tested_is_still_a_summary():
    got = build_summary([ComboResult("A", "day", False, (), "no candles in window")],
                        FREE, window_days_for=lambda r: 365)
    assert got.combos_tested == 0 and got.total_trades == 0 and got.top == []


def test_the_summary_carries_no_locked_year_field():
    """The guarantee of design 2.4: what Opus sees cannot contain the exam."""
    fields = {f.name for f in dataclasses.fields(TrainingSummary)}
    assert not any(word in name for name in fields for word in LOCKED_WORDS)
    blob = json.dumps(build_summary([winner("A", "day")], FREE,
                                    window_days_for=lambda r: 365).as_dict()).lower()
    assert not any(word in blob for word in LOCKED_WORDS)
```

- [ ] **Step 2: Run to verify failure**

Expected: `ModuleNotFoundError: No module named 'research.summary'`.

- [ ] **Step 3: Implement**

Create `research/summary.py`:
```python
"""What Opus is shown after a version is tested (design 5.2).

TRAINING ONLY. This object is the single channel between the sweep and the AI,
and it has no locked-year field - which is what makes design 2.4 structural
rather than a promise. A test asserts it, by field name and by serialised
content.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from costs import HoldingCostModel
from research.lakh import compound
from research.sweep import ComboResult

LIST_SIZE = 15


@dataclass(frozen=True)
class TrainingSummary:
    combos_tested: int
    combos_profitable: int
    total_trades: int
    win_rate_pct: float | None
    net_pnl: float
    gross_pnl: float
    fees_paid: float
    per_timeframe: list[dict[str, Any]] = field(default_factory=list)
    top: list[dict[str, Any]] = field(default_factory=list)
    bottom: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "combos_tested": self.combos_tested,
            "combos_profitable": self.combos_profitable,
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate_pct,
            "net_pnl": self.net_pnl,
            "gross_pnl": self.gross_pnl,
            "fees_paid": self.fees_paid,
            "per_timeframe": self.per_timeframe,
            "top": self.top,
            "bottom": self.bottom,
            "skipped": self.skipped,
        }


def _row(result: ComboResult, cost_model: HoldingCostModel, window_days: int) -> dict[str, Any]:
    scored = compound(result.trades, cost_model, window_days=window_days)
    wins = sum(1 for t in result.trades if t.net_pnl > 0)
    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "trades": len(result.trades),
        "win_rate_pct": round(100 * wins / len(result.trades), 2) if result.trades else None,
        "net_pnl": round(result.net_pnl, 2),
        "worst_dip_pct": scored.worst_dip_pct,
        "hold_return_pct": result.hold_return_pct,
    }


def build_summary(
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> TrainingSummary:
    tested = [r for r in results if r.skipped_reason is None]
    skipped: dict[str, int] = {}
    for r in results:
        if r.skipped_reason is not None:
            skipped[r.skipped_reason] = skipped.get(r.skipped_reason, 0) + 1

    rows = [_row(r, cost_model, window_days_for(r)) for r in tested]
    trades = [t for r in tested for t in r.trades]
    gross = sum(t.gross_pnl for t in trades)
    fees = sum(t.costs for t in trades)
    wins = sum(1 for t in trades if t.net_pnl > 0)

    by_timeframe: dict[str, list[ComboResult]] = {}
    for r in tested:
        by_timeframe.setdefault(r.timeframe, []).append(r)

    per_timeframe = []
    for timeframe, group in sorted(by_timeframe.items()):
        profitable = sum(1 for r in group if r.net_pnl > 0)
        holds = [r.hold_return_pct for r in group if r.hold_return_pct is not None]
        per_timeframe.append({
            "timeframe": timeframe,
            "combos": len(group),
            "trades": sum(len(r.trades) for r in group),
            "net_pnl": round(sum(r.net_pnl for r in group), 2),
            "symbols_profitable_pct": round(100 * profitable / len(group), 2),
            "avg_hold_return_pct": round(sum(holds) / len(holds), 2) if holds else None,
        })

    ordered = sorted(rows, key=lambda row: row["net_pnl"], reverse=True)
    return TrainingSummary(
        combos_tested=len(tested),
        combos_profitable=sum(1 for r in tested if r.net_pnl > 0),
        total_trades=len(trades),
        win_rate_pct=round(100 * wins / len(trades), 2) if trades else None,
        net_pnl=round(sum(t.net_pnl for t in trades), 2),
        gross_pnl=round(gross, 2),
        fees_paid=round(fees, 2),
        per_timeframe=per_timeframe,
        top=ordered[:LIST_SIZE],
        bottom=list(reversed(ordered[-LIST_SIZE:])) if ordered else [],
        skipped=skipped,
    )
```

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/summary.py tests/test_research_summary.py
git commit -m "feat(research): the training summary Opus is shown

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: The checker

**Files:**
- Create: `research/checker.py`
- Test: `tests/test_research_checker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_checker.py`:
```python
"""Research rules, applied to whatever Opus proposes (design 5.6)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.checker import CheckError, check_proposal, research_name  # noqa: E402

GOOD = """
name: whatever-opus-called-it
position_type: long
timeframe: 60m
instruments: [NSE:RELIANCE]
entry:
  all:
    - indicator: close
      operator: ">"
      compare_to: {indicator: sma, params: {period: 50}}
exit:
  any:
    - indicator: close
      operator: "<"
      compare_to: {indicator: sma, params: {period: 50}}
risk:
  stop_loss: {type: atr, period: 14, multiplier: 2.0}
  target: {type: atr, period: 14, multiplier: 4.0}
sizing: {type: fixed_quantity, quantity: 1}
max_cycles_per_day: 2
"""


def checked(text=GOOD, **kwargs):
    return check_proposal(text, idea="dip buyer", day=date(2026, 9, 12), version=1, **kwargs)


def test_sizing_is_forced_to_one_lakh_notional():
    """A fixed share count would rank symbols by share price, not by signal."""
    doc = checked().document
    assert doc["sizing"] == {"type": "notional", "notional_per_trade": 100000}


def test_the_strategy_is_never_enabled():
    assert checked().document["enabled"] is False


def test_symbols_and_timeframe_are_replaced_because_the_sweep_supplies_them():
    doc = checked().document
    assert doc["instruments"] == ["NSE:RELIANCE"]
    assert doc["timeframe"] == "5m"


def test_the_name_is_generated_not_taken_from_the_proposal():
    assert checked().document["name"] == "R-20260912-dip-buyer-v1"


def test_research_name_slugifies_awkward_titles():
    assert research_name(date(2026, 9, 12), "RSI(2) + Volume!! spike", 3) == "R-20260912-rsi-2-volume-spike-v3"


def test_a_short_strategy_must_square_off():
    text = GOOD.replace("position_type: long", "position_type: short")
    with pytest.raises(CheckError, match="square_off"):
        check_proposal(text, idea="x", day=date(2026, 9, 12), version=1)


def test_a_short_strategy_with_square_off_is_accepted():
    text = (GOOD.replace("position_type: long", "position_type: short")
            + "session:\n  square_off: \"15:10\"\n")
    assert check_proposal(text, idea="x", day=date(2026, 9, 12), version=1).document["position_type"] == "short"


def test_broken_yaml_is_a_check_error_naming_the_problem():
    with pytest.raises(CheckError, match="YAML"):
        check_proposal("entry: [unclosed", idea="x", day=date(2026, 9, 12), version=1)


def test_an_invalid_strategy_reports_the_parser_message():
    text = GOOD.replace('operator: ">"', 'operator: "≥"')
    with pytest.raises(CheckError, match="operator"):
        check_proposal(text, idea="x", day=date(2026, 9, 12), version=1)


def test_a_v3_machine_is_accepted_and_still_gets_the_research_rules():
    text = yaml.safe_dump({
        "version": 3, "name": "x", "timeframe": "60m", "universe": "NIFTY50",
        "initial": "flat", "states": [
            {"name": "flat", "transitions": [
                {"when": "close > sma(20)", "enter": {"side": "long"}, "goto": "holding"}]},
            {"name": "holding", "transitions": [
                {"when": "close < sma(20)", "exit": {"reason": "signal"}, "goto": "flat"}]},
        ],
        "risk": {"stop_loss": {"type": "percent", "value": 2.0},
                 "target": {"type": "percent", "value": 4.0}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
        "max_cycles_per_day": 2,
    })
    got = check_proposal(text, idea="machine", day=date(2026, 9, 12), version=2)
    assert got.document["sizing"]["notional_per_trade"] == 100000
    assert got.document["name"] == "R-20260912-machine-v2"
    assert got.strategy.position_type == "long"
```

- [ ] **Step 2: Run to verify failure**

Expected: `ModuleNotFoundError: No module named 'research.checker'`.

- [ ] **Step 3: Implement**

Create `research/checker.py`:
```python
"""Apply the research rules to whatever Opus proposes (design 5.6).

The rules are applied, not requested: a proposal that names its own symbols or
sizing is corrected rather than rejected, because those are the sweep's job and
arguing about them in the prompt wastes a version. What IS rejected is a
strategy that cannot be parsed, or a short strategy with no square-off - an
overnight short is a different risk from the one being researched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from strategy.v3 import CURRENT_V3_VERSION, is_v3_document, parse_machine
from strategy_schema import parse_strategy_dict

# The sweep supplies the real symbols and timeframe per combination. These
# placeholders only have to parse: 5m is the lowest stock timeframe, so a rule
# referencing any higher timeframe is still legal.
PLACEHOLDER_TIMEFRAME = "5m"
PLACEHOLDER_INSTRUMENTS = ["NSE:RELIANCE"]
NOTIONAL_PER_TRADE = 100000


class CheckError(ValueError):
    """A proposal that cannot be used. The message is sent back to Opus."""


@dataclass(frozen=True)
class CheckedProposal:
    document: dict[str, Any]
    strategy: Any


def research_name(day: date, idea: str, version: int) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:40] or "idea"
    return f"R-{day:%Y%m%d}-{slug}-v{version}"


def check_proposal(
    strategy_yaml: str, *, idea: str, day: date, version: int
) -> CheckedProposal:
    try:
        doc = yaml.safe_load(strategy_yaml)
    except yaml.YAMLError as exc:
        raise CheckError(f"the strategy is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CheckError("the strategy must be a mapping of key: value lines")

    doc = dict(doc)
    doc["name"] = research_name(day, idea, version)
    doc["enabled"] = False
    doc["sizing"] = {"type": "notional", "notional_per_trade": NOTIONAL_PER_TRADE}
    doc["timeframe"] = PLACEHOLDER_TIMEFRAME
    doc.pop("universe", None)
    doc["instruments"] = list(PLACEHOLDER_INSTRUMENTS)

    if doc.get("position_type") == "short":
        session = doc.get("session") or {}
        if not session.get("square_off"):
            raise CheckError(
                "a short strategy needs session.square_off, e.g. \"15:10\": an "
                "overnight short is a different risk from the one being researched."
            )

    try:
        strategy = (
            parse_machine(doc) if is_v3_document(doc)
            else parse_strategy_dict(doc, where="strategy")
        )
    except ValueError as exc:
        raise CheckError(str(exc)) from exc

    return CheckedProposal(document=doc, strategy=strategy)
```

Note for v3: `is_v3_document` keys on `version == CURRENT_V3_VERSION`, and a v3 document carries `universe`/`instruments` at the top level too, so the same replacements apply. Import `CURRENT_V3_VERSION` even if only referenced in a comment — remove the import if flake8 objects.

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/checker.py tests/test_research_checker.py
git commit -m "feat(research): apply the research rules to a proposal

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The prompts

**Files:**
- Create: `research/prompts.py`
- Test: `tests/test_research_prompts.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_prompts.py`:
```python
"""What Opus is asked, built only from training-side facts."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.prompts import (  # noqa: E402
    PROPOSE_SCHEMA,
    REVIEW_SCHEMA,
    propose_prompt,
    review_prompt,
)
from research.summary import build_summary  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

LOCKED_WORDS = ("locked year", "lakh", "just holding", "verdict", "2026-07-31")


def a_summary():
    trades = tuple(
        trade(entry=ist(2024, 1, 2 + i, 10), exit_=ist(2024, 1, 2 + i, 14),
              entry_price=100.0, exit_price=105.0)
        for i in range(3)
    )
    return build_summary(
        [ComboResult("NSE:A", "day", False, trades, hold_return_pct=2.0)],
        FREE, window_days_for=lambda r: 365,
    )


def test_propose_carries_the_format_docs_and_the_rules():
    text = propose_prompt(formats=["V2 FORMAT DOC", "V3 FORMAT DOC"], notes=[], ideas_tried=[])
    assert "V2 FORMAT DOC" in text and "V3 FORMAT DOC" in text
    assert "notional" in text and "square_off" in text


def test_propose_includes_recent_notes_and_what_was_already_tried():
    text = propose_prompt(
        formats=["F"],
        notes=[{"day": date(2026, 9, 11), "idea_title": "gap fade",
                "lessons": "gaps close less often than expected"}],
        ideas_tried=["2026-09-11 gap fade — lost money in training"],
    )
    assert "gap fade" in text and "gaps close less often" in text
    assert "2026-09-11 gap fade" in text


def test_propose_asks_for_a_first_version_or_a_change():
    first = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    again = propose_prompt(formats=["F"], notes=[], ideas_tried=[],
                           previous=a_summary(), change_hint="make it trade less")
    assert "change_note" in first
    assert "make it trade less" in again


def test_review_shows_the_training_result_and_asks_for_a_decision():
    text = review_prompt(summary=a_summary(), version=2, versions_left=5)
    assert "combos_tested" in text and "decision" in text
    assert "next_version" in text and "new_idea" in text and "stop" in text


def test_the_last_version_is_told_it_must_stop():
    text = review_prompt(summary=a_summary(), version=7, versions_left=0)
    assert "must" in text.lower() and "stop" in text
    assert "last version" in text.lower()


def test_no_prompt_can_mention_the_locked_year():
    """Design 2.4: the exam is not in anything the builder is shown."""
    texts = [
        propose_prompt(formats=["F"], notes=[], ideas_tried=[], previous=a_summary()),
        review_prompt(summary=a_summary(), version=1, versions_left=6),
    ]
    for text in texts:
        lowered = text.lower()
        assert not any(word in lowered for word in LOCKED_WORDS)


def test_the_schemas_require_the_fields_the_loop_reads():
    assert set(PROPOSE_SCHEMA["required"]) == {
        "title", "description", "hypothesis", "strategy_yaml", "change_note"}
    assert set(REVIEW_SCHEMA["required"]) == {
        "why_failed", "why_worked", "lessons", "decision"}
    assert REVIEW_SCHEMA["properties"]["decision"]["enum"] == [
        "next_version", "new_idea", "stop"]
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Create `research/prompts.py`:
```python
"""Build what Opus is asked, from training-side facts only.

Every input here is a TrainingSummary, a research note, or a format document.
None of them carries a locked-year number, which is the whole of design 2.4:
the builder cannot use what it cannot see. A test asserts the built text
mentions nothing from the locked year.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from research.summary import TrainingSummary

PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "hypothesis", "strategy_yaml", "change_note"],
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "description": {"type": "string", "maxLength": 400},
        "hypothesis": {"type": "string", "maxLength": 600},
        "strategy_yaml": {"type": "string"},
        "change_note": {"type": "string", "maxLength": 400},
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["why_failed", "why_worked", "lessons", "decision"],
    "properties": {
        "why_failed": {"type": "string", "maxLength": 800},
        "why_worked": {"type": "string", "maxLength": 800},
        "lessons": {"type": "string", "maxLength": 800},
        "decision": {"type": "string", "enum": ["next_version", "new_idea", "stop"]},
        "change_hint": {"type": "string", "maxLength": 400},
    },
}

RULES = """\
Rules the tool applies to whatever you write, so do not spend words on them:
- sizing is forced to notional, 100000 rupees per trade
- the symbols and timeframe you write are replaced: every strategy is tested on
  200 NSE stocks across 5m, 15m, 25m, 30m, 60m and day, and on 9 indexes at 60m
  and day
- enabled is forced to false; nothing you write can trade real or paper money
- a short strategy MUST set session.square_off, e.g. "15:10"
- fees are charged per trade, and a position held overnight pays delivery
  charges (about 0.21% of turnover) rather than intraday ones
"""


def _summary_block(summary: TrainingSummary) -> str:
    return json.dumps(summary.as_dict(), indent=2, default=str)


def propose_prompt(
    *,
    formats: Sequence[str],
    notes: Sequence[dict[str, Any]],
    ideas_tried: Sequence[str],
    previous: TrainingSummary | None = None,
    change_hint: str | None = None,
) -> str:
    parts = [
        "You are designing ONE trading strategy to be tested on Indian equities.",
        "",
        RULES,
    ]
    if notes:
        parts.append("\nWhat earlier days learned:")
        for note in notes:
            parts.append(f"- {note.get('day')}: {note.get('idea_title')} — {note.get('lessons')}")
    if ideas_tried:
        parts.append("\nIdeas already tried (do not repeat one that failed the same way):")
        parts.extend(f"- {line}" for line in ideas_tried)
    if previous is not None:
        parts.append("\nHow your previous version did on the training years:")
        parts.append(_summary_block(previous))
    if change_hint:
        parts.append(f"\nThe change to make this time: {change_hint}")
    parts.append("\nStrategy format reference:")
    parts.extend(formats)
    parts.append(
        "\nReturn the strategy as YAML in strategy_yaml. Write change_note as an "
        "empty string for a first version, otherwise say in one line what you "
        "changed and why."
    )
    return "\n".join(parts)


def review_prompt(*, summary: TrainingSummary, version: int, versions_left: int) -> str:
    parts = [
        f"Your version {version} was tested on the training years. Here is how it did.",
        "",
        _summary_block(summary),
        "",
        "Judge it honestly. A strategy that made money while simply holding the "
        "same stocks made more has no edge; say so.",
        "",
        "Then decide what happens next:",
        "- next_version: keep this idea and change one thing (say what in change_hint)",
        "- new_idea: this idea is not worth more versions; start a different one",
        "- stop: nothing further is worth trying today",
    ]
    if versions_left <= 0:
        parts.append(
            "\nThis was the LAST version allowed today, so decision MUST be "
            "\"stop\". The tool will use this version as final_version."
        )
    else:
        parts.append(f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)
```

The review schema deliberately has no `final_version`. The loop treats the version it just reviewed as the candidate, and when a day ends without a clean `stop` the caller picks the best valid version by its training score. That is simpler than trusting the model to number its own versions, and it still matches design §2.5: Opus chooses the version, the tool picks the combination.

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/prompts.py tests/test_research_prompts.py
git commit -m "feat(research): build the prompts from training facts only

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The brain

**Files:**
- Create: `research/brain.py`
- Test: `tests/test_research_brain.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_brain.py`:
```python
"""Calling Claude Code headlessly, and reading what comes back."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.brain import BrainError, BrainStopped, Claude, build_command  # noqa: E402

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}


class FakeRun:
    """Stands in for subprocess.run."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self


def envelope(structured=None, result="ok"):
    body = {"result": result, "session_id": "s1", "total_cost_usd": 0.12}
    if structured is not None:
        body["structured_output"] = structured
    return json.dumps(body)


def test_the_command_removes_every_tool():
    command = build_command("do it", SCHEMA, model="opus")
    assert "--disallowed-tools" in command and "*" in command
    assert "--safe-mode" in command
    assert "--bare" not in command          # bare mode refuses the Pro login


def test_the_command_asks_for_the_schema_and_json():
    command = build_command("do it", SCHEMA, model="opus")
    assert "--output-format" in command and "json" in command
    assert json.loads(command[command.index("--json-schema") + 1]) == SCHEMA


def test_a_structured_reply_comes_back_as_a_dict():
    run = FakeRun(stdout=envelope({"a": "hello"}))
    assert Claude(runner=run).ask("prompt", SCHEMA) == {"a": "hello"}


def test_the_prompt_is_piped_on_stdin():
    run = FakeRun(stdout=envelope({"a": "hello"}))
    Claude(runner=run).ask("a very long context", SCHEMA)
    assert run.calls[0][1]["input"] == "a very long context"


def test_a_reply_without_structured_output_is_an_error():
    run = FakeRun(stdout=envelope(None, result="I cannot do that"))
    with pytest.raises(BrainError, match="no structured output"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_unreadable_output_is_an_error_that_quotes_it():
    run = FakeRun(stdout="not json at all")
    with pytest.raises(BrainError, match="not json at all"):
        Claude(runner=run).ask("prompt", SCHEMA)


@pytest.mark.parametrize("text", [
    "Claude usage limit reached. Your limit will reset at 12:40pm",
    "OAuth token has expired. Please run /login",
    "Invalid API key · Please run /login",
])
def test_a_usage_or_auth_failure_stops_the_day(text):
    run = FakeRun(stdout=envelope(None, result=text), returncode=1)
    with pytest.raises(BrainStopped):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_any_other_failure_is_a_plain_error():
    run = FakeRun(stdout="", stderr="something broke", returncode=2)
    with pytest.raises(BrainError, match="something broke"):
        Claude(runner=run).ask("prompt", SCHEMA)
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Create `research/brain.py`:
```python
"""Ask Opus, with no tools and a fixed answer shape.

Every call is one non-interactive `claude -p` with EVERY tool removed, so
Claude cannot read a file, run a command or reach the network. That is what
makes design 2.4 enforceable: it knows only what the prompt builder chose to
put in front of it.

`--safe-mode` (not `--bare`) is deliberate: both skip local configuration, but
bare mode refuses the subscription login and demands an API key, and this
system runs on the Pro plan.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

TIMEOUT_SECONDS = 900

# Failures that end the day rather than the version: no retry can fix them.
_STOP_MARKERS = (
    "usage limit",
    "rate limit",
    "oauth token has expired",
    "invalid api key",
    "please run /login",
    "credit balance",
)


class BrainError(RuntimeError):
    """One call failed in a way the loop may retry or record."""


class BrainStopped(RuntimeError):
    """The day cannot continue: usage limit, or no valid credential."""


def build_command(instruction: str, schema: dict[str, Any], *, model: str) -> list[str]:
    return [
        shutil.which("claude") or "claude",
        "-p", instruction,
        "--safe-mode",
        "--disallowed-tools", "*",
        "--permission-mode", "dontAsk",
        "--permission-prompts", "none",
        "--model", model,
        "--max-turns", "1",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
    ]


class Claude:
    """One `claude -p` call per ask. No session is carried between calls."""

    def __init__(self, *, model: str = "opus", runner: Callable[..., Any] = subprocess.run,
                 timeout: int = TIMEOUT_SECONDS) -> None:
        self._model, self._runner, self._timeout = model, runner, timeout

    def ask(self, prompt: str, schema: dict[str, Any], *, instruction: str = "Follow the input.") -> dict[str, Any]:
        command = build_command(instruction, schema, model=self._model)
        completed = self._runner(
            command, input=prompt, capture_output=True, text=True,
            timeout=self._timeout, encoding="utf-8",
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        lowered = f"{stdout}\n{stderr}".lower()
        if any(marker in lowered for marker in _STOP_MARKERS):
            raise BrainStopped(stdout or stderr or "Claude refused the call")

        if completed.returncode != 0 and not stdout:
            raise BrainError(f"claude exited {completed.returncode}: {stderr or 'no output'}")

        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BrainError(f"could not read the reply as JSON: {stdout[:400]}") from exc

        structured = envelope.get("structured_output")
        if not isinstance(structured, dict):
            raise BrainError(
                "the reply had no structured output; Claude said: "
                f"{str(envelope.get('result'))[:400]}"
            )
        return structured
```

- [ ] **Step 4: Run to verify pass**

- [ ] **Step 5: One real call, to prove the flags work**

Run:
```bash
./.venv/Scripts/python.exe -c "
from research.brain import Claude
schema = {'type':'object','additionalProperties':False,'required':['answer'],'properties':{'answer':{'type':'string'}}}
print(Claude().ask('Reply with the word pong and nothing else.', schema))
"
```
Expected: a dict like `{'answer': 'pong'}` within a few seconds.

If it fails with a usage limit, STOP and report — that is the owner's Pro allowance, and it resets on its own.

- [ ] **Step 6: Commit**

```bash
git add research/brain.py tests/test_research_brain.py
git commit -m "feat(research): ask Opus with every tool removed

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The loop

**Files:**
- Create: `research/loop.py`
- Test: `tests/test_research_loop.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_loop.py`:
```python
"""The version loop: limits, repairs, decisions and the time budget."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.checker import CheckError  # noqa: E402
from research.loop import MAX_VERSIONS, REPAIR_ATTEMPTS, run_versions  # noqa: E402

GOOD_YAML = "a strategy"


class FakeBrain:
    def __init__(self, proposals, reviews):
        self.proposals, self.reviews = list(proposals), list(reviews)
        self.asked = []

    def propose(self, **kwargs):
        self.asked.append(("propose", kwargs))
        return self.proposals.pop(0)

    def review(self, **kwargs):
        self.asked.append(("review", kwargs))
        return self.reviews.pop(0)


def proposal(title="dip", yaml_text=GOOD_YAML, change=""):
    return {"title": title, "description": "d", "hypothesis": "h",
            "strategy_yaml": yaml_text, "change_note": change}


def review(decision="stop", hint=""):
    return {"why_failed": "f", "why_worked": "w", "lessons": "l",
            "decision": decision, "change_hint": hint}


def fake_check(strategy_yaml, **kwargs):
    if strategy_yaml == "broken":
        raise CheckError("entry: unknown indicator 'foo'")
    return f"checked:{strategy_yaml}"


def fake_test(checked, version_no):
    return f"summary-for-{checked}-v{version_no}"


def run(brain, **kwargs):
    return run_versions(
        brain=brain, check=fake_check, test=fake_test, day=date(2026, 9, 12), **kwargs
    )


def test_one_version_then_stop():
    brain = FakeBrain([proposal()], [review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 1
    assert outcome.versions[0].valid and outcome.stopped_because == "stop"


def test_next_version_keeps_the_idea_and_passes_the_hint():
    brain = FakeBrain([proposal(), proposal(change="traded less")],
                      [review("next_version", hint="trade less"), review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 2
    assert outcome.versions[1].idea_no == 1
    assert brain.asked[2][1]["change_hint"] == "trade less"


def test_a_new_idea_starts_a_fresh_idea_number():
    brain = FakeBrain([proposal("a"), proposal("b")],
                      [review("new_idea"), review("stop")])
    outcome = run(brain)
    assert [v.idea_no for v in outcome.versions] == [1, 2]
    assert outcome.versions[1].version_no == 1


def test_an_invalid_proposal_is_repaired_without_costing_a_version():
    brain = FakeBrain([proposal(yaml_text="broken"), proposal()], [review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 1 and outcome.versions[0].valid
    assert outcome.repairs == 1


def test_a_proposal_that_stays_broken_becomes_a_failed_version():
    brain = FakeBrain([proposal(yaml_text="broken")] * (REPAIR_ATTEMPTS + 1),
                      [review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 1
    assert outcome.versions[0].valid is False
    assert "foo" in outcome.versions[0].error


def test_seven_versions_is_the_ceiling():
    brain = FakeBrain([proposal()] * MAX_VERSIONS, [review("next_version")] * MAX_VERSIONS)
    outcome = run(brain)
    assert len(outcome.versions) == MAX_VERSIONS
    assert outcome.stopped_because == "version_limit"


def test_the_last_review_is_told_no_versions_remain():
    brain = FakeBrain([proposal()] * MAX_VERSIONS, [review("next_version")] * MAX_VERSIONS)
    run(brain)
    last_review = [c for c in brain.asked if c[0] == "review"][-1]
    assert last_review[1]["versions_left"] == 0


def test_the_time_budget_stops_before_starting_another_version():
    clock = iter([0.0, 100.0, 4000.0, 4000.0, 4000.0])
    brain = FakeBrain([proposal()] * 3, [review("next_version")] * 3)
    outcome = run(brain, budget_seconds=3600, now=lambda: next(clock))
    assert outcome.stopped_because == "time_budget"
    assert len(outcome.versions) == 1


def test_a_max_versions_override_is_respected_for_cheap_live_tests():
    brain = FakeBrain([proposal()] * 2, [review("next_version")] * 2)
    outcome = run(brain, max_versions=2)
    assert len(outcome.versions) == 2 and outcome.stopped_because == "version_limit"
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Create `research/loop.py`:
```python
"""The version loop: propose, check, test, review - until something stops it.

Pure by injection: the brain, the checker and the tester are parameters, so
every limit and decision here is tested with fakes and no network, no Claude
and no candles.

Stopping is a first-class result, not an exception. A day that ran out of
versions, time or allowance still has versions worth keeping, and the caller
evaluates the locked year regardless (design 8).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

MAX_VERSIONS = 7
REPAIR_ATTEMPTS = 3
DEFAULT_BUDGET_SECONDS = 5 * 60 * 60


@dataclass
class VersionAttempt:
    idea_no: int
    version_no: int
    title: str
    description: str
    hypothesis: str
    change_note: str
    valid: bool
    error: str | None = None
    checked: Any = None
    summary: Any = None
    review: dict[str, Any] = field(default_factory=dict)


@dataclass
class LoopOutcome:
    versions: list[VersionAttempt]
    stopped_because: str
    repairs: int = 0
    ideas_dropped: int = 0


def run_versions(
    *,
    brain: Any,
    check: Callable[..., Any],
    test: Callable[[Any, int], Any],
    day: date,
    notes: Sequence[dict[str, Any]] = (),
    ideas_tried: Sequence[str] = (),
    max_versions: int = MAX_VERSIONS,
    budget_seconds: float = DEFAULT_BUDGET_SECONDS,
    now: Callable[[], float] = time.monotonic,
) -> LoopOutcome:
    started = now()
    versions: list[VersionAttempt] = []
    repairs = 0
    ideas_dropped = 0
    idea_no = 1
    version_no = 1
    previous_summary = None
    change_hint: str | None = None
    slowest = 0.0

    while True:
        if len(versions) >= max_versions:
            return LoopOutcome(versions, "version_limit", repairs, ideas_dropped)
        elapsed = now() - started
        if versions and elapsed + slowest > budget_seconds:
            return LoopOutcome(versions, "time_budget", repairs, ideas_dropped)

        version_started = now()
        error: str | None = None
        checked = None
        proposal: dict[str, Any] = {}

        for attempt in range(REPAIR_ATTEMPTS + 1):
            proposal = brain.propose(
                notes=notes, ideas_tried=ideas_tried, previous=previous_summary,
                change_hint=change_hint, error=error,
            )
            try:
                checked = check(
                    proposal.get("strategy_yaml", ""),
                    idea=proposal.get("title", "idea"), day=day, version=version_no,
                )
                error = None
                break
            except Exception as exc:        # noqa: BLE001 - sent back to Opus
                error = str(exc)
                checked = None
                if attempt < REPAIR_ATTEMPTS:
                    repairs += 1

        attempt_row = VersionAttempt(
            idea_no=idea_no, version_no=version_no,
            title=proposal.get("title", ""), description=proposal.get("description", ""),
            hypothesis=proposal.get("hypothesis", ""), change_note=proposal.get("change_note", ""),
            valid=checked is not None, error=error, checked=checked,
        )

        if checked is None:
            versions.append(attempt_row)
            version_no += 1
            previous_summary = None
            change_hint = None
            continue

        attempt_row.summary = test(checked, version_no)
        versions.append(attempt_row)
        slowest = max(slowest, now() - version_started)

        versions_left = max_versions - len(versions)
        review = brain.review(
            summary=attempt_row.summary, version=len(versions), versions_left=versions_left,
        )
        attempt_row.review = review
        decision = review.get("decision", "stop")

        if decision == "stop" or versions_left <= 0:
            because = "stop" if decision == "stop" else "version_limit"
            return LoopOutcome(versions, because, repairs, ideas_dropped)
        if decision == "new_idea":
            ideas_dropped += 1
            idea_no += 1
            version_no = 1
            previous_summary = None
            change_hint = None
        else:
            version_no += 1
            previous_summary = attempt_row.summary
            change_hint = review.get("change_hint") or None
```

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/loop.py tests/test_research_loop.py
git commit -m "feat(research): the version loop, with limits and stop reasons

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The journal

**Files:**
- Create: `research/journal.py`
- Test: `tests/test_research_journal.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_journal.py`:
```python
"""The daily note a later run reads - training lessons only."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.journal import journal_markdown, note_rows  # noqa: E402

LOCKED_WORDS = ("locked", "lakh", "verdict", "just holding")


def entries():
    return [
        {"idea_title": "dip buyer", "outcome_training": "137 of 1177 profitable",
         "lessons": "shallow dips beat deep ones"},
        {"idea_title": "gap fade", "outcome_training": "lost money before fees",
         "lessons": "gaps close less often than expected"},
    ]


def test_the_note_lists_every_idea_with_its_lesson():
    text = journal_markdown(date(2026, 9, 12), entries())
    assert "dip buyer" in text and "gap fade" in text
    assert "shallow dips beat deep ones" in text


def test_the_note_is_dated_so_a_later_run_can_order_them():
    assert "2026-09-12" in journal_markdown(date(2026, 9, 12), entries())


def test_the_note_never_carries_the_locked_year():
    text = journal_markdown(date(2026, 9, 12), entries()).lower()
    assert not any(word in text for word in LOCKED_WORDS)


def test_note_rows_are_tagged_with_the_run_and_day():
    rows = note_rows("run-1", date(2026, 9, 12), entries())
    assert all(r["run_id"] == "run-1" and r["day"] == date(2026, 9, 12) for r in rows)
    assert [r["idea_title"] for r in rows] == ["dip buyer", "gap fade"]


def test_a_day_with_no_entries_still_writes_a_note():
    text = journal_markdown(date(2026, 9, 12), [])
    assert "2026-09-12" in text and "no" in text.lower()
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Create `research/journal.py`:
```python
"""The daily note, and the rows a later run reads.

Written from the TRAINING review only. Locked-year numbers live in
research_runs, the dashboard and the messages - never here, because this is
one of the few things a later day's prompt builder reads (design 2.4).

The file is also why the GitHub schedule stays alive: a public repository's
scheduled workflow is disabled after 60 days without repository activity, and
committing this note every morning is that activity.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

JOURNAL_DIR = Path("research/journal")


def journal_markdown(day: date, entries: Sequence[dict[str, Any]]) -> str:
    lines = [f"# Research notes — {day.isoformat()}", ""]
    if not entries:
        lines.append("No idea reached a tested version today.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        lines.append(f"## {entry.get('idea_title') or 'untitled idea'}")
        lines.append("")
        lines.append(f"**Training outcome:** {entry.get('outcome_training') or 'not recorded'}")
        lines.append("")
        lines.append(f"**Lessons:** {entry.get('lessons') or 'none recorded'}")
        lines.append("")
    return "\n".join(lines)


def write_journal(day: date, entries: Sequence[dict[str, Any]], *, root: Path | None = None) -> Path:
    directory = (root or Path(".")) / JOURNAL_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{day.isoformat()}.md"
    path.write_text(journal_markdown(day, entries), encoding="utf-8")
    return path


def note_rows(run_id: str, day: date, entries: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "run_id": run_id,
            "day": day,
            "idea_title": entry.get("idea_title"),
            "outcome_training": entry.get("outcome_training"),
            "lessons": entry.get("lessons"),
        }
        for entry in entries
    ]
```

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/journal.py tests/test_research_journal.py
git commit -m "feat(research): the daily note a later run reads

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Store the versions and the strategies

**Files:**
- Modify: `research/store.py`
- Test: `tests/test_research_store.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_store.py`:
```python
from research.store import save_notes, save_versions  # noqa: E402


def test_versions_are_written_with_their_run_and_review():
    captured: dict[str, list] = {}
    save_versions(FakeClient(captured=captured), "run-1", [
        {"idea_no": 1, "version_no": 1, "valid": True, "why_failed": "f"},
        {"idea_no": 1, "version_no": 2, "valid": False, "error": "bad rule"},
    ])
    rows = captured["research_versions"]
    assert [r["run_id"] for r in rows] == ["run-1", "run-1"]
    assert rows[1]["error"] == "bad rule"


def test_notes_are_written_with_their_day_as_text():
    captured: dict[str, list] = {}
    save_notes(FakeClient(captured=captured), [
        {"run_id": "run-1", "day": date(2026, 9, 12), "idea_title": "dip", "lessons": "l"},
    ])
    assert captured["research_notes"][0]["day"] == "2026-09-12"


def test_writing_no_versions_touches_nothing():
    client = FakeClient()
    save_versions(client, "run-1", [])
    assert client.log == []
```

- [ ] **Step 2: Run to verify failure**

- [ ] **Step 3: Implement**

Append to `research/store.py`:
```python
def save_versions(
    client: Any, run_id: str, versions: Sequence[Mapping[str, Any]]
) -> int:
    """Every version a run tried, valid or not (design 2.7: summaries only)."""
    rows = [{**dict(v), "run_id": run_id} for v in versions]
    if not rows:
        return 0
    for start in range(0, len(rows), CHUNK):
        _insert(client, "research_versions", rows[start:start + CHUNK], run_id)
    return len(rows)


def save_notes(client: Any, rows: Sequence[Mapping[str, Any]]) -> int:
    """The journal rows a later day's prompt builder reads."""
    if not rows:
        return 0
    _insert(client, "research_notes", list(rows), None)
    return len(rows)


def recent_notes(client: Any, *, days: int = 30, limit: int = 60) -> list[dict[str, Any]]:
    """The last `days` of notes, newest first. Training lessons only, by table."""
    try:
        resp = (
            client.table("research_notes").select("day,idea_title,outcome_training,lessons")
            .order("day", desc=True).limit(limit).execute()
        )
    except Exception:       # noqa: BLE001 - a missing table means no history yet
        return []
    return list(getattr(resp, "data", None) or [])
```

Also add a strategy-saving helper, since `db.save_strategy_document` does not write the research columns added in piece 2:
```python
def save_research_strategy(
    store: Any, document: Mapping[str, Any], *, title: str, description: str, hypothesis: str
) -> int | None:
    """Save a proposed strategy to the library, paused, with its story.

    Returns the strategy_versions id, so a run row can point at exactly the
    definition that was tested.
    """
    result = store.save_strategy_document(dict(document))
    try:
        store._table("strategies").update({
            "title": title, "description": description,
            "hypothesis": hypothesis, "origin": "research",
        }).eq("name", document["name"]).execute()
    except Exception as exc:        # noqa: BLE001 - the strategy is saved either way
        raise ResearchStoreError(
            f"saved strategy {document['name']} but could not write its research "
            f"columns: {exc}"
        ) from exc
    return getattr(result, "version_id", None)
```

- [ ] **Step 4: Run to verify pass, then the full suite**

- [ ] **Step 5: Commit**

```bash
git add research/store.py tests/test_research_store.py
git commit -m "feat(research): store versions, notes and research strategies

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: The day

**Files:**
- Create: `research/run_day.py`

- [ ] **Step 1: Write it**

Create `research/run_day.py` wiring the pieces in the order of design §4. It must:

1. Parse `--max-versions` (default 7), `--max-stocks`, `--stock-timeframes`, `--workers`, `--budget-seconds`, `--no-save`, `--dry-run` (skip every AI call and use a fixed built-in strategy, for wiring tests).
2. Connect Supabase, build the `FrozenPriceReader`, place `DATA_END` and the windows exactly as `research/evaluate.py` does — import those steps from a shared helper rather than copying them. Extract `research/evaluate.py`'s setup into `prepare_run(settings, store, stocks, timeframes)` returning `(reader, windows, data_end, symbol_ends)` and have BOTH commands call it.
3. Load `recent_notes` and build `ideas_tried` from `research_versions` titles.
4. Build a `Brain` adapter with `.propose(...)` and `.review(...)` that calls `Claude.ask` with `PROPOSE_SCHEMA` / `REVIEW_SCHEMA` and the prompts from Task 4, passing `error=` back as an extra line when repairing.
5. Run `run_versions(...)`, where `test(checked, version_no)` runs the sweep on the training windows and returns `build_summary(...)`.
6. Choose the final version: the last valid version whose review said `stop`, else the valid version with the best training score by `picker.pick_best` on its own results.
7. Save every version to the library with its title and description (paused), and record `research_versions` rows.
8. Run the pick and the locked year ONCE, after the last AI call, reusing the evaluate command's code path.
9. Write and commit the journal note, save `research_notes`.
10. Print a report in the same shape as `research/evaluate.py`, ending with `saved as run <id>`.

Catch `BrainStopped` around the loop: keep the versions already finished, set the run status to `stopped_limit`, and continue to the pick and locked year (design §8).

- [ ] **Step 2: Dry run, no AI**

Run: `./.venv/Scripts/python.exe -m research.run_day --dry-run --max-stocks 5 --stock-timeframes day --workers 2`
Expected: a full report and `saved as run <id>`, with one version whose title is the built-in placeholder. No Claude call is made.

- [ ] **Step 3: One real version**

Run: `./.venv/Scripts/python.exe -m research.run_day --max-versions 1 --max-stocks 5 --stock-timeframes day --workers 2`
Expected: one propose call, one sweep, one review call, then the pick and locked year, then `saved as run <id>`. Read the printed proposal and review: they should be about the strategy, not about the system.

- [ ] **Step 4: Check the stored row**

With the Supabase MCP:
```sql
select r.status, r.versions_tried, r.final_strategy_name, r.pick_symbol,
       (select count(*) from research_versions v where v.run_id = r.id) as versions,
       (select count(*) from research_notes n where n.run_id = r.id) as notes
from research_runs r order by r.started_at desc limit 1;
```
Expected: `versions` matches `versions_tried`, and `notes` is at least 1.

- [ ] **Step 5: Full suite and commit**

```bash
git add research/run_day.py research/evaluate.py
git commit -m "feat(research): run a whole research day

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: A real day, and finish

- [ ] **Step 1: Run a full day**

Run: `./.venv/Scripts/python.exe -m research.run_day --max-versions 3`
Expected: about 15 minutes per version plus the AI calls. Read the report.

If the Pro allowance runs out mid-run, the day should stop cleanly with status `stopped_limit`, still evaluate the locked year, and still store a row. That is the design working, not a failure — report it as such.

- [ ] **Step 2: Look at it in the dashboard**

Boot the dashboard and open Research. The new run should be a row, its detail showing the version timeline.

- [ ] **Step 3: Record it in the spec**

Mark piece 3 built in §10 with the measured version count, wall-clock time and what the first real ideas were.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md
git commit -m "docs(spec): piece 3 built

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
