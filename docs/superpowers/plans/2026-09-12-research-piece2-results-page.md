# Research Loop — Piece 2: Results Page — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every research run is stored, and appears in the dashboard as a row you can click to see the full detail.

**Architecture:** A migration adds the `research_*` tables from design §6. Pure builders turn piece 1's in-memory results into row dicts; a thin store writes them; the evaluate command calls both. A new Streamlit page reads the rows, renders the grid from design §7.1, and shows the detail from §7.2 for the selected row. All page logic that can be tested lives in pure functions, matching `tests/test_dashboard.py`.

**Tech Stack:** Python 3.11, Supabase (Postgres + PostgREST), pandas 2.2.3, Streamlit 1.41.1, pytest 8.3.4.

**Spec:** `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` §5.5, §6, §7.1, §7.2 — build piece 2 of 4 (§10).

---

## Context for the implementer

- Piece 1 is merged into `main`. `python -m research.evaluate --strategy NAME` sweeps 1,213 combinations (929 s on 8 workers), picks one, and PRINTS the locked-year result. Nothing is stored yet. That command is the only producer of results in piece 2 — the AI loop is piece 3.
- The Supabase MCP connector is authorised in this session, so migrations can be applied directly with `mcp__supabase__apply_migration`. The matching file under `sql/` is the human-readable record and must still be written.
- Existing tables use: `rls enabled`, an `anon` SELECT policy, and a table comment. Follow `sql/004_backtest_runs.sql` for style.
- Dashboard pages take `AppContext` (`app_common.py`) and are registered in `dashboard.py`. Reads go through `app_common.fetch_table` / `fetch_optional_table` (cached 45 s, anon-safe). Writes go through `SupabaseStore` and only happen in edit mode.
- `db.SupabaseStore._insert_dropping_unknown_columns(table, payload, doing)` inserts and survives a database missing newer columns; `insert_backtest_equity` shows the chunked pattern (500 rows) and the missing-table fallback.
- Measured facts this piece must not contradict: DATA_END is 2026-07-31, the locked year is 2025-08-01 → 2026-07-31, and a full run tests 1,213 combinations of which ~1,177 are testable.
- Windows: run Python as `./.venv/Scripts/python.exe` from the repo root, Bash tool with Git Bash syntax. Work on branch `feat/research-piece2`.

## File structure

| File | Status | Responsibility |
|---|---|---|
| `sql/011_research.sql` | create | the migration, as applied |
| `research/records.py` | create | pure builders: piece-1 objects → row dicts |
| `research/store.py` | create | write one run and its child rows |
| `research/lakh.py` | modify | `equity_series`: daily ₹1 lakh vs just-holding balances |
| `research/evaluate.py` | modify | build records, save them, `--no-save` |
| `app_pages/research_page.py` | create | grid (§7.1) and detail (§7.2) |
| `dashboard.py` | modify | register the page |
| `tests/test_research_records.py` | create | row shapes, verdict columns |
| `tests/test_research_store.py` | create | writes, chunking, missing table |
| `tests/test_research_lakh.py` | modify | equity series |
| `tests/test_research_page.py` | create | grid frame, formatting |

---

### Task 1: The migration

**Files:**
- Create: `sql/011_research.sql`

