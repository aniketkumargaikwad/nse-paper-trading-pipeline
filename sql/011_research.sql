-- ---------------------------------------------------------------------------
-- Piece 2: the research loop's own tables (design 6).
--
-- One run per day becomes one grid row; its children hold what that row opens
-- into. Additive only: nothing existing changes shape, and every table is
-- anon-readable so the hosted dashboard (view-only key) can render it.
--
-- Size, measured from a real run: ~1,213 combo rows, <= 60 locked trades and
-- <= 500 equity points per run - about 0.4 MB a day.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

-- --- 6.1 strategies gains research provenance ------------------------------
alter table strategies add column if not exists title       text;
alter table strategies add column if not exists description text;
alter table strategies add column if not exists hypothesis  text;
alter table strategies add column if not exists origin      text
    not null default 'manual' check (origin in ('manual', 'research'));

comment on column strategies.origin is
    'manual = written by a person; research = produced by the daily loop.';

-- The stored timeframe is now the PICK's timeframe, which may be any supported
-- one (5m and 25m were missing from the original check). The old constraint is
-- dropped by DEFINITION rather than by name: sql/001 did not name it
-- explicitly, so the name is whatever Postgres generated.
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

-- --- the final version's combinations ---------------------------------------
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

-- --- the pick's locked-year trades ------------------------------------------
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

create index if not exists research_locked_trades_run_idx
    on research_locked_trades (run_id, entry_at);

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

create index if not exists research_locked_equity_run_idx
    on research_locked_equity (run_id, day);

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
