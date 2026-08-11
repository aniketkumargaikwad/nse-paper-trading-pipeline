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
    'universe can always state how old it is — membership is TODAY''s applied '
    'to past data, so results carry survivorship bias that must stay visible.';

-- Engines filter on this on every run.
create index if not exists strategies_runnable_idx
    on strategies (status) where status = 'valid';