- [ ] **Step 1: Write `sql/011_research.sql`**
```sql
-- ---------------------------------------------------------------------------
-- Piece 2: the research loop's own tables (design §6).
--
-- One run per day becomes one grid row; its children hold what that row opens
-- into. Additive only: nothing existing changes shape, and every table is
-- anon-readable so the hosted dashboard (view-only key) can render it.
--
-- Size, measured from a real run: ~1,213 combo rows, <= 60 locked trades and
-- <= 500 equity points per run — about 0.4 MB a day.
-- ---------------------------------------------------------------------------

-- --- 6.1 strategies gains research provenance ------------------------------
alter table strategies add column if not exists title       text;
alter table strategies add column if not exists description text;
alter table strategies add column if not exists hypothesis  text;
alter table strategies add column if not exists origin      text
    not null default 'manual' check (origin in ('manual', 'research'));

comment on column strategies.origin is
    'manual = written by a person; research = produced by the daily loop.';

-- The stored timeframe is now the PICK''s timeframe, which may be any
-- supported one (5m and 25m were missing from the original check). The old
-- constraint is dropped by DEFINITION rather than by name: sql/001 did not
-- name it explicitly, so the name is whatever Postgres generated.
do $$
declare existing text;
begin
    select conname into existing
      from pg_constraint
     where conrelid = 'public.strategies'::regclass
       and contype = 'c'
       and pg_get_constraintdef(oid) ilike '%timeframe%'
     limit 1;
    if existing is not null then
        execute format('alter table strategies drop constraint %I', existing);
    end if;
end $$;

alter table strategies add constraint strategies_timeframe_check
    check (timeframe in ('1m', '5m', '15m', '25m', '30m', '60m', 'day'));

-- --- 6.2 one row per run: the grid -----------------------------------------
create table if not exists research_runs (
    id                  uuid primary key default gen_random_uuid(),
    trigger             text        not null default 'manual',
    started_at          timestamptz not null,
    finished_at         timestamptz,
    status              text        not null
        check (status in ('completed', 'stopped_limit', 'stopped_time', 'failed')),
    failed_step         text,

    -- The frozen window this run measured. Stored per run because it moves
    -- when prices are topped up, and an old row must still say what it meant.
    data_end            date        not null,
    locked_from         date        not null,
    locked_to           date        not null,

    final_strategy_name text,
    final_version_id    bigint      references strategy_versions(id),
    pick_symbol         text,
    pick_timeframe      text,

    locked_trades       integer,
    trades_per_month    numeric(10,2),
    win_rate_pct        numeric(6,2),
    lakh_end_value      numeric(14,2),
    hold_end_value      numeric(14,2),
    worst_dip_pct       numeric(6,2),
    verdict_passed      boolean,
    beat_holding        boolean,

    combos_profitable   integer,
    combos_tested       integer,
    versions_tried      integer     not null default 1,
    ideas_dropped       integer     not null default 0,

    ai_review           text,
    warnings            jsonb       not null default '[]'::jsonb,
    notify_status       jsonb       not null default '{}'::jsonb,
    created_at          timestamptz not null default now()
);

create index if not exists research_runs_started_idx on research_runs (started_at desc);

comment on table research_runs is
    'One research run: the grid row. Children hold what it opens into.';
comment on column research_runs.lakh_end_value is
    'What Rs 1,00,000 became over the locked year, trading the pick. Fees are '
    'recomputed on the running balance, so a doubled balance pays doubled fees.';
comment on column research_runs.hold_end_value is
    'What the same Rs 1,00,000 became simply holding the pick symbol, delivery '
    'fees once. The honest comparison for any long strategy.';

-- --- one row per version attempt (written by piece 3) ----------------------
create table if not exists research_versions (
    id                   bigserial   primary key,
    run_id               uuid        not null references research_runs(id) on delete cascade,
    idea_no              integer     not null,
    version_no           integer     not null,
    strategy_name        text,
    strategy_version_id  bigint      references strategy_versions(id),
    valid                boolean     not null,
    error                text,
    change_note          text,
    why_failed           text,
    why_worked           text,
    lessons              text,
    decision             text,
    training_summary     jsonb,
    created_at           timestamptz not null default now(),
    constraint research_versions_seq_key unique (run_id, idea_no, version_no)
);

create index if not exists research_versions_run_idx on research_versions (run_id);

comment on table research_versions is
    'Every version a run tried, valid or not, with the review that followed it.';

-- --- the final version''s combinations --------------------------------------
create table if not exists research_combo_results (
    id             bigserial primary key,
    run_id         uuid      not null references research_runs(id) on delete cascade,
    symbol         text      not null,
    timeframe      text      not null,
    trades         integer   not null default 0,
    win_rate_pct   numeric(6,2),
    net_pnl        numeric(14,2),
    cagr_pct       numeric(10,4),
    worst_dip_pct  numeric(6,2),
    skipped_reason text
);

create index if not exists research_combo_run_idx on research_combo_results (run_id);

comment on table research_combo_results is
    'Every stock x timeframe the final version was tested on, TRAINING window. '
    'A skipped_reason means it was never scored - not that it scored zero.';

-- --- the pick''s locked-year trades ------------------------------------------
create table if not exists research_locked_trades (
    id              bigserial   primary key,
    run_id          uuid        not null references research_runs(id) on delete cascade,
    entry_at        timestamptz not null,
    exit_at         timestamptz not null,
    side            text        not null,
    entry_price     numeric(14,4) not null,
    exit_price      numeric(14,4) not null,
    fees            numeric(14,4) not null,
    net_return_pct  numeric(10,4) not null,
    balance_after   numeric(14,2) not null
);

create index if not exists research_locked_trades_run_idx on research_locked_trades (run_id, entry_at);

comment on table research_locked_trades is
    'The pick''s trades inside the locked year, with the Rs 1 lakh balance after each.';

-- --- daily balances for the chart -------------------------------------------
create table if not exists research_locked_equity (
    id            bigserial primary key,
    run_id        uuid      not null references research_runs(id) on delete cascade,
    day           date      not null,
    lakh_balance  numeric(14,2) not null,
    hold_balance  numeric(14,2),
    constraint research_locked_equity_day_key unique (run_id, day)
);

create index if not exists research_locked_equity_run_idx on research_locked_equity (run_id, day);

comment on table research_locked_equity is
    'Daily Rs 1 lakh balance beside just-holding, so the chart needs no trade replay.';

-- --- what the AI reads on later days (written by piece 3) -------------------
create table if not exists research_notes (
    id                bigserial primary key,
    run_id            uuid      not null references research_runs(id) on delete cascade,
    day               date      not null,
    idea_title        text,
    outcome_training  text,
    lessons           text,
    created_at        timestamptz not null default now()
);

create index if not exists research_notes_day_idx on research_notes (day desc);

comment on table research_notes is
    'The running journal a later run reads, so each day starts from what is known.';

-- --- RLS: anon may read, matching every other table -------------------------
alter table research_runs           enable row level security;
alter table research_versions       enable row level security;
alter table research_combo_results  enable row level security;
alter table research_locked_trades  enable row level security;
alter table research_locked_equity  enable row level security;
alter table research_notes          enable row level security;

drop policy if exists "anon read research_runs"          on research_runs;
drop policy if exists "anon read research_versions"      on research_versions;
drop policy if exists "anon read research_combo_results" on research_combo_results;
drop policy if exists "anon read research_locked_trades" on research_locked_trades;
drop policy if exists "anon read research_locked_equity" on research_locked_equity;
drop policy if exists "anon read research_notes"         on research_notes;

create policy "anon read research_runs"          on research_runs          for select to anon using (true);
create policy "anon read research_versions"      on research_versions      for select to anon using (true);
create policy "anon read research_combo_results" on research_combo_results for select to anon using (true);
create policy "anon read research_locked_trades" on research_locked_trades for select to anon using (true);
create policy "anon read research_locked_equity" on research_locked_equity for select to anon using (true);
create policy "anon read research_notes"         on research_notes         for select to anon using (true);
```

- [ ] **Step 2: Apply it**

Apply the same SQL with `mcp__supabase__apply_migration`, name `research_tables`.

- [ ] **Step 3: Verify**

`mcp__supabase__list_tables` must now show the six `research_*` tables. Then check the widened constraint with `execute_sql`:
```sql
insert into strategies (name, enabled, position_type, timeframe, definition, status)
values ('__tf_check__', false, 'long', '5m', '{}'::jsonb, 'draft');
delete from strategies where name = '__tf_check__';
```
Both statements must succeed. If the insert fails on the check constraint, the `do $$` block matched nothing — check `pg_constraint` by hand and fix the migration before continuing.

- [ ] **Step 4: Commit**

```bash
git add sql/011_research.sql
git commit -m "feat(db): tables for the research loop

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Daily balances for the chart

**Files:**
- Modify: `research/lakh.py`
- Test: `tests/test_research_lakh.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_research_lakh.py`:
```python
from research.lakh import equity_series  # noqa: E402


