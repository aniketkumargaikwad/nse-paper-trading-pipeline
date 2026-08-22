-- ---------------------------------------------------------------------------
-- Allow the 1-minute timeframe.
--
-- Four CHECK constraints hard-coded the storable and strategy timeframes when
-- the store held only a 5m base and an adjusted daily series. 1m cannot be
-- derived from either — nothing resamples downward — so it is fetched and
-- stored on its own, and every one of those lists has to admit it.
--
-- 1m is deliberately NOT made the base for 5m and above. Re-deriving them
-- from 1m would be tidier in the abstract and would silently change every
-- stored 5m candle and every result computed from one, so the two series stay
-- independent and each is fetched from the provider on its own terms.
--
-- Apply in the Supabase SQL editor (the Python client cannot run DDL).
-- ---------------------------------------------------------------------------

-- Stored candle series. '1m' joins '5m' and 'day' as a thing we persist
-- rather than derive.
alter table candles
    drop constraint if exists candles_timeframe_check;
alter table candles
    add constraint candles_timeframe_check
    check (timeframe = any (array['1m', '5m', 'day']));

alter table candle_coverage
    drop constraint if exists candle_coverage_timeframe_check;
alter table candle_coverage
    add constraint candle_coverage_timeframe_check
    check (timeframe = any (array['1m', '5m', 'day']));

alter table data_quality_flags
    drop constraint if exists data_quality_flags_timeframe_check;
alter table data_quality_flags
    add constraint data_quality_flags_timeframe_check
    check (timeframe = any (array['1m', '5m', 'day']));

-- Timeframes a STRATEGY may declare. This list is wider than the stored one
-- because 15m/25m/30m/60m are resampled on read.
alter table strategies
    drop constraint if exists strategies_timeframe_check;
alter table strategies
    add constraint strategies_timeframe_check
    check (
        timeframe is null
        or timeframe = any (array['1m', '5m', '15m', '25m', '30m', '60m', 'day'])
    );

comment on constraint candles_timeframe_check on candles is
    'Only timeframes that are STORED rather than derived. 15m and above are '
    'resampled from the 5m base on read, so storing them would be duplication '
    'that can drift.';
