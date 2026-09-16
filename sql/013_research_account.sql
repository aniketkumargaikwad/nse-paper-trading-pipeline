-- ---------------------------------------------------------------------------
-- The account replay (research/account.py): the locked year is judged on a
-- ten-slot account taking signals as they arrive across every stock, which is
-- what the owner's money would actually have earned. The monthly columns from
-- sql/012 now hold the ACCOUNT's figures; these say how it was run.
--
-- Additive only. Apply in the Supabase SQL editor.
-- ---------------------------------------------------------------------------

alter table research_runs add column if not exists account_slots               integer;
alter table research_runs add column if not exists locked_slot_use_pct         numeric(6,2);
alter table research_runs add column if not exists locked_signals_skipped_pct  numeric(6,2);

comment on column research_runs.account_slots is
    'Positions the account could hold at once, Rs 1 lakh each. Null for runs before 16 Sep 2026.';
comment on column research_runs.locked_slot_use_pct is
    'Share of slot-time actually in a position over the locked year.';
comment on column research_runs.locked_signals_skipped_pct is
    'Share of entry signals skipped because every slot was taken.';