def test_equity_series_holds_the_balance_between_trades():
    days = pd.DatetimeIndex([
        datetime(2025, 1, d, 0, 0, tzinfo=IST).astimezone(UTC) for d in (2, 3, 6, 7)
    ])
    got = equity_series([same_day(3, 10)], FREE, day_index=days)
    assert [row["day"] for row in got] == [date(2025, 1, 2), date(2025, 1, 3),
                                           date(2025, 1, 6), date(2025, 1, 7)]
    assert [row["lakh_balance"] for row in got] == [100_000.0, 110_000.0, 110_000.0, 110_000.0]


def test_equity_series_ends_where_compound_ends():
    days = pd.DatetimeIndex([
        datetime(2025, 1, d, 0, 0, tzinfo=IST).astimezone(UTC) for d in (2, 3, 6)
    ])
    trades = [same_day(2, 10), same_day(3, -20)]
    series = equity_series(trades, FREE, day_index=days)
    assert series[-1]["lakh_balance"] == pytest.approx(
        compound(trades, FREE, window_days=365).end_value
    )


def test_equity_series_adds_holding_when_closes_are_given():
    days = pd.DatetimeIndex([
        datetime(2025, 1, d, 0, 0, tzinfo=IST).astimezone(UTC) for d in (2, 3)
    ])
    closes = pd.Series([100.0, 120.0], index=days)
    got = equity_series([], FREE, day_index=days, closes=closes)
    assert got[0]["hold_balance"] == pytest.approx(100_000.0)
    assert got[-1]["hold_balance"] == pytest.approx(120_000.0)


def test_equity_series_without_days_is_empty():
    assert equity_series([same_day(2, 10)], FREE, day_index=pd.DatetimeIndex([])) == []
```

Add `from datetime import date` to that file's imports if absent.

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_lakh.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'equity_series'`.

- [ ] **Step 3: Implement**

Append to `research/lakh.py`:
```python
def equity_series(
    trades: Sequence[SimTrade],
    cost_model: HoldingCostModel,
    *,
    day_index: pd.DatetimeIndex,
    closes: pd.Series | None = None,
    start_value: float = START_VALUE,
) -> list[dict[str, Any]]:
    """Daily Rs 1 lakh balance, and just-holding beside it when closes are given.

    The balance changes only when a trade closes and is carried flat between
    trades, which is what the account would really show: money earns nothing
    while it waits. The final point equals `compound(...).end_value` by
    construction - the same walk, sampled daily.
    """
    if len(day_index) == 0:
        return []

    days = sorted({ts.astimezone(IST).date() for ts in day_index})
    balance_on: dict[Any, float] = {}
    balance = start_value
    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        balance = max(balance + balance * gross_return - fees, 0.0)
        balance_on[t.exit_fill_ts.astimezone(IST).date()] = balance

    hold_by_day: dict[Any, float] = {}
    if closes is not None and len(closes) >= 1:
        first_close = float(closes.iloc[0])
        last_close = float(closes.iloc[-1])
        units = start_value / first_close
        fees = cost_model.delivery.round_trip(first_close, last_close, units)
        for ts, close in closes.items():
            hold_by_day[ts.astimezone(IST).date()] = round(
                start_value + units * (float(close) - first_close) - fees, 2
            )

    out: list[dict[str, Any]] = []
    running = start_value
    for day in days:
        running = balance_on.get(day, running)
        row: dict[str, Any] = {"day": day, "lakh_balance": round(running, 2)}
        if hold_by_day:
            row["hold_balance"] = hold_by_day.get(day)
        out.append(row)
    return out
```

Add `from typing import Any` to the imports of `research/lakh.py`.

Note the deliberate asymmetry: just-holding subtracts its fees on every point, so the final point matches the headline figure, while the ₹1 lakh path pays each trade's fee as it happens.

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_lakh.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/lakh.py tests/test_research_lakh.py
git commit -m "feat(research): daily balances for the locked-year chart

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Row builders

**Files:**
- Create: `research/records.py`
- Test: `tests/test_research_records.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_records.py`:
```python
"""Turning a finished run into the rows the database stores."""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.lakh import LakhResult  # noqa: E402
from research.records import combo_rows, locked_trade_rows, run_row  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

UTC = timezone.utc
LOCKED = LakhResult(start_value=100_000.0, end_value=107_461.0, trades=6,
                    winning_trades=4, worst_dip_pct=7.1, cagr_pct=7.4)


def base_run(**over):
    kwargs = dict(
        started_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 12, 1, 20, tzinfo=UTC),
        status="completed",
        data_end=date(2026, 7, 31),
        locked_from=date(2025, 8, 1),
        strategy_name="N200-PULLBACK-DAY",
        pick_symbol="NSE:360ONE",
        pick_timeframe="day",
        locked=LOCKED,
        hold_end_value=107_622.0,
        combos_profitable=4,
        combos_tested=18,
        warnings=["prices frozen at 2026-07-31"],
    )
    kwargs.update(over)
    return run_row(**kwargs)


def test_run_row_carries_the_verdict_columns():
    row = base_run()
    assert row["lakh_end_value"] == 107_461.0
    assert row["hold_end_value"] == 107_622.0
    assert row["win_rate_pct"] == pytest.approx(66.67, abs=0.01)
    assert row["trades_per_month"] == pytest.approx(0.5, abs=0.01)
    assert row["locked_to"] == date(2026, 7, 31)


def test_verdict_fails_on_too_few_trades_even_when_profitable():
    """Six trades is under the ten the design requires."""
    assert base_run()["verdict_passed"] is False


def test_verdict_passes_when_all_three_conditions_hold():
    good = LakhResult(start_value=100_000.0, end_value=108_400.0, trades=38,
                      winning_trades=17, worst_dip_pct=6.1, cagr_pct=8.4)
    assert base_run(locked=good)["verdict_passed"] is True


def test_beat_holding_is_separate_from_the_verdict():
    row = base_run()
    assert row["beat_holding"] is False
    assert base_run(hold_end_value=100_000.0)["beat_holding"] is True


def test_a_run_with_no_qualifying_pick_still_makes_a_row():
    row = run_row(
        started_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 12, 1, 20, tzinfo=UTC),
        status="completed", data_end=date(2026, 7, 31), locked_from=date(2025, 8, 1),
        strategy_name="X", pick_symbol=None, pick_timeframe=None, locked=None,
        hold_end_value=None, combos_profitable=0, combos_tested=1177, warnings=[],
    )
    assert row["pick_symbol"] is None
    assert row["lakh_end_value"] is None
    assert row["verdict_passed"] is False
    assert row["combos_tested"] == 1177


def test_combo_rows_keep_skips_with_their_reason():
    results = [
        ComboResult("NSE:A", "day", False, (trade(entry=ist(2025, 1, 2), exit_=ist(2025, 1, 2, 14)),)),
        ComboResult("NSE:B", "60m", False, (), "no candles in window"),
    ]
    rows = combo_rows("run-1", results, FREE, window_days_for=lambda r: 730)
    assert rows[0]["symbol"] == "NSE:A" and rows[0]["skipped_reason"] is None
    assert rows[0]["trades"] == 1 and rows[0]["cagr_pct"] is not None
    assert rows[1]["skipped_reason"] == "no candles in window"
    assert rows[1]["trades"] == 0 and rows[1]["cagr_pct"] is None


def test_locked_trade_rows_carry_the_running_balance():
    trades = [
        trade(entry=ist(2025, 8, 4, 10), exit_=ist(2025, 8, 4, 14), entry_price=100.0, exit_price=110.0),
        trade(entry=ist(2025, 8, 5, 10), exit_=ist(2025, 8, 5, 14), entry_price=100.0, exit_price=90.0),
    ]
    rows = locked_trade_rows("run-1", trades, FREE)
    assert [r["side"] for r in rows] == ["long", "long"]
    assert rows[0]["balance_after"] == pytest.approx(110_000.0)
    assert rows[1]["balance_after"] == pytest.approx(99_000.0)
    assert rows[0]["net_return_pct"] == pytest.approx(10.0)
    assert rows[1]["net_return_pct"] == pytest.approx(-10.0)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_records.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'research.records'`.

