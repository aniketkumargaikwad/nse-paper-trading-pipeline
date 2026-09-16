-- ---------------------------------------------------------------------------
-- The basket (design 2026-09-16 §3): the locked year is now judged on Rs 1
-- lakh spread equally over every stock at the picked timeframe, month by
-- month, instead of on one stock picked out of ~1,177 tries.
--
-- Additive only. Older rows keep null here and the page shows a dash.
-- `pick_symbol` now carries the text "NIFTY200 basket (N stocks)".
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

alter table research_runs add column if not exists basket_stocks              integer;
alter table research_runs add column if not exists locked_avg_month_pct       numeric(8,3);
alter table research_runs add column if not exists locked_months_positive_pct numeric(6,2);
alter table research_runs add column if not exists locked_worst_month_pct     numeric(8,3);
alter table research_runs add column if not exists locked_target_met          boolean;
alter table research_runs add column if not exists training_avg_month_pct     numeric(8,3);
alter table research_runs add column if not exists training_months            integer;
alter table research_runs add column if not exists training_edge_t            numeric(8,3);
alter table research_runs add column if not exists locked_months              jsonb;

comment on column research_runs.basket_stocks is
    'How many stocks the locked-year basket held (Rs 1 lakh spread equally).';
comment on column research_runs.locked_avg_month_pct is
    'The basket''s average calendar month over the locked year, in percent of Rs 1 lakh.';
comment on column research_runs.locked_target_met is
    'True when the average locked month reached the owner''s 4% floor.';
comment on column research_runs.training_edge_t is
    'Training luck check: average monthly excess over holding, in standard errors.';
comment on column research_runs.locked_months is
    'One row per locked month: {month, strategy_pct, holding_pct, trades, stocks}.';
