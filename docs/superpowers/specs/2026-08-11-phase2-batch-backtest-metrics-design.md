# Phase 2 — Batch Backtest Engine and Metric Suite

**Status:** Approved · **Date:** 2026-08-11

The third of six phases. Phase 0 built a precise candle store; Phase 1 made
strategies expressible across a universe. This phase makes a universe strategy
actually *run*, and makes its result mean something.

---

## 1. Why this phase exists

### 1.1 A universe strategy currently backtests nothing

`backtest.py:538` iterates `strategy.instruments`. For a strategy written as
`universe: NIFTY50`, that tuple is **empty** — Phase 1 deliberately kept the
parser pure, so a universe is a name until someone resolves it, and nobody
does. The run produces zero combinations, reports no error, and finishes
successfully.

This is the worst failure mode this codebase recognises: a silent nothing that
looks like a clean result. Every other layer refuses it — coverage is never
overstated, an empty universe is never returned, a zero-quantity trade is
recorded as a skip. The engine is the one place it still happens.

### 1.2 Fifty rows are not a verdict

The engine reports one row per strategy × instrument. Across NIFTY50 that is
fifty rows and no answer to the question the universe was for: *does this edge
hold broadly, or did one stock carry it?*

The existing `MIN_PROFITABLE_SYMBOLS` kill rule shows the instinct is already
there — "edge on one symbol only is usually curve-fitting" — but it is a
pass/fail flag rather than a number you can read.

### 1.3 Net P&L cannot distinguish steady from lucky

Current metrics are trades, win rate, profit factor, max drawdown, longest
losing streak. All useful, none risk-adjusted. Two strategies with identical
net P&L — one grinding out small consistent wins, one with a single enormous
outlier — are indistinguishable in today's output.

### 1.4 The robustness rule stopped scaling

`MIN_PROFITABLE_SYMBOLS = 3` was written when strategies named five symbols by
hand: a 60% bar. Applied unchanged to NIFTY50 it becomes "profitable on 3 of
50" — a 6% bar that would pass a strategy losing money on 47 stocks, which is
precisely the curve-fitting the rule exists to catch.

### Non-goals for this phase

- **Performance work.** No parallelism or vectorisation. At 50–100 symbols the
  sequential engine is adequate; NIFTY500 can wait until someone actually wants
  to run it.
- Shared-capital portfolio simulation — still per-symbol and independent, as
  decided in Phase 1.
- Strategies grid, detail page, results popup — *Phase 3*
- Paper-trading deployment and performance page — *Phase 4*
- Live broker execution — *Phase 5*

---

## 2. Decisions and the reasoning behind them

### 2.1 Universe resolution happens in the engine, loudly

`backtest.py` resolves `strategy.universe` through `universes.resolve_universe`
before running, and a strategy that resolves to zero symbols is a **hard
error**, not an empty result.

Partial resolution is reported, never silently narrowed: a NIFTY100 run that
resolves 97 names says so, names the three, and records both counts. This
mirrors what `refresh_universes.py` already does — the rule is the same
wherever a universe becomes symbols.

### 2.2 Both a pooled headline and its dispersion

Pooling every trade answers "what would this have made". Averaging per-symbol
results answers "does this generalise". They disagree exactly when it matters —
a strategy carried by one outlier is pooled-profitable and broadly negative.

**Chosen: report both.** Pooled metrics for the headline, plus dispersion:
symbols traded, symbols profitable, median symbol net P&L, best and worst
symbol. The `MIN_PROFITABLE_SYMBOLS` instinct becomes a first-class number
rather than only a flag.

### 2.3 Risk-adjusted metrics on two clearly separated bases

A Sharpe ratio needs a return series and a capital base. This system has
neither by default: trades are per-symbol and independent, with no shared
capital. Computing "Sharpe" on trade returns produces a number that is not
comparable to any published Sharpe and rewards trading more often.

**Chosen: compute both, label both.**

| Basis | What it is | Why |
|---|---|---|
| **Daily** | Daily realised P&L across the universe ÷ capital base, annualised the conventional way | Comparable to published figures. Well-defined because intraday strategies are flat overnight |
| **Trade** | Expectancy (mean net P&L per trade) and System Quality Number (mean ÷ stdev of trade P&L × √n) | Needs no capital assumption; intuitive |

