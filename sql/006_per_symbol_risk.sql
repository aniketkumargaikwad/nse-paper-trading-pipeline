-- ---------------------------------------------------------------------------
-- Per-symbol risk metrics, and the equity curve behind a run.
--
-- backtest_results carried trades, win rate, net P&L, profit factor, drawdown
-- and losing streak — enough to say WHAT happened on a symbol, not enough to
-- rank symbols against each other. Net P&L alone cannot separate a steady
-- contributor from one lucky trade, which is exactly the judgement ranking a
-- universe requires.
--
-- The same functions that already compute these pooled are reused per symbol,
-- so a symbol row and the run row cannot drift apart in definition.
--
-- Additive only. Existing rows keep their columns and gain nulls.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

alter table backtest_results
    add column if not exists sharpe_daily          numeric(10,4),
    add column if not exists sortino_daily         numeric(10,4),
    add column if not exists cagr_pct              numeric(10,4),
    add column if not exists expectancy_per_trade  numeric(14,4),
    add column if not exists system_quality_number numeric(10,4),
    add column if not exists capital_base          numeric(16,4),
    add column if not exists entries_skipped       integer not null default 0;

comment on column backtest_results.sharpe_daily is
    'Per-symbol, same definition as the run-level figure. Null means not '
    'measurable from this symbol''s data - never a measured zero.';

-- What the run assumed about charges. Costs are chosen by an environment
-- variable, so without this a run made under a flat Rs30 charge and one made
-- under itemised charges are indistinguishable in the table while differing
-- by more than the edge being measured — which makes comparing them a lie.
alter table backtest_runs
    add column if not exists cost_model text;

-- The equity curve, stored once per run rather than recomputed on every view.
-- Everything visual (equity, drawdown, monthly heatmap) derives from it, and
-- recomputing means re-reading every trade each time a chart is drawn.
create table if not exists backtest_equity (
    id            bigserial   primary key,
    batch_id      uuid        not null,
    strategy_name text        not null,
    timeframe     text        not null,
    -- IST trading date. Days the strategy was flat are present with 0, because
    -- a flat day is a real observation of how it behaves; omitting them would
    -- compress the curve and flatter every risk figure derived from it.
    day           date        not null,
    daily_pnl     numeric(14,4) not null,
    equity        numeric(14,4) not null,   -- cumulative net P&L
    created_at    timestamptz not null default now(),

    constraint backtest_equity_key unique (batch_id, strategy_name, timeframe, day)
);

create index if not exists backtest_equity_run_idx
    on backtest_equity (batch_id, strategy_name, timeframe, day);

comment on table backtest_equity is
    'Daily realised P&L and cumulative equity per run. Stored so charts do not '
    're-read every trade, and so a curve can be compared across runs.';

alter table backtest_equity enable row level security;
drop policy if exists "anon read backtest_equity" on backtest_equity;
create policy "anon read backtest_equity" on backtest_equity for select to anon using (true);
