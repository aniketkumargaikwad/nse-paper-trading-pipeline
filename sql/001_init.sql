-- ============================================================================
-- 001_init.sql — one-time database setup for the paper-trading pipeline.
--
-- HOW TO RUN: Supabase dashboard -> SQL Editor -> New query -> paste this
-- entire file -> Run. Safe to re-run: every statement is idempotent
-- (IF NOT EXISTS / OR REPLACE), so a partial first run can simply be re-run.
--
-- SECURITY MODEL
--   * The engine / backtester / login script use the SERVICE-ROLE key, which
--     bypasses Row Level Security (RLS) entirely.
--   * The Streamlit dashboard uses the ANON key. RLS is enabled on every
--     table; anon gets read-only SELECT policies on display tables ONLY.
--   * daily_token has NO anon policy: the Kite access token must never be
--     readable with the dashboard's key.
-- ============================================================================

begin;

-- gen_random_uuid() lives in pgcrypto (enabled by default on Supabase, but
-- guarded here so this file also works on a plain Postgres).
create extension if not exists pgcrypto;

-- ----------------------------------------------------------------------------
-- strategies: snapshot of strategies.yaml, synced by the engine on every run.
-- The YAML file in the repo is the SOURCE OF TRUTH; this table exists so the
-- dashboard can show definitions without access to the repo.
-- ----------------------------------------------------------------------------
create table if not exists strategies (
    name          text primary key,
    enabled       boolean     not null,
    position_type text        not null check (position_type in ('long', 'short')),
    timeframe     text        not null check (timeframe in ('15m', '30m', '60m', 'day')),
    definition    jsonb       not null,  -- full validated config, for display/audit
    updated_at    timestamptz not null default now()
);

comment on table strategies is
    'Snapshot of strategies.yaml (source of truth is the repo file). Synced each engine run.';

-- ----------------------------------------------------------------------------
-- daily_token: the Kite access token written each morning by login.py.
-- SEBI mandates daily expiry, so exactly one row per trading date.
-- ----------------------------------------------------------------------------
create table if not exists daily_token (
    token_date   date        primary key,  -- IST trading date the token is valid for
    access_token text        not null,
    created_at   timestamptz not null default now()
);

comment on table daily_token is
    'Kite access token per IST trading date. Written by login.py each morning. NEVER exposed to the dashboard (no anon RLS policy).';

-- ----------------------------------------------------------------------------
-- positions: OPEN simulated positions only — the live state the stateless
-- engine reloads every run. A row is deleted when the position closes
-- (the completed round-trip then lives in `trades`).
-- ----------------------------------------------------------------------------
create table if not exists positions (
    id                     uuid           primary key default gen_random_uuid(),
    strategy_name          text           not null,
    instrument             text           not null,  -- 'NSE:RELIANCE'
    position_type          text           not null check (position_type in ('long', 'short')),
    quantity               integer        not null check (quantity > 0),
    -- The CLOSED candle whose signal caused the entry (UTC). Also the
    -- idempotency anchor: re-running the engine for the same candle cannot
    -- open the same position twice.
    entry_signal_candle_ts timestamptz    not null,
    entry_fill_ts          timestamptz    not null,  -- next candle's open time (UTC)
    intended_entry_price   numeric(14,4)  not null check (intended_entry_price > 0),
    entry_price            numeric(14,4)  not null check (entry_price > 0),  -- after slippage
    stop_loss_price        numeric(14,4)  not null check (stop_loss_price > 0),
    target_price           numeric(14,4)  not null check (target_price > 0),
    created_at             timestamptz    not null default now(),

    -- One open position per strategy+instrument. This is the primary guard
    -- against double entries when a run is retried.
    constraint positions_one_open_per_strategy_instrument
        unique (strategy_name, instrument)
);

comment on table positions is
    'OPEN simulated positions (live state). Deleted on close; the round-trip is recorded in trades.';

-- ----------------------------------------------------------------------------
-- trades: append-only log of COMPLETED simulated round-trips.
-- ----------------------------------------------------------------------------
create table if not exists trades (
    id                     uuid           primary key default gen_random_uuid(),
    strategy_name          text           not null,
    instrument             text           not null,
    position_type          text           not null check (position_type in ('long', 'short')),
    quantity               integer        not null check (quantity > 0),

    entry_signal_candle_ts timestamptz    not null,
    entry_fill_ts          timestamptz    not null,
    intended_entry_price   numeric(14,4)  not null,
    entry_price            numeric(14,4)  not null,

    exit_signal_candle_ts  timestamptz    not null,
    exit_fill_ts           timestamptz    not null,
    intended_exit_price    numeric(14,4)  not null,
    exit_price             numeric(14,4)  not null,
    exit_reason            text           not null
        check (exit_reason in ('signal', 'stop_loss', 'target', 'end_of_day')),

    gross_pnl              numeric(14,4)  not null,  -- before costs
    costs                  numeric(14,4)  not null,  -- slippage is already in prices; this is the flat per-trade cost
    net_pnl                numeric(14,4)  not null,
    created_at             timestamptz    not null default now(),

    -- Idempotency: one recorded trade per entry signal. A re-run that tries
    -- to close the same position again hits this constraint and is ignored.
    constraint trades_unique_per_entry_signal
        unique (strategy_name, instrument, entry_signal_candle_ts)
);