**Capital base** = `notional_per_trade × symbols traded`. This assumes every
symbol could hold a position simultaneously, which is the worst case under
per-symbol independent sizing. It makes the daily Sharpe read **low rather than
flattering**, which is the correct direction for a number used to decide
whether to deploy real money.

For `fixed_quantity` strategies the notional is unknown, so the capital base
falls back to the largest entry notional actually observed — the same basis
`compute_metrics` already uses for drawdown percentage.

**Documented limitation:** daily-basis figures are computed from *realised*
P&L only. A position held across a day boundary (possible without
`square_off`) contributes nothing until it closes, so its daily series is
lumpier than a mark-to-market one. Stated on the result rather than corrected,
because marking to market needs a price series per open position and would add
a second source of truth for P&L.

### 2.4 Symbol robustness becomes proportional

`MIN_PROFITABLE_SYMBOLS = 3` is replaced by
`MIN_PROFITABLE_SYMBOL_PCT = 40.0` — profitable on at least 40% of the symbols
actually traded.

40% is a judgement call and is recorded as one: a strategy profitable on fewer
than two of five symbols was already failing the old rule, and 40% preserves
that meaning while scaling to 50 or 500. It will fail some strategies that pass
today; that is the point, since those are passing a rule that stopped working.

The other three kill rules are unchanged.

### 2.5 A run record, separate from per-symbol results

Per-symbol results and a strategy-level verdict are different grains. Putting
both in `backtest_results` with a null instrument would mean every query has to
remember to filter it, and a forgotten filter double-counts P&L — silent
wrongness rather than a loud failure.

**Chosen: a new `backtest_runs` table**, one row per (batch, strategy), joined
to the existing per-symbol rows on `batch_id`. It is also the natural home for
the survivorship caveat: the resolved symbol list, the universe name, and the
constituent-list date live on the run, so a result can always state what it was
computed over and how old that membership was.

This is the per-run snapshot Phase 1 §2.3 promised and deferred to here.

### 2.6 Metrics move to a pure module

`backtest.py` is ~680 lines holding simulation, metrics, kill rules, CLI and
database writes. This phase would push it past 900.

**Chosen: extract `metrics.py`** — pure functions over trades, no I/O, no
database, no strategy objects. A subtly wrong Sharpe is worse than no Sharpe
because it looks authoritative, so the module that computes it must be testable
against hand-computed values in isolation.

---

## 3. Architecture

```
   strategies (Supabase, status='valid')
            │
            ▼
   ┌────────────────────┐   universe name    ┌──────────────┐
   │  backtest.py       │───────────────────►│ universes.py │
   │  (orchestration)   │◄───────────────────│  resolve     │
   └─────────┬──────────┘   symbols + as_of  └──────────────┘
             │
             │ per symbol: candles ──► simulate_with_skips()
             ▼
   ┌────────────────────────────────────────┐
   │  metrics.py  (PURE)                    │
   │   per_symbol()   → ComboMetrics        │
   │   pooled()       → PooledMetrics       │
   │   dispersion()   → Dispersion          │
   │   daily_series() → risk-adjusted       │
   └─────────┬──────────────────────────────┘
             ▼
   ┌────────────────────┐        ┌────────────────────┐
   │ backtest_results   │        │ backtest_runs      │
   │ one row per symbol │◄──────►│ one row per        │
   │                    │batch_id│ strategy + run     │
   └────────────────────┘        └────────────────────┘
```

The property that matters: **`metrics.py` never touches a database, a network,
or a clock.** Everything it computes can be checked against numbers worked out
by hand.

### 3.1 Modules

| Module | Responsibility | Depends on |
|---|---|---|
| `metrics.py` | All metric maths: per-symbol, pooled, dispersion, daily and trade-basis risk measures. Pure | — |
| `backtest.py` | Orchestration: resolve universes, load candles, simulate, assemble, persist | `metrics`, `universes`, `db` |
| `universes.py` | Unchanged — already returns a `UniverseResolution` carrying symbols, missing names and the list date | — |
| `db.py` | Gains `insert_backtest_run`; `insert_backtest_results` unchanged | — |

