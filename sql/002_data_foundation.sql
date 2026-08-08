-- ============================================================================
-- 002_data_foundation.sql — Phase 0: precise candle storage.
--
-- HOW TO RUN: Supabase dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to re-run: every statement is idempotent, including the constraint
-- additions below (guarded with DO blocks / DROP...IF EXISTS + re-CREATE,
-- since Postgres has no ADD CONSTRAINT IF NOT EXISTS and this migration has
-- already been applied to the live database, so CREATE TABLE IF NOT EXISTS
-- alone is a no-op there).
--
-- Mostly additive: new tables, indexes, and constraints for the candle
-- store. The one exception is `strategies.timeframe`: 001's CHECK predates
-- Phase 0's '5m'/'25m' timeframes, so it is widened here (drop + recreate,
-- see bottom of file) or a 25m strategy would be unstorable. Nothing else
-- in Phase 1 (positions, trades, run_audit, backtest_results) is touched.
-- ============================================================================

begin;

-- Symbol master, mapping our 'NSE:RELIANCE' notation to Dhan identifiers.
create table if not exists instruments (
    id               bigserial primary key,
    symbol           text not null,
    exchange         text not null,
    tradingsymbol    text not null,
    dhan_security_id text not null,
    dhan_segment     text not null,
    name             text,
    instrument_type  text not null default 'EQUITY',  -- EQUITY | INDEX
    lot_size         integer,
    is_active        boolean not null default true,
    refreshed_on     date not null,
    constraint instruments_symbol_key unique (symbol)
);

comment on table instruments is
    'Symbol master: our EXCHANGE:SYMBOL notation mapped to Dhan security IDs.';

-- Named universes (Nifty 50/100, custom). Populated in Phase 1.
create table if not exists symbol_groups (
    id          bigserial primary key,
    name        text not null unique,
    description text,
    is_system   boolean not null default false,
    created_at  timestamptz not null default now()
);

create table if not exists symbol_group_members (
    group_id      bigint not null references symbol_groups(id) on delete cascade,
    instrument_id bigint not null references instruments(id)   on delete cascade,
    primary key (group_id, instrument_id)
);

-- The candle store. numeric (not float) so P&L maths cannot drift.
create table if not exists candles (
    instrument_id bigint        not null references instruments(id) on delete cascade,
    timeframe     text          not null check (timeframe in ('5m', 'day')),  -- '5m' (base) | 'day'
    ts            timestamptz   not null,  -- candle START (open) time, UTC
    open          numeric(14,4) not null,
    high          numeric(14,4) not null,
    low           numeric(14,4) not null,
    close         numeric(14,4) not null,
    volume        bigint        not null,
    primary key (instrument_id, timeframe, ts)
);

comment on table candles is
    'Stored candles. Only the 5m base and adjusted daily are stored; 15/25/30/60m are resampled on read.';

-- What is ACTUALLY cached. The guard against claiming coverage we lack.
create table if not exists candle_coverage (
    instrument_id     bigint      not null references instruments(id) on delete cascade,
    timeframe         text        not null,
    first_ts          timestamptz not null,  -- candle-START convention, same as candles.ts
    last_ts           timestamptz not null,  -- candle-START convention, same as candles.ts
    source            text        not null,  -- 'dhan' | 'yfinance'
    last_refreshed_at timestamptz not null default now(),
    primary key (instrument_id, timeframe),
    constraint candle_coverage_range_ok check (first_ts <= last_ts)
);

comment on table candle_coverage is
    'One contiguous cached range per (instrument, timeframe). Never advanced past genuinely fetched data.';

-- Detected problems, surfaced for review - never silently corrected.
create table if not exists data_quality_flags (
    id            bigserial primary key,
    instrument_id bigint not null references instruments(id) on delete cascade,
    timeframe     text   not null,
    flag_type     text   not null
        check (flag_type in ('suspected_split', 'session_gap', 'ohlc_invalid')),
    ts            timestamptz not null,
    detail        jsonb  not null,
    resolved      boolean not null default false,
    created_at    timestamptz not null default now()
);

-- Provider credentials with expiry. NEVER exposed to the dashboard.
create table if not exists provider_tokens (
    provider     text primary key,  -- 'dhan'
    access_token text not null,
    expires_at   timestamptz not null,
    updated_at   timestamptz not null default now()
);

-- candles_instrument_tf_ts_idx intentionally NOT created: it would duplicate
-- the primary key (instrument_id, timeframe, ts). Postgres btrees scan in
-- both directions, and with equality on the two leading columns the DESC
-- direction on ts needs no separate index. ~375MB at target scale on a
-- 500MB budget, and it doubles index maintenance on every bulk upsert.
drop index if exists candles_instrument_tf_ts_idx;

