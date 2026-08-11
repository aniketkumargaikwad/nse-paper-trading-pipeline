-- ---------------------------------------------------------------------------
-- Phase 2: the strategy-level verdict.
--
-- backtest_results holds one row per strategy x instrument. A strategy-level
-- verdict is a DIFFERENT grain, so it gets its own table rather than rows with
-- a null instrument: every query would then have to remember to filter that
-- null, and a forgotten filter double-counts P&L — silent wrongness rather
-- than a loud failure.
--
-- Additive only. backtest_results is untouched.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

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
    symbols_missing       jsonb       not null,  -- listed but produced no candles

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

    -- Risk-adjusted, on two explicitly separate bases. Nulls are meaningful:
    -- they mean "not measurable from this data", never "measured zero".
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

comment on column backtest_runs.symbols_missing is
    'Listed by the universe but produced no candles. symbols_resolved counts '
    'only those that did, so a 47-symbol result cannot present itself as a '
    'NIFTY50 one.';

-- ---------------------------------------------------------------------------
-- Row Level Security — matches backtest_results.
-- ---------------------------------------------------------------------------
alter table backtest_runs enable row level security;
drop policy if exists "anon read backtest_runs" on backtest_runs;
create policy "anon read backtest_runs" on backtest_runs for select to anon using (true);
