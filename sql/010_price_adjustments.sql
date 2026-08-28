-- ---------------------------------------------------------------------------
-- Putting intraday candles onto the same price basis as the daily feed.
--
-- Dhan's daily feed is adjusted for corporate actions; its intraday feed is
-- raw. So EICHERMOT's 5-minute candles fall from 21,780 to 2,178 overnight in
-- August 2020 — a 90% collapse that never happened, it was a 1:10 split.
-- Forty-nine of the 200 stored symbols carry at least one such break, and
-- mid-caps are the worse half: NIFTY50 accounts for 21 of the 66 corrections,
-- the 150 symbols added for NIFTY200 for the other 45.
--
-- WHY A TABLE RATHER THAN REWRITING THE CANDLES
-- ---------------------------------------------
-- Because a corrected price is indistinguishable from a real one. Multiplying
-- 378,000 stored candles would leave no way to tell what had been changed, no
-- way to check the factor was right, and no way back.
--
-- These rows are applied when candles are READ. The raw feed stays exactly as
-- Dhan sent it, the correction is a number anybody can look at and argue
-- with, and deleting a row undoes it completely.
--
-- WHAT THE NUMBERS MEAN
-- ---------------------
-- price_factor multiplies open/high/low/close for candles in [effective_from,
-- effective_to]. It is measured as the median of daily_close / intraday_close
-- across the period — that gap IS the cumulative adjustment still owed,
-- because the daily feed is adjusted to today's basis.
--
-- volume_factor is RECORDED BUT NEVER APPLIED. A split does change volume,
-- but the measured volume gap turned out to track feed changes rather than
-- corporate actions: RELIANCE's is 0.50 across nine years and 1.01 over the
-- last one, with no price change at all. Applying it would have doubled nine
-- years of volume for nothing. It is kept because it is evidence about what
-- kind of event this was — a split moves it, a demerger does not.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

create table if not exists price_adjustments (
    id                bigserial primary key,
    instrument_id     bigint      not null references instruments(id),
    timeframe         text        not null check (timeframe in ('1m', '5m')),

    -- Inclusive on both ends, in IST trading dates.
    effective_from    date        not null,
    effective_to      date        not null,

    price_factor      double precision not null check (price_factor > 0),
    volume_factor     double precision,

    -- How the factor was arrived at, so a surprising number can be judged
    -- rather than merely trusted.
    sample_days       integer     not null,
    method            text        not null default 'median_daily_ratio',
    detected_at       timestamptz not null default now(),
    note              text,

    constraint price_adjustments_window check (effective_from <= effective_to),
    constraint price_adjustments_unique
        unique (instrument_id, timeframe, effective_from)
);

comment on table price_adjustments is
    'Rescales raw intraday candles onto the corporate-action-adjusted basis '
    'of the daily feed. Applied on READ; the stored candles are never '
    'modified, so deleting a row fully undoes its correction.';

comment on column price_adjustments.volume_factor is
    'Recorded as evidence, never applied. The measured volume gap tracks feed '
    'changes rather than corporate actions - RELIANCE is 0.50 over nine years '
    'and 1.01 over the last one, with no price change.';

create index if not exists price_adjustments_lookup
    on price_adjustments (instrument_id, timeframe, effective_from);

alter table price_adjustments enable row level security;

-- Same posture as the rest: the engines write with the service-role key, the
-- dashboard may read so a correction can be seen rather than guessed at.
drop policy if exists price_adjustments_anon_read on price_adjustments;
create policy price_adjustments_anon_read
    on price_adjustments for select
    to anon
    using (true);