- [ ] **Step 3: Implement**

Create `research/records.py`:
```python
"""Turn a finished run into the rows the database stores.

Pure: no client, no clock, no I/O. The evaluate command (and later the daily
loop) builds these and hands them to research.store, so what is written can be
tested without a database.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from backtest_types import SimTrade
from costs import HoldingCostModel
from research.lakh import LakhResult, compound, passed
from research.sweep import ComboResult
from research.windows import LOCKED_DAYS

MONTHS_PER_YEAR = 12


def run_row(
    *,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    data_end: date,
    locked_from: date,
    strategy_name: str | None,
    pick_symbol: str | None,
    pick_timeframe: str | None,
    locked: LakhResult | None,
    hold_end_value: float | None,
    combos_profitable: int,
    combos_tested: int,
    warnings: Sequence[str],
    trigger: str = "manual",
    final_version_id: int | None = None,
    versions_tried: int = 1,
    ideas_dropped: int = 0,
    ai_review: str | None = None,
) -> dict[str, Any]:
    """The grid row (design §5.5, §6.2).

    A run with no qualifying pick is a RESULT, not an error: every locked-year
    column is null and the verdict is False, so the grid can show it plainly.
    """
    win_rate = None
    trades_per_month = None
    if locked is not None and locked.trades:
        win_rate = round(100 * locked.winning_trades / locked.trades, 2)
    if locked is not None:
        trades_per_month = round(locked.trades / MONTHS_PER_YEAR, 2)

    return {
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "data_end": data_end,
        "locked_from": locked_from,
        "locked_to": data_end,
        "final_strategy_name": strategy_name,
        "final_version_id": final_version_id,
        "pick_symbol": pick_symbol,
        "pick_timeframe": pick_timeframe,
        "locked_trades": None if locked is None else locked.trades,
        "trades_per_month": trades_per_month,
        "win_rate_pct": win_rate,
        "lakh_end_value": None if locked is None else locked.end_value,
        "hold_end_value": hold_end_value,
        "worst_dip_pct": None if locked is None else locked.worst_dip_pct,
        "verdict_passed": False if locked is None else passed(locked),
        "beat_holding": (
            None if locked is None or hold_end_value is None
            else locked.end_value > hold_end_value
        ),
        "combos_profitable": combos_profitable,
        "combos_tested": combos_tested,
        "versions_tried": versions_tried,
        "ideas_dropped": ideas_dropped,
        "ai_review": ai_review,
        "warnings": list(warnings),
    }


def combo_rows(
    run_id: str,
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> list[dict[str, Any]]:
    """One row per combination the final version was tested on."""
    rows: list[dict[str, Any]] = []
    for r in results:
        scored = None
        if r.skipped_reason is None and r.trades:
            scored = compound(r.trades, cost_model, window_days=window_days_for(r))
        wins = sum(1 for t in r.trades if t.net_pnl > 0)
        rows.append({
            "run_id": run_id,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "trades": len(r.trades),
            "win_rate_pct": round(100 * wins / len(r.trades), 2) if r.trades else None,
            "net_pnl": round(r.net_pnl, 2) if r.trades else None,
            "cagr_pct": None if scored is None else scored.cagr_pct,
            "worst_dip_pct": None if scored is None else scored.worst_dip_pct,
            "skipped_reason": r.skipped_reason,
        })
    return rows


def locked_trade_rows(
    run_id: str, trades: Sequence[SimTrade], cost_model: HoldingCostModel,
    *, start_value: float = 100_000.0,
) -> list[dict[str, Any]]:
    """The pick's locked-year trades, each with the balance it left behind."""
    rows: list[dict[str, Any]] = []
    balance = start_value
    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        before = balance
        balance = max(balance + balance * gross_return - fees, 0.0)
        rows.append({
            "run_id": run_id,
            "entry_at": t.entry_fill_ts,
            "exit_at": t.exit_fill_ts,
            "side": t.position_type,
            "entry_price": round(t.entry_price, 4),
            "exit_price": round(t.exit_price, 4),
            "fees": round(fees, 4),
            "net_return_pct": round(100 * (balance - before) / before, 4) if before else 0.0,
            "balance_after": round(balance, 2),
        })
    return rows


def equity_rows(run_id: str, series: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Daily balances, tagged with the run they belong to."""
    return [{"run_id": run_id, **point} for point in series]
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_records.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/records.py tests/test_research_records.py
git commit -m "feat(research): build the rows a run is stored as

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: The store

**Files:**
- Create: `research/store.py`
- Test: `tests/test_research_store.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_store.py`:
```python
"""Writing one run and its children, without a database."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.store import ResearchStoreError, save_run  # noqa: E402


class FakeTable:
    def __init__(self, name, log, fail_on=None):
        self.name, self._log, self._fail_on = name, log, fail_on
        self._payload = None

    def insert(self, payload):
        self._payload = payload
        return self

    def execute(self):
        if self._fail_on == self.name:
            raise RuntimeError(f"insert into {self.name} failed")
        rows = self._payload if isinstance(self._payload, list) else [self._payload]
        self._log.append((self.name, len(rows)))
        return SimpleNamespace(data=[{**r, "id": "run-1"} for r in rows])


class FakeClient:
    def __init__(self, fail_on=None):
        self.log, self._fail_on = [], fail_on

    def table(self, name):
        return FakeTable(name, self.log, self._fail_on)


def payload(combos=3, trades=2, equity=4):
    return {
        "run": {"status": "completed", "started_at": None},
        "combos": [{"symbol": f"S{i}"} for i in range(combos)],
        "locked_trades": [{"side": "long"} for _ in range(trades)],
        "equity": [{"day": i} for i in range(equity)],
    }


def test_save_run_writes_every_table_and_returns_the_id():
    client = FakeClient()
    run_id = save_run(client, **payload())
    assert run_id == "run-1"
    assert dict(client.log) == {
        "research_runs": 1, "research_combo_results": 3,
        "research_locked_trades": 2, "research_locked_equity": 4,
    }


def test_children_are_tagged_with_the_new_run_id():
    captured = {}

    class CapturingTable(FakeTable):
        def insert(self, payload):
            captured.setdefault(self.name, payload)
            return super().insert(payload)

    class CapturingClient(FakeClient):
        def table(self, name):
            return CapturingTable(name, self.log)

    save_run(CapturingClient(), **payload())
    assert all(row["run_id"] == "run-1" for row in captured["research_combo_results"])


def test_children_are_written_in_chunks():
    client = FakeClient()
    save_run(client, **payload(combos=1100, trades=0, equity=0))
    combo_writes = [n for name, n in client.log if name == "research_combo_results"]
    assert len(combo_writes) == 3 and sum(combo_writes) == 1100


def test_a_failed_run_insert_raises_with_the_table_named():
    with pytest.raises(ResearchStoreError, match="research_runs"):
        save_run(FakeClient(fail_on="research_runs"), **payload())


def test_a_failed_child_insert_names_the_run_that_was_written():
    with pytest.raises(ResearchStoreError, match="run-1"):
        save_run(FakeClient(fail_on="research_combo_results"), **payload())


def test_empty_children_write_nothing():
    client = FakeClient()
    save_run(client, **payload(combos=0, trades=0, equity=0))
    assert dict(client.log) == {"research_runs": 1}
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_store.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'research.store'`.

- [ ] **Step 3: Implement**

Create `research/store.py`:
```python
"""Write one research run and its children.