create index if not exists quality_unresolved_idx
    on data_quality_flags (instrument_id) where not resolved;

-- ============================================================================
-- Additive constraints. Guarded because this migration has already run once
-- live: CREATE TABLE IF NOT EXISTS above is a no-op there, and Postgres has
-- no ADD CONSTRAINT IF NOT EXISTS, so each addition checks pg_constraint
-- first. Also correct and safe to run against a brand-new database.
-- ============================================================================

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.candles'::regclass
          and conname = 'candles_prices_positive'
    ) then
        alter table candles add constraint candles_prices_positive
            check (open > 0 and high > 0 and low > 0 and close > 0);
    end if;
end $$;

-- Deliberately NO high/low ordering check (e.g. high >= greatest(open,
-- close)). data_quality_flags.flag_type includes 'ohlc_invalid' because the
-- design intends to STORE and surface internally-inconsistent candles for
-- review, not reject them at the DB layer. Do not add one.
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.candles'::regclass
          and conname = 'candles_volume_nonnegative'
    ) then
        alter table candles add constraint candles_volume_nonnegative
            check (volume >= 0);
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.candle_coverage'::regclass
          and conname = 'candle_coverage_timeframe_check'
    ) then
        alter table candle_coverage add constraint candle_coverage_timeframe_check
            check (timeframe in ('5m', 'day'));
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.candle_coverage'::regclass
          and conname = 'candle_coverage_source_check'
    ) then
        alter table candle_coverage add constraint candle_coverage_source_check
            check (source in ('dhan', 'yfinance'));
    end if;
end $$;

do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.data_quality_flags'::regclass
          and conname = 'data_quality_flags_timeframe_check'
    ) then
        alter table data_quality_flags add constraint data_quality_flags_timeframe_check
            check (timeframe in ('5m', 'day'));
    end if;
end $$;

-- Quality detection runs on every fetch; without this, re-scanning a range
-- with a persistent session gap inserts a duplicate flag every time.
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.data_quality_flags'::regclass
          and conname = 'data_quality_flags_unique_flag'
    ) then
        alter table data_quality_flags add constraint data_quality_flags_unique_flag
            unique (instrument_id, timeframe, flag_type, ts);
    end if;
end $$;

-- Prevents a symbol rename during a master refresh from silently
-- double-storing identical candles under two instrument_ids.
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conrelid = 'public.instruments'::regclass
          and conname = 'instruments_dhan_id_key'
    ) then
        alter table instruments add constraint instruments_dhan_id_key
            unique (dhan_segment, dhan_security_id);
    end if;
end $$;

-- ============================================================================
-- Widen strategies.timeframe: 001 predates Phase 0's '5m' and '25m'
-- timeframes. drop + recreate under the same constraint name is idempotent
-- (safe to re-run against the already-migrated database).
-- ============================================================================
alter table strategies drop constraint if exists strategies_timeframe_check;
alter table strategies add constraint strategies_timeframe_check
    check (timeframe in ('5m', '15m', '25m', '30m', '60m', 'day'));

-- ============================================================================
-- Row Level Security
-- ============================================================================
alter table instruments          enable row level security;
alter table symbol_groups        enable row level security;
alter table symbol_group_members enable row level security;
alter table candles              enable row level security;
alter table candle_coverage      enable row level security;
alter table data_quality_flags   enable row level security;
alter table provider_tokens      enable row level security;

drop policy if exists "anon read instruments"          on instruments;
drop policy if exists "anon read symbol_groups"        on symbol_groups;
drop policy if exists "anon read symbol_group_members" on symbol_group_members;
drop policy if exists "anon read candles"              on candles;
drop policy if exists "anon read candle_coverage"      on candle_coverage;
drop policy if exists "anon read quality_flags"        on data_quality_flags;

create policy "anon read instruments"          on instruments          for select to anon using (true);
create policy "anon read symbol_groups"        on symbol_groups        for select to anon using (true);
create policy "anon read symbol_group_members" on symbol_group_members for select to anon using (true);
create policy "anon read candles"              on candles              for select to anon using (true);
create policy "anon read candle_coverage"      on candle_coverage      for select to anon using (true);
create policy "anon read quality_flags"        on data_quality_flags   for select to anon using (true);

-- provider_tokens gets NO anon policy: it holds a live API credential.
-- With RLS on and no policy, the dashboard's key sees zero rows.

commit;