---

## 4. Data model

Additive only.

```sql
create table if not exists backtest_runs (
    id                    uuid primary key default gen_random_uuid(),
    batch_id              uuid        not null,
    strategy_name         text        not null,
    timeframe             text        not null,
    start_date            date        not null,
    end_date              date        not null,

    -- What it was actually computed over. Without this a result cannot be
    -- reproduced, and its survivorship caveat has nowhere to live.
    universe_name         text,                  -- null for an instruments: list
    constituents_as_of    date,                  -- null for an instruments: list
    symbols_requested     integer     not null,
    symbols_resolved      integer     not null,
    symbols               jsonb       not null,  -- the exact list used
    symbols_missing       jsonb       not null,  -- listed but absent from instruments

    -- Pooled: every trade across the universe as one stream.
    total_trades          integer       not null,
    winning_trades        integer       not null,
    net_pnl               numeric(14,4) not null,
    win_rate_pct          numeric(6,2)  not null,
    profit_factor         numeric(10,4),
    max_drawdown_pct      numeric(6,2)  not null,
    longest_losing_streak integer       not null,
    entries_skipped       integer       not null default 0,

    -- Dispersion: was the edge broad, or one lucky name?
    symbols_profitable    integer       not null,
    median_symbol_pnl     numeric(14,4) not null,
    best_symbol           text,
    best_symbol_pnl       numeric(14,4),
    worst_symbol          text,
    worst_symbol_pnl      numeric(14,4),

    -- Risk-adjusted, on two bases (section 2.3).
    capital_base          numeric(16,4) not null,
    sharpe_daily          numeric(10,4),
    sortino_daily         numeric(10,4),
    cagr_pct              numeric(10,4),
    expectancy_per_trade  numeric(14,4) not null,
    system_quality_number numeric(10,4),

    passed_kill_rules     boolean     not null,
    kill_rule_flags       jsonb       not null,
    created_at            timestamptz not null default now()
);

create index if not exists backtest_runs_batch_idx    on backtest_runs (batch_id);
create index if not exists backtest_runs_strategy_idx on backtest_runs (strategy_name, created_at desc);

comment on table backtest_runs is
    'One row per strategy per batch: the pooled verdict, how it was distributed '
    'across symbols, and exactly which symbols it was computed over.';
comment on column backtest_runs.constituents_as_of is
    'Date of the index constituent list used. Membership is TODAY''s applied to '
    'past data, so results are flattered by survivorship — this keeps that visible.';
comment on column backtest_runs.capital_base is
    'notional_per_trade x symbols traded: the worst case under per-symbol '
    'independent sizing, so daily Sharpe reads low rather than flattering.';
```

`backtest_results` is unchanged. `entries_skipped` finally persists what
`SimResult.skipped` has been computing since Phase 1 and discarding.

**RLS:** `backtest_runs` gets RLS enabled with an anon read policy, matching
`backtest_results`.

---

## 5. Metric definitions

Stated precisely, because these are the numbers a deploy decision rests on.

**Pooled metrics** use the identical formulas `compute_metrics` uses today,
over the concatenation of every symbol's trades sorted by exit fill time. Sort
order matters: drawdown and losing streak are path-dependent, and concatenating
symbol-by-symbol instead of chronologically would produce a fictitious equity
curve.

**Dispersion**
- `symbols_profitable` — symbols whose own net P&L > 0
- `median_symbol_pnl` — median of per-symbol net P&L
- `best/worst_symbol` — the extremes, named

**Daily basis**
- Daily realised P&L: each trade's net P&L attributed to the IST date of its
  **exit fill**, summed per date, over trading dates in range with no trade
  contributing zero.
- Daily return = daily P&L ÷ `capital_base`
- `sharpe_daily` = mean(daily returns) ÷ stdev(daily returns) × √252
- `sortino_daily` — as above but the denominator uses downside deviation
  (negative daily returns only)
- `cagr_pct` = ((1 + total return)^(365/days) − 1) × 100
- Fewer than 2 trading days, or zero standard deviation, yields `null` rather
  than a fabricated figure

