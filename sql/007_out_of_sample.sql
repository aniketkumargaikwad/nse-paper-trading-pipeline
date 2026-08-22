-- ---------------------------------------------------------------------------
-- In-sample vs out-of-sample figures for a run.
--
-- Every result this platform stored before now was in-sample: the rules, the
-- stop, the target and the universe were all chosen while looking at the same
-- candles that then scored them. `sweep.py` already warns that trying 24
-- variants at a 5% threshold produces a passing variant by chance alone; these
-- columns are the other half of that warning — somewhere to record how the
-- survivor did on data it was never fitted to.
--
-- `passed_kill_rules` deliberately keeps its existing meaning (the whole
-- window) so runs made with and without a holdout stay comparable. The honest
-- verdict is `oos_passed_kill_rules` beside it.
--
-- Null in every one of these means the run was made without --holdout. That is
-- NOT the same as "the strategy failed out of sample", and nothing should
-- render it as a zero.
--
-- Additive only. Existing rows keep their columns and gain nulls.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

alter table backtest_runs
    -- The boundary. In-sample is everything before it, out-of-sample from it
    -- onward; a trade is assigned by its ENTRY, because a trade belongs to the
    -- period whose data caused it to be taken.
    add column if not exists oos_start                 date,

    add column if not exists is_trades                 integer,
    add column if not exists is_net_pnl                numeric(16,4),
    add column if not exists is_win_rate_pct           numeric(6,2),
    add column if not exists is_profit_factor          numeric(10,4),
    add column if not exists is_max_drawdown_pct       numeric(6,2),
    add column if not exists is_sharpe_daily           numeric(10,4),
    add column if not exists is_symbols_profitable     integer,
    add column if not exists is_passed_kill_rules      boolean,

    add column if not exists oos_trades                integer,
    add column if not exists oos_net_pnl               numeric(16,4),
    add column if not exists oos_win_rate_pct          numeric(6,2),
    add column if not exists oos_profit_factor         numeric(10,4),
    add column if not exists oos_max_drawdown_pct      numeric(6,2),
    add column if not exists oos_sharpe_daily          numeric(10,4),
    add column if not exists oos_symbols_profitable    integer,
    add column if not exists oos_passed_kill_rules     boolean;

comment on column backtest_runs.oos_start is
    'Start of the out-of-sample period. Null means the run used no holdout - '
    'which is not the same as a strategy that failed out of sample.';

comment on column backtest_runs.oos_passed_kill_rules is
    'The verdict on data the strategy was never fitted to. When present this '
    'is the figure to trust, not passed_kill_rules, which covers the whole '
    'window including the part any tuning was done against.';

comment on column backtest_runs.oos_trades is
    'Out-of-sample trade count. A small number here makes every other oos_ '
    'column noise rather than evidence - check it before reading them.';