The run row is written first so its id can tag the children. A child failure
therefore leaves a run row with nothing under it - which the grid shows as a
run whose detail is missing, rather than losing the run entirely. The error
names the run id so the rest can be re-attached by hand if it ever matters.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from db import to_native

CHUNK = 500

_CHILD_TABLES = (
    ("combos", "research_combo_results"),
    ("locked_trades", "research_locked_trades"),
    ("equity", "research_locked_equity"),
)


class ResearchStoreError(RuntimeError):
    """A research row could not be written. The message says which table."""


def _insert(client: Any, table: str, rows: Sequence[Mapping[str, Any]], run_id: str | None):
    payload = [to_native(dict(r)) for r in rows]
    try:
        return client.table(table).insert(payload).execute()
    except Exception as exc:        # noqa: BLE001 - re-raised with context
        where = f" (run {run_id})" if run_id else ""
        raise ResearchStoreError(f"could not write {table}{where}: {exc}") from exc


def save_run(
    client: Any,
    *,
    run: Mapping[str, Any],
    combos: Sequence[Mapping[str, Any]] = (),
    locked_trades: Sequence[Mapping[str, Any]] = (),
    equity: Sequence[Mapping[str, Any]] = (),
) -> str:
    """Store a run and everything under it. Returns the new run id."""
    response = _insert(client, "research_runs", [dict(run)], None)
    rows = getattr(response, "data", None) or []
    if not rows or "id" not in rows[0]:
        raise ResearchStoreError(
            "research_runs insert returned no id, so children cannot be attached"
        )
    run_id = str(rows[0]["id"])

    children = {"combos": combos, "locked_trades": locked_trades, "equity": equity}
    for key, table in _CHILD_TABLES:
        batch = [{**dict(row), "run_id": run_id} for row in children[key]]
        for start in range(0, len(batch), CHUNK):
            _insert(client, table, batch[start:start + CHUNK], run_id)
    return run_id
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_store.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/store.py tests/test_research_store.py
git commit -m "feat(research): store a run and its children

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Save from the evaluate command

**Files:**
- Modify: `research/evaluate.py`

- [ ] **Step 1: Add the flag**

In `main`, after the `--stock-timeframes` argument, add:
```python
    parser.add_argument("--no-save", action="store_true",
                        help="print the result without storing it")
```

- [ ] **Step 2: Record when the run started**

Immediately after `args = parser.parse_args(argv)`, add:
```python
    started_at = datetime.now(UTC)
```

- [ ] **Step 3: Import what saving needs**

Extend the import block inside `main`:
```python
    from research.lakh import compound, equity_series, just_holding, passed
    from research.records import combo_rows, equity_rows, locked_trade_rows, run_row
    from research.store import ResearchStoreError, save_run
```
(the first line replaces the existing `from research.lakh import ...`).

- [ ] **Step 4: Collect the warnings in one place**