create index if not exists trades_strategy_idx  on trades (strategy_name);
create index if not exists trades_exit_fill_idx on trades (exit_fill_ts desc);
create index if not exists trades_entry_fill_idx on trades (entry_fill_ts desc);

comment on table trades is
    'Append-only log of completed SIMULATED round-trips. No real orders exist anywhere in this system.';

-- ----------------------------------------------------------------------------
-- run_audit: one row per engine/backtest/login run — the operational history
-- you check when something looks wrong.
-- ----------------------------------------------------------------------------
create table if not exists run_audit (
    id              uuid        primary key default gen_random_uuid(),
    run_type        text        not null
        check (run_type in ('paper', 'backtest', 'login', 'selftest')),
    run_started_at  timestamptz not null,
    run_finished_at timestamptz,
    candle_ts       timestamptz,           -- closed candle evaluated (paper runs)
    status          text        not null check (status in ('ok', 'skipped', 'error')),
    reason          text,                  -- 'market closed', error text, etc.
    details         jsonb,                 -- counts: signals seen, entries, exits...
    created_at      timestamptz not null default now()
);

create index if not exists run_audit_started_idx on run_audit (run_started_at desc);

comment on table run_audit is
    'One row per run. status=skipped with a reason is the NORMAL result outside market hours.';

-- ----------------------------------------------------------------------------
-- instrument_cache: Kite instrument tokens, refreshed at most once per day.
-- Kite''s full instrument dump is several MB; caching the handful of tokens we
-- need keeps each 15-min run fast and API-friendly (free-tier constraint).
-- ----------------------------------------------------------------------------
create table if not exists instrument_cache (
    instrument       text   primary key,   -- 'NSE:RELIANCE'
    instrument_token bigint not null,
    refreshed_on     date   not null       -- IST date of last refresh
);

comment on table instrument_cache is
    'Kite instrument_token lookup cache so stateless runs avoid re-downloading the instrument dump.';

-- ----------------------------------------------------------------------------
-- backtest_results: one row per strategy x instrument x timeframe combination
-- per backtest batch. (Created now so the database needs only one migration.)
-- ----------------------------------------------------------------------------
create table if not exists backtest_results (
    id                     uuid          primary key default gen_random_uuid(),
    batch_id               uuid          not null,   -- groups one backtester invocation
    strategy_name          text          not null,
    instrument             text          not null,
    timeframe              text          not null,
    start_date             date          not null,
    end_date               date          not null,

    total_trades           integer       not null,
    winning_trades         integer       not null,
    net_pnl                numeric(14,4) not null,
    win_rate_pct           numeric(6,2)  not null,
    profit_factor          numeric(10,4),            -- null when there were no losing trades
    max_drawdown_pct       numeric(6,2)  not null,
    longest_losing_streak  integer       not null,

    passed_kill_rules      boolean       not null,
    kill_rule_flags        jsonb         not null,   -- which rule failed and by how much
    created_at             timestamptz   not null default now()
);

create index if not exists backtest_results_batch_idx on backtest_results (batch_id);

comment on table backtest_results is
    'Per-combination metrics from batch backtests, grouped by batch_id.';

-- ============================================================================
-- Row Level Security
-- ============================================================================

alter table strategies       enable row level security;
alter table daily_token      enable row level security;
alter table positions        enable row level security;
alter table trades           enable row level security;
alter table run_audit        enable row level security;
alter table instrument_cache enable row level security;
alter table backtest_results enable row level security;

-- Read-only SELECT for the dashboard's anon key on display tables.
-- (drop+create keeps re-runs idempotent; CREATE POLICY has no IF NOT EXISTS.)
drop policy if exists "anon read strategies"       on strategies;
drop policy if exists "anon read positions"        on positions;
drop policy if exists "anon read trades"           on trades;
drop policy if exists "anon read run_audit"        on run_audit;
drop policy if exists "anon read backtest_results" on backtest_results;

create policy "anon read strategies"       on strategies       for select to anon using (true);
create policy "anon read positions"        on positions        for select to anon using (true);
create policy "anon read trades"           on trades           for select to anon using (true);
create policy "anon read run_audit"        on run_audit        for select to anon using (true);
create policy "anon read backtest_results" on backtest_results for select to anon using (true);

-- daily_token and instrument_cache get NO anon policies:
--   * daily_token holds a live API credential — dashboard must never read it.
--   * instrument_cache is engine plumbing the dashboard does not need.
-- With RLS enabled and no policy, the anon key gets zero rows.

commit;
