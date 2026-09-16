-- ---------------------------------------------------------------------------
-- The atlas (research/atlas.py): baseline signals measured once on the
-- training years, so the AI designs from what this data has shown rather
-- than from folklore. One row per baseline, replaced when re-measured.
--
-- Training years only - the training_summary is the same object Opus sees
-- for its own versions, which has no locked-year field by construction.
--
-- Apply in the Supabase SQL editor.
-- ---------------------------------------------------------------------------

create table if not exists research_atlas (
    name              text        primary key,
    description       text        not null,
    strategy_yaml     text        not null,
    data_end          date        not null,
    training_summary  jsonb       not null,
    updated_at        timestamptz not null default now()
);

comment on table research_atlas is
    'Baseline signals measured on the training years, shown to the AI before it proposes.';

alter table research_atlas enable row level security;
drop policy if exists "anon read research_atlas" on research_atlas;
create policy "anon read research_atlas" on research_atlas for select to anon using (true);
