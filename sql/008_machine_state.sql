-- ---------------------------------------------------------------------------
-- Where a v3 state machine is, between runs.
--
-- The paper engine is deliberately stateless: it wakes every 15 minutes,
-- rebuilds its whole world from this database, acts, and exits. That works for
-- a v2 strategy because a boolean-per-candle depends on nothing but the
-- candles.
--
-- A state machine is the opposite. "I swept the low at 09:45 and I am waiting
-- for a confirming candle, with swept_low = 97.30" has to survive until the
-- next tick, and there is nowhere to put it. `positions` cannot hold it: while
-- the machine is hunting a setup there IS no position, and that is precisely
-- when the state matters most.
--
-- Without this table the engine would have to restart every machine from its
-- initial state on every run. A machine that needs three candles to reach an
-- entry would then never reach one - and would report nothing, cleanly,
-- forever. Exactly the silent-nothing failure this codebase keeps refusing.
--
-- last_candle_ts is what makes a re-run safe. The engine is idempotent by
-- design (a retried tick must not double-count), so a machine must be stepped
-- ONCE per closed candle. Storing which candle it last saw makes a repeat run
-- a no-op instead of advancing the machine twice on the same bar.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

create table if not exists machine_state (
    id                bigserial primary key,
    strategy_name     text        not null,
    instrument        text        not null,
    -- The state the machine is in ON ENTRY to the next candle.
    state             text        not null,
    -- Variables captured by `set:`, as {name: number}. jsonb rather than
    -- columns because which variables exist is decided by the strategy
    -- document, not by this schema.
    variables         jsonb       not null default '{}'::jsonb,
    -- Candles spent in the current state, for `timeout:`.
    bars_in_state     integer     not null default 0,
    -- The newest closed candle this machine has already been stepped on.
    -- A run that sees the same candle again must not step it a second time.
    last_candle_ts    timestamptz,
    updated_at        timestamptz not null default now(),

    constraint machine_state_unique unique (strategy_name, instrument)
);

comment on table machine_state is
    'Where each v3 state machine is between stateless paper-engine runs. '
    'Deleted when a machine returns to its initial state with no variables, '
    'so the table holds only machines actually mid-setup.';

comment on column machine_state.last_candle_ts is
    'Guards idempotency: the engine steps a machine once per closed candle, '
    'so a re-run on the same candle is a no-op rather than a double advance.';

create index if not exists machine_state_strategy_idx
    on machine_state (strategy_name);

alter table machine_state enable row level security;

-- Same posture as `positions`: the engine writes with the service-role key,
-- and the dashboard may read to show where a machine is. No anon writes.
drop policy if exists machine_state_anon_read on machine_state;
create policy machine_state_anon_read
    on machine_state for select
    to anon
    using (true);