**Trade basis**
- `expectancy_per_trade` = mean net P&L per trade
- `system_quality_number` = mean(trade P&L) ÷ stdev(trade P&L) × √(trade count);
  `null` below 2 trades or at zero standard deviation

---

## 6. Error handling

| Failure | Behaviour |
|---|---|
| `universe` names a group that does not exist | Hard error naming the universe and listing those that do |
| Universe resolves to zero symbols | Hard error. Never a zero-trade "result" |
| Universe resolves partially | Run proceeds over what resolved; both counts and the missing names are stored and printed |
| A symbol has no cached candles | That symbol is skipped and named; the run continues and records it in `symbols_missing` |
| A symbol's ATR period exceeds its history | `RiskLevelError` for that symbol, recorded; the run continues (matches the paper engine's per-combination isolation) |
| Fewer than 2 trading days of results | Risk-adjusted metrics are `null`, not zero — an absent measurement must not read as a measured zero |
| Zero standard deviation | Same: `null` |
| No trades at all across the universe | A run row is still written with zeroes and `passed_kill_rules = false`. A strategy that traded nothing is a result, and must be visible rather than absent |

---

## 7. Testing

**Offline, no network or credentials — the bulk**

- Every metric against hand-computed values on small fixed trade sets. This is
  the core of the phase: a plausible-looking wrong Sharpe is worse than none.
- Pooled metrics use chronological order — asserted with symbols whose trades
  interleave in time, so a symbol-by-symbol concatenation gives a different
  drawdown and the test catches it.
- Dispersion on a deliberately lopsided set: one huge winner, many small
  losers. Pooled P&L positive, `symbols_profitable` low — the exact case the
  dispersion numbers exist to expose.
- Null-not-zero: single trading day, zero variance, and no trades at all.
- Proportional kill rule at boundaries (39%, 40%, 41%) and on a 5-symbol
  universe, confirming it still means what the old fixed count meant.
- Universe resolution in the engine: full, partial, and zero-resolution.
- A strategy with `instruments:` behaves exactly as before — asserted by
  comparing a full run against the current output.

**Live (opt-in, one command)**

- A NIFTY50 run against the real cached candles, asserting a run row is written
  with 50 symbols and a constituent date.

**Migration safety**

- Additive; `backtest_results` untouched; existing rows unaffected.

---

## 8. Risks

| Risk | Mitigation |
|---|---|
| A wrong risk metric looks authoritative | `metrics.py` is pure and tested against hand-computed values; two bases reported separately so they cross-check |
| The 40% threshold is arbitrary | Recorded as a judgement call with its reasoning; a named constant, changed in one place |
| Capital base overstates required capital | Deliberate — it biases Sharpe low, the safe direction for a deploy decision, and is documented on the column |
| Daily basis ignores unrealised P&L | Stated as a limitation; correcting it needs a second P&L source of truth |
| Sequential runs too slow at NIFTY500 | Explicit non-goal; 50–100 is the working scale, and the storage projection already warns before a 500-symbol backfill |

---

## 9. Definition of done

- [ ] `universe: NIFTY50` backtests 50 symbols; a zero-resolution universe is a
      hard error, not an empty result
- [ ] One `backtest_runs` row per strategy per batch, carrying the resolved
      symbol list and the constituent date
- [ ] Pooled metrics computed over chronologically ordered trades
- [ ] Dispersion exposes a one-lucky-symbol strategy as such
- [ ] Sharpe/Sortino/CAGR on the daily basis, expectancy and SQN on the trade
      basis, each labelled with its basis
- [ ] Insufficient data yields null metrics, never zero
- [ ] Symbol robustness is proportional and behaves sensibly at 5 and at 50
- [ ] `entries_skipped` persisted
- [ ] An `instruments:` strategy produces the same results it does today
- [ ] Full offline suite green

---

## 10. Sources

- Phase 0 design: `docs/superpowers/specs/2026-08-08-data-foundation-design.md`
- Phase 1 design: `docs/superpowers/specs/2026-08-10-phase1-strategy-universes-design.md`
  (§2.3 defers the per-run snapshot to this phase)
- [QuantStats metrics reference](https://github.com/ranaroussi/quantstats) —
  informed the metric selection; not taken as a dependency
