-- ---------------------------------------------------------------------------
-- Phase 4: immutable strategy versions.
--
-- WHY THIS EXISTS
--
-- `strategies` is keyed by name and saving overwrites in place. A stored
-- backtest result therefore points at a definition that can be silently
-- rewritten afterwards, which means:
--
--   * no result is reproducible - the thing it measured may no longer exist
--   * comparing two results is unsound - they may describe different rules
--   * deploying to paper trading deploys a NAME, so editing a strategy
--     changes what is trading without any deliberate act of deployment
--
-- A version is immutable once written. Nothing ever updates a row in
-- strategy_versions; a change creates the next version.
--
-- Additive only. Existing rows are backfilled as version 1 at the end.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

create table if not exists strategy_versions (
    id              bigserial   primary key,
    strategy_name   text        not null references strategies(name) on delete cascade,
    version         integer     not null,
    definition      jsonb       not null,
    -- sha256 over the CANONICAL json (sorted keys, no whitespace). Saving a
    -- strategy that is byte-identical to its current version must not mint a
    -- new one, or the history fills with noise and "which version did I test?"
    -- stops having a useful answer.
    definition_hash text        not null,
    format_version  integer     not null,
    created_at      timestamptz not null default now(),

    constraint strategy_versions_seq_key  unique (strategy_name, version),
    constraint strategy_versions_hash_key unique (strategy_name, definition_hash)
);

create index if not exists strategy_versions_name_idx
    on strategy_versions (strategy_name, version desc);

comment on table strategy_versions is
    'Immutable snapshots of a strategy definition. Never updated - a change '
    'creates the next version. Results and deployments reference these, so '
    'what was measured can always be recovered exactly.';
comment on column strategy_versions.definition_hash is
    'sha256 of the canonical JSON. Identical content reuses the version rather '
    'than minting a duplicate.';

-- --- pointers from the mutable world into the immutable one ----------------
alter table strategies
    add column if not exists current_version_id bigint references strategy_versions(id);

comment on column strategies.current_version_id is
    'The version this strategy currently resolves to when edited or deployed.';

alter table backtest_runs
    add column if not exists strategy_version_id bigint references strategy_versions(id);
alter table backtest_results
    add column if not exists strategy_version_id bigint references strategy_versions(id);

comment on column backtest_runs.strategy_version_id is
    'EXACTLY which definition produced this result. Null only for runs that '
    'predate versioning.';

create index if not exists backtest_runs_version_idx
    on backtest_runs (strategy_version_id);

-- --- backfill: every existing valid strategy becomes version 1 -------------
-- Runs once and is idempotent: the insert skips strategies that already have
-- a version, so re-applying this migration is harmless.
insert into strategy_versions (strategy_name, version, definition, definition_hash, format_version)
select s.name,
       1,
       s.definition,
       -- Placeholder hash for pre-existing rows. Real hashes are computed in
       -- Python over canonical JSON; Postgres cannot reproduce that exactly,
       -- and a wrong hash would wrongly suppress the next genuine version.
       'backfill:' || s.name,
       coalesce(s.format_version, 2)
from strategies s
where s.status = 'valid'
  and s.definition is not null
  and not exists (
      select 1 from strategy_versions v where v.strategy_name = s.name
  );

update strategies s
set current_version_id = v.id
from strategy_versions v
where v.strategy_name = s.name
  and v.version = 1
  and s.current_version_id is null;

-- --- RLS, matching the tables it sits beside -------------------------------
alter table strategy_versions enable row level security;
drop policy if exists "anon read strategy_versions" on strategy_versions;
create policy "anon read strategy_versions" on strategy_versions for select to anon using (true);