Replace the WARNINGS block at the end of `main`:
```python
    print("\nWARNINGS")
    print(f"  prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)")
    print(f"  {counts.tested} combinations tried: some look good in training by luck alone")
    return 0
```
with:
```python
    warnings = [
        f"prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)",
        f"{counts.tested} combinations tried: some look good in training by luck alone",
    ]
    if pick_last is not None and pick_last < data_end:
        warnings.append(
            f"the pick's candles stop {pick_last}, before DATA_END {data_end}"
        )
    print("\nWARNINGS")
    for line in warnings:
        print(f"  {line}")

    if args.no_save:
        print("\nnot saved (--no-save)")
        return 0

    window_days_for = lambda r: windows.training_days(      # noqa: E731
        is_index=r.is_index, timeframe=r.timeframe,
        data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
    )
    day_candles = reader.candles(p.symbol, "day", windows.locked_from_utc, windows.end_utc)
    saved = save_run(
        store._client,
        run=run_row(
            started_at=started_at, finished_at=datetime.now(UTC), status="completed",
            data_end=data_end, locked_from=windows.locked_from,
            strategy_name=args.strategy, pick_symbol=p.symbol, pick_timeframe=p.timeframe,
            locked=locked, hold_end_value=hold,
            combos_profitable=counts.profitable, combos_tested=counts.tested,
            warnings=warnings,
        ),
        combos=combo_rows(_PENDING, results, cost_model, window_days_for=window_days_for),
        locked_trades=locked_trade_rows(_PENDING, locked_trades, cost_model),
        equity=equity_rows(_PENDING, equity_series(
            locked_trades, cost_model,
            day_index=day_candles.index, closes=day_candles["close"],
        )),
    )
    print(f"\nsaved as run {saved}")
    return 0
```

Add near the other module constants:
```python
# The children are built before the run row exists; save_run replaces this with
# the real id. Naming it beats a bare empty string in a row that must not ship.
_PENDING = "pending"
```

- [ ] **Step 5: Handle a run with no qualifying pick**

Replace:
```python
    if pick is None:
        print("\nNo qualifying pick (needs 30+ training trades and a worst dip within 30%). "
              "Locked year not opened.")
        return 0
```
with:
```python
    if pick is None:
        print("\nNo qualifying pick (needs 30+ training trades and a worst dip within 30%). "
              "Locked year not opened.")
        if args.no_save:
            return 0
        saved = save_run(store._client, run=run_row(
            started_at=started_at, finished_at=datetime.now(UTC), status="completed",
            data_end=data_end, locked_from=windows.locked_from,
            strategy_name=args.strategy, pick_symbol=None, pick_timeframe=None,
            locked=None, hold_end_value=None,
            combos_profitable=counts.profitable, combos_tested=counts.tested,
            warnings=[f"no qualifying pick among {counts.tested} combinations"],
        ))
        print(f"saved as run {saved}")
        return 0
```

- [ ] **Step 6: Fail loudly, but only after printing**

Wrap the two `save_run` calls so a storage failure never loses the printed result. Add just before `return 0` handling in both places — the simplest form is to catch at the call site:
```python
    try:
        saved = save_run(...)
    except ResearchStoreError as exc:
        print(f"\nWARNING: the result was NOT stored: {exc}", file=sys.stderr)
        return 1
    print(f"\nsaved as run {saved}")
    return 0
```
Apply that shape to both save points.

- [ ] **Step 7: Quick run, saved**

Run: `./.venv/Scripts/python.exe -m research.evaluate --strategy N200-PULLBACK-DAY --max-stocks 5 --stock-timeframes day --workers 2`
Expected: the same report as piece 1, ending with `saved as run <uuid>`.

Then confirm with the Supabase MCP `execute_sql`:
```sql
select pick_symbol, pick_timeframe, lakh_end_value, hold_end_value,
       verdict_passed, beat_holding, combos_tested
from research_runs order by started_at desc limit 1;
```
Expected: one row matching the printed report.

Also check the children:
```sql
select (select count(*) from research_combo_results) as combos,
       (select count(*) from research_locked_trades) as trades,
       (select count(*) from research_locked_equity) as equity_points;
```
Expected: 18 combos, the printed trade count, and one equity point per trading day of the locked year.

- [ ] **Step 8: Full suite and commit**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider` (expect 0 failed).

```bash
git add research/evaluate.py
git commit -m "feat(research): store every evaluation run

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The research page

**Files:**
- Create: `app_pages/research_page.py`
- Test: `tests/test_research_page.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_page.py`:
```python
"""The research page's pure helpers (no Streamlit runtime needed)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app_pages.research_page import grid_frame, verdict_label  # noqa: E402


def runs_frame(**over):
    row = {
        "id": "run-1", "started_at": "2026-09-12T01:00:00+00:00",
        "final_strategy_name": "N200-PULLBACK-DAY", "pick_symbol": "NSE:360ONE",
        "pick_timeframe": "day", "trades_per_month": 0.5, "win_rate_pct": 66.67,
        "lakh_end_value": 107461.0, "hold_end_value": 107622.0, "worst_dip_pct": 7.1,
        "verdict_passed": False, "beat_holding": False, "combos_profitable": 4,
        "combos_tested": 18, "versions_tried": 1, "status": "completed",
    }
    row.update(over)
    return pd.DataFrame([row])


def test_grid_has_the_columns_the_design_asks_for():
    got = grid_frame(runs_frame(), {})
    assert list(got.columns) == [
        "Date", "Strategy", "Description", "Trades/month", "Best stock/index",
        "Best timeframe", "Success ratio", "₹1 lakh → became", "Just holding → became",
        "Worst dip", "Verdict", "Beat holding", "Broad or lucky", "Versions", "Run status",
    ]


def test_grid_reads_the_description_from_the_strategy():
    got = grid_frame(runs_frame(), {"N200-PULLBACK-DAY": "Buys a shallow dip in an uptrend"})
    assert got["Description"].iloc[0] == "Buys a shallow dip in an uptrend"


def test_a_strategy_without_a_description_shows_a_dash():
    assert grid_frame(runs_frame(), {})["Description"].iloc[0] == "—"


def test_broad_or_lucky_shows_the_share_of_profitable_combinations():
    assert grid_frame(runs_frame(), {})["Broad or lucky"].iloc[0] == "4 of 18"


def test_money_is_formatted_in_rupees():
    got = grid_frame(runs_frame(), {})
    assert got["₹1 lakh → became"].iloc[0] == "₹1,07,461"


def test_a_run_with_no_pick_shows_dashes_not_zeros():
    got = grid_frame(runs_frame(
        pick_symbol=None, pick_timeframe=None, lakh_end_value=None,
        hold_end_value=None, worst_dip_pct=None, win_rate_pct=None,
        trades_per_month=None, beat_holding=None,
    ), {})
    assert got["₹1 lakh → became"].iloc[0] == "—"
    assert got["Best stock/index"].iloc[0] == "—"
    assert got["Success ratio"].iloc[0] == "—"


def test_verdict_label_is_readable():
    assert verdict_label(True) == "✅ Passed"
    assert verdict_label(False) == "❌ Failed"
    assert verdict_label(None) == "—"


def test_newest_run_comes_first():
    two = pd.concat([
        runs_frame(id="old", started_at="2026-09-10T01:00:00+00:00"),
        runs_frame(id="new", started_at="2026-09-12T01:00:00+00:00"),
    ], ignore_index=True)
    assert grid_frame(two, {})["Date"].iloc[0] == "12 Sep 2026"


def test_grid_of_no_runs_is_empty_but_shaped():
    got = grid_frame(pd.DataFrame(), {})
    assert got.empty and len(got.columns) == 15
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_page.py -q -p no:cacheprovider`
Expected: `ModuleNotFoundError: No module named 'app_pages.research_page'`.

- [ ] **Step 3: Implement**

Create `app_pages/research_page.py`:
```python
"""Research: what the daily loop found, one row per run.

The grid is design §7.1, the detail §7.2. Every row is a run that was measured
on training years only; the locked year beside it was opened once, at the end.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, fetch_optional_table, page_header, to_ist

GRID_COLUMNS = [
    "Date", "Strategy", "Description", "Trades/month", "Best stock/index",
    "Best timeframe", "Success ratio", "₹1 lakh → became", "Just holding → became",
    "Worst dip", "Verdict", "Beat holding", "Broad or lucky", "Versions", "Run status",
]


def _rupees(value: Any) -> str:
    """Indian grouping: ₹1,07,461 rather than ₹107,461."""
    if value is None or pd.isna(value):
        return "—"
    whole = f"{int(round(float(value))):d}"
    if len(whole) <= 3:
        return f"₹{whole}"
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return "₹" + ",".join(groups + [tail])


def _text(value: Any) -> str:
    return "—" if value is None or (isinstance(value, float) and pd.isna(value)) else str(value)


def _percent(value: Any, digits: int = 0) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):.{digits}f}%"


def verdict_label(passed: Any) -> str:
    if passed is None or pd.isna(passed):
        return "—"
    return "✅ Passed" if bool(passed) else "❌ Failed"


def grid_frame(runs: pd.DataFrame, descriptions: dict[str, str]) -> pd.DataFrame:
    """One display row per run, newest first (design §7.1)."""
    if runs.empty:
        return pd.DataFrame(columns=GRID_COLUMNS)

    r = runs.copy()
    r["_when"] = pd.to_datetime(r["started_at"], utc=True, errors="coerce")
    r = r.sort_values("_when", ascending=False)

    out = pd.DataFrame({
        "Date": to_ist(r["started_at"]).dt.strftime("%d %b %Y"),
        "Strategy": r["final_strategy_name"].map(_text),
        "Description": r["final_strategy_name"].map(lambda n: descriptions.get(n) or "—"),
        "Trades/month": r["trades_per_month"].map(lambda v: "—" if v is None or pd.isna(v) else f"{float(v):.1f}"),
        "Best stock/index": r["pick_symbol"].map(_text),
        "Best timeframe": r["pick_timeframe"].map(_text),
        "Success ratio": r["win_rate_pct"].map(lambda v: _percent(v)),
        "₹1 lakh → became": r["lakh_end_value"].map(_rupees),
        "Just holding → became": r["hold_end_value"].map(_rupees),
        "Worst dip": r["worst_dip_pct"].map(lambda v: _percent(v, 1)),
        "Verdict": r["verdict_passed"].map(verdict_label),
        "Beat holding": r["beat_holding"].map(
            lambda v: "—" if v is None or pd.isna(v) else ("yes" if v else "no")),
        "Broad or lucky": [
            f"{int(p)} of {int(t)}" if pd.notna(p) and pd.notna(t) else "—"
            for p, t in zip(r["combos_profitable"], r["combos_tested"])
        ],
        "Versions": r["versions_tried"].map(_text),
        "Run status": r["status"].map(_text),
    })
    return out.reset_index(drop=True)


@st.cache_data(ttl=45, show_spinner=False)
def _children(_client, table: str, run_id: str, order_by: str) -> pd.DataFrame:
    """One run's child rows, filtered in the DATABASE.

    Fetching whole child tables and filtering here would break quietly: a
    single run stores about 1,213 combination rows, so after a few runs a
    5,000-row page read would stop reaching the older ones.
    """
    try:
        resp = (
            _client.table(table).select("*")
            .eq("run_id", run_id).order(order_by).limit(5000).execute()
        )
    except Exception as exc:        # noqa: BLE001 - an absent table is not fatal
        message = str(exc).lower()
        if "does not exist" in message or "not find the table" in message:
            return pd.DataFrame()
        raise
    return pd.DataFrame(resp.data)


def _detail(ctx: AppContext, run: pd.Series) -> None:
    st.subheader(f"{_text(run.get('final_strategy_name'))} — {_text(run.get('pick_symbol'))} · {_text(run.get('pick_timeframe'))}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("₹1 lakh became", _rupees(run.get("lakh_end_value")))
    c2.metric("Just holding", _rupees(run.get("hold_end_value")))
    c3.metric("Worst dip", _percent(run.get("worst_dip_pct"), 1))
    c4.metric("Verdict", verdict_label(run.get("verdict_passed")))

    st.caption(
        f"Locked year {run.get('locked_from')} → {run.get('locked_to')}, opened once. "
        f"Chosen on training years only, from {run.get('combos_tested')} combinations."
    )

    run_id = str(run["id"])
    equity = _children(ctx.client, "research_locked_equity", run_id, "day")
    if not equity.empty:
        st.markdown("**Locked year: ₹1 lakh against just holding**")
        chart = (
            equity.sort_values("day").set_index("day")[["lakh_balance", "hold_balance"]]
            .astype(float)
        )
        st.line_chart(chart.rename(columns={
            "lakh_balance": "₹1 lakh strategy", "hold_balance": "Just holding"}))

    combos = _children(ctx.client, "research_combo_results", run_id, "symbol")
    if not combos.empty:
        st.markdown("**Every stock and timeframe tested (training years)**")
        tested = combos[combos["skipped_reason"].isna()].copy()
        tested["net_pnl"] = tested["net_pnl"].astype(float)
        st.dataframe(
            tested.sort_values("net_pnl", ascending=False)[
                ["symbol", "timeframe", "trades", "win_rate_pct", "net_pnl",
                 "cagr_pct", "worst_dip_pct"]
            ].rename(columns={
                "symbol": "Symbol", "timeframe": "Timeframe", "trades": "Trades",
                "win_rate_pct": "Win %", "net_pnl": "Net ₹",
                "cagr_pct": "Yearly %", "worst_dip_pct": "Worst dip %"}),
            use_container_width=True, hide_index=True, height=320,
        )
        skipped = combos[combos["skipped_reason"].notna()]
        if not skipped.empty:
            st.caption(f"{len(skipped)} combinations skipped — "
                       + ", ".join(sorted(skipped["skipped_reason"].unique())[:3]))

    trades = _children(ctx.client, "research_locked_trades", run_id, "entry_at")
    if not trades.empty:
        st.markdown("**Locked-year trades**")
        shown = trades.copy()
        shown["Entry (IST)"] = to_ist(shown["entry_at"])
        shown["Exit (IST)"] = to_ist(shown["exit_at"])
        st.dataframe(
            shown[["Entry (IST)", "Exit (IST)", "side", "entry_price", "exit_price",
                   "fees", "net_return_pct", "balance_after"]].rename(columns={
                "side": "Side", "entry_price": "Entry", "exit_price": "Exit",
                "fees": "Fees ₹", "net_return_pct": "Return %",
                "balance_after": "Balance ₹"}),
            use_container_width=True, hide_index=True, height=260,
        )

    warnings = run.get("warnings")
    if isinstance(warnings, list) and warnings:
        st.markdown("**Warnings**")
        for line in warnings:
            st.caption(f"⚠️ {line}")

    review = run.get("ai_review")
    if review:
        st.markdown("**Review**")
        st.write(review)


def render(ctx: AppContext) -> None:
    page_header(
        "🔬 Research",
        "One row per research run. Each was built on training years only; the "
        "locked final year was opened once, at the end.",
        ctx,
    )

    runs = fetch_optional_table(ctx.client, "research_runs", "started_at")
    if runs.empty:
        empty_state(
            "No research runs yet",
            "Run one from the project folder:\n\n"
            "`.venv\\Scripts\\python.exe -m research.evaluate --strategy NAME`\n\n"
            "It tests every stock, index and timeframe on training years, picks "
            "one combination, then opens the locked year once.",
            "🔬",
        )
        return

    strategies = fetch_optional_table(ctx.client, "strategies", "name")
    descriptions: dict[str, str] = {}
    if not strategies.empty and "description" in strategies.columns:
        descriptions = {
            row["name"]: row["description"]
            for _, row in strategies.iterrows()
            if row.get("description")
        }

    grid = grid_frame(runs, descriptions)
    event = st.dataframe(
        grid, use_container_width=True, hide_index=True, height=320,
        on_select="rerun", selection_mode="single-row",
    )

    chosen = list(getattr(event, "selection", {}).get("rows", []))
    if not chosen:
        st.caption("Select a row to see the full detail.")
        return

    ordered = runs.copy()
    ordered["_when"] = pd.to_datetime(ordered["started_at"], utc=True, errors="coerce")
    ordered = ordered.sort_values("_when", ascending=False).reset_index(drop=True)
    st.divider()
    _detail(ctx, ordered.iloc[chosen[0]])
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_page.py -q -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app_pages/research_page.py tests/test_research_page.py
git commit -m "feat(dashboard): the research grid and its detail view

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Register the page

**Files:**
- Modify: `dashboard.py`

- [ ] **Step 1: Import it**

In `dashboard.py`'s `_app()`, add `research_page` to the `from app_pages import (...)` list, keeping alphabetical order.

- [ ] **Step 2: Add it to the navigation**

In the `pages = [...]` list, directly after the `backtest_page` entry, add:
```python
        page(research_page, "Research", "🔬", "research"),
```

- [ ] **Step 3: Mention it in the module docstring**

In the `PAGES` block of `dashboard.py`'s docstring, after the `Backtest` line, add:
```
  Research       what the daily loop found: one row per run
```

- [ ] **Step 4: Prove importing the app still has no side effects**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_dashboard.py -q -p no:cacheprovider`
Expected: all PASS (that file imports `dashboard` precisely to prove this).

- [ ] **Step 5: Boot the app and look at it**

Run: `./.venv/Scripts/streamlit.exe run dashboard.py --server.headless true --server.port 8599`
Open `http://localhost:8599`, log in, open **Research**, confirm the run saved in Task 5 appears as a row, click it, and confirm the detail shows the chart, the combination table and the trade list. Stop the server afterwards.

If the row is missing but `research_runs` has data, check that `fetch_optional_table` is not caching an older empty result — use the sidebar's **🔄 Refresh data**.

- [ ] **Step 6: Commit**

```bash
git add dashboard.py
git commit -m "feat(dashboard): add the Research page to the sidebar

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Full run, then finish

- [ ] **Step 1: Store a real full run**

Run: `./.venv/Scripts/python.exe -m research.evaluate --strategy N200-PULLBACK-DAY`
Expected: about 15 minutes, ending with `saved as run <uuid>`, with 1,213 combination rows.

- [ ] **Step 2: Full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: 0 failed.

- [ ] **Step 3: Record it in the spec**

In `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` §10, mark piece 2 built, with the measured row and child counts.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md
git commit -m "docs(spec): piece 2 built

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```
