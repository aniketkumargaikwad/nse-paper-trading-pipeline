"""Tests for recovering missing candles from a second feed.

The frames here are built by hand rather than recorded, because every case
that matters is a SHAPE - a stub candle at the bell, a feed on a different
price basis, a session the provider skipped - and a recorded response only
ever holds the shape it happened to have on the day it was captured.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from price_recovery import (  # noqa: E402
    EXPECTED_BARS,
    MAX_RATIO_DISPERSION,
    JOIN_BREAK_RATIO,
    MIN_OVERLAP_SESSIONS,
    RecoveryError,
    check_alignment,
    earliest_recoverable,
    expires_on,
    measure_rebasing,
    apply_rebasing,
    overlap_ratios,
    recover,
    restamp_daily,
    session_bar_counts,
    session_last_close,
    trading_sessions,
    trim_to_session,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

NOW = datetime(2026, 9, 22, 4, 0, tzinfo=UTC)          # 09:30 IST
HOLIDAYS = frozenset({date(2026, 8, 15)})


# ---------------------------------------------------------------------------
# Frame builders
# ---------------------------------------------------------------------------


def session_frame(
    day: date, *, bars: int = EXPECTED_BARS["5m"], close: float = 100.0,
    first_ist: time = time(9, 15), step_minutes: int = 5,
) -> pd.DataFrame:
    """One session of intraday candles, every close identical."""
    start = datetime.combine(day, first_ist, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=step_minutes * i)).astimezone(UTC)
         for i in range(bars)],
        name="ts",
    )
    return pd.DataFrame(
        {
            "open": np.full(bars, close),
            "high": np.full(bars, close * 1.01),
            "low": np.full(bars, close * 0.99),
            "close": np.full(bars, close),
            "volume": np.full(bars, 1000.0),
        },
        index=index,
    )


def intraday(days: dict[date, float], **kwargs) -> pd.DataFrame:
    """Several sessions, keyed by date -> close."""
    parts = [session_frame(d, close=c, **kwargs) for d, c in sorted(days.items())]
    return pd.concat(parts).sort_index()


def daily(days: dict[date, float], at_ist: time = time(15, 30)) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime.combine(d, at_ist, tzinfo=IST).astimezone(UTC)
         for d in sorted(days)],
        name="ts",
    )
    closes = [days[d] for d in sorted(days)]
    return pd.DataFrame(
        {
            "open": closes, "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes], "close": closes,
            "volume": [1000.0] * len(closes),
        },
        index=index,
    )


JULY = [date(2026, 7, 27), date(2026, 7, 28), date(2026, 7, 29),
        date(2026, 7, 30), date(2026, 7, 31)]
AUGUST = [date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 5)]


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------


def test_earliest_recoverable_is_an_ist_date_not_a_utc_one():
    # 2026-09-22 04:00 UTC is already the 22nd in IST; minus 58 days is the
    # 26th of July. Reading the UTC date would shift the answer by a day at
    # every hour before 18:30 UTC, and the boundary is where it matters.
    assert earliest_recoverable(NOW, 58) == date(2026, 7, 26)


def test_each_session_expires_on_its_own_date():
    # The window is rolling, so "August is available until late October" is
    # true only of the end of August. The start of it goes first.
    assert expires_on(date(2026, 8, 3), 58) == date(2026, 9, 30)
    assert expires_on(date(2026, 8, 31), 58) == date(2026, 10, 28)


def test_trading_sessions_skips_weekends_and_holidays():
    days = trading_sessions(date(2026, 8, 13), date(2026, 8, 18), HOLIDAYS)
    assert days == [date(2026, 8, 13), date(2026, 8, 14),
                    date(2026, 8, 17), date(2026, 8, 18)]
    assert date(2026, 8, 15) not in days      # Independence Day, a Saturday too
    assert date(2026, 8, 16) not in days      # Sunday


# ---------------------------------------------------------------------------
# Session shaping
# ---------------------------------------------------------------------------


def test_trim_to_session_drops_the_stub_candle_at_the_closing_bell():
    day = date(2026, 8, 3)
    good = session_frame(day, bars=EXPECTED_BARS["5m"])
    stub = session_frame(day, bars=1, first_ist=time(15, 30))
    trimmed = trim_to_session(pd.concat([good, stub]), "5m")
    assert len(trimmed) == EXPECTED_BARS["5m"]
    assert trimmed.index[-1].tz_convert(IST).time() == time(15, 25)


def test_trim_to_session_drops_a_pre_open_candle():
    day = date(2026, 8, 3)
    early = session_frame(day, bars=1, first_ist=time(9, 0))
    frame = pd.concat([early, session_frame(day, bars=3)]).sort_index()
    assert len(trim_to_session(frame, "5m")) == 3


def test_trim_to_session_leaves_daily_candles_alone():
    frame = daily({date(2026, 8, 3): 100.0})
    assert len(trim_to_session(frame, "day")) == 1


def test_session_helpers_key_on_the_ist_date():
    frame = intraday({date(2026, 8, 3): 100.0, date(2026, 8, 4): 101.0}, bars=4)
    assert list(session_last_close(frame).index) == [date(2026, 8, 3), date(2026, 8, 4)]
    assert list(session_bar_counts(frame)) == [4, 4]


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------


def test_alignment_accepts_two_feeds_on_the_same_five_minute_grid():
    stored = intraday({d: 100.0 for d in JULY}, bars=6)
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=6)
    assert check_alignment(stored, incoming, "5m").ok


def test_alignment_rejects_a_feed_on_a_different_grid():
    # A feed starting at 09:00 interleaves with the stored one, and every
    # resampled 15/30/60-minute bar would then be built from a mixture.
    stored = intraday({d: 100.0 for d in JULY}, bars=6)
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=6, first_ist=time(9, 0))
    alignment = check_alignment(stored, incoming, "5m")
    assert not alignment.ok
    assert "different grids" in alignment.note


def test_daily_alignment_reports_the_stored_time_of_day_to_restamp_onto():
    # The trap: Dhan stamps a daily candle from an epoch second and Yahoo
    # stamps it at IST midnight. Both are the right DAY, so nothing looks
    # wrong - but merged as-is the store holds two candles per session.
    stored = daily({d: 100.0 for d in JULY}, at_ist=time(15, 30))
    incoming = daily({d: 100.0 for d in AUGUST}, at_ist=time(0, 0))
    alignment = check_alignment(stored, incoming, "day")
    assert alignment.ok
    assert alignment.restamp_to == time(15, 30)


def test_daily_alignment_refuses_when_the_store_is_not_self_consistent():
    mixed = pd.concat([
        daily({date(2026, 7, 27): 100.0}, at_ist=time(15, 30)),
        daily({date(2026, 7, 28): 100.0}, at_ist=time(0, 0)),
    ]).sort_index()
    alignment = check_alignment(mixed, daily({date(2026, 8, 3): 100.0}), "day")
    assert not alignment.ok
    assert "one time of day" in alignment.note


def test_restamp_daily_moves_candles_without_changing_their_session():
    frame = daily({d: 100.0 for d in AUGUST}, at_ist=time(0, 0))
    moved = restamp_daily(frame, time(15, 30))
    assert [ts.tz_convert(IST).date() for ts in moved.index] == AUGUST
    assert {ts.tz_convert(IST).time() for ts in moved.index} == {time(15, 30)}


# ---------------------------------------------------------------------------
# Rebasing
# ---------------------------------------------------------------------------


def test_overlap_ratios_only_covers_sessions_both_feeds_hold():
    stored = intraday({d: 200.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY[2:]},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    ratios = overlap_ratios(stored, incoming)
    assert list(ratios.index) == JULY[2:]
    assert ratios.tolist() == pytest.approx([2.0, 2.0, 2.0])


def test_rebasing_measures_the_basis_difference_between_the_feeds():
    # Yahoo is dividend-adjusted to today; the store is raw. A constant 3%
    # gap across the shared sessions is exactly that difference.
    stored = intraday({d: 103.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    rebasing = measure_rebasing(stored, incoming)
    assert rebasing.ok
    assert rebasing.method == "overlap"
    assert rebasing.factor == pytest.approx(1.03)
    assert rebasing.sessions == len(JULY)
    assert rebasing.is_material


def test_rebasing_refuses_when_too_few_sessions_are_shared():
    # This is the deadline in code form: the overlap is made of sessions the
    # store already holds, and it shrinks by one a day.
    shared = JULY[-(MIN_OVERLAP_SESSIONS - 1):]
    stored = intraday({d: 100.0 for d in shared}, bars=3)
    incoming = intraday({**{d: 100.0 for d in shared},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    assert "does not get better by waiting" in rebasing.reason


def test_rebasing_refuses_when_the_ratios_do_not_agree():
    # A corporate action inside the overlap: the ratio steps part-way
    # through, so no single factor describes it and the median would be an
    # average of two different bases.
    stored = intraday({JULY[0]: 100.0, JULY[1]: 100.0, JULY[2]: 100.0,
                       JULY[3]: 200.0, JULY[4]: 200.0}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    assert "shifts" in rebasing.reason
    assert "2026-07-30" in rebasing.reason      # names the session it moved on


def test_apply_rebasing_scales_prices_and_never_volume():
    # A split really does change volume, but the measured volume gap between
    # two feeds tracks how they COUNT volume - see price_adjust.Adjustment.
    frame = intraday({d: 100.0 for d in AUGUST}, bars=3)
    stored = intraday({d: 110.0 for d in JULY}, bars=3)
    with_overlap = pd.concat([intraday({d: 100.0 for d in JULY}, bars=3), frame])
    rebasing = measure_rebasing(stored, with_overlap)
    out = apply_rebasing(frame, rebasing)
    assert out["close"].tolist() == pytest.approx([110.0] * len(frame))
    assert out["volume"].tolist() == frame["volume"].tolist()


def test_apply_rebasing_leaves_an_immaterial_factor_untouched():
    # Multiplying by 1.0000003 and rounding is not a correction, it is noise
    # written into every candle.
    frame = intraday({d: 100.0 for d in AUGUST}, bars=3)
    stored = intraday({d: 100.05 for d in JULY}, bars=3)
    with_overlap = pd.concat([intraday({d: 100.0 for d in JULY}, bars=3), frame])
    rebasing = measure_rebasing(stored, with_overlap)
    assert rebasing.ok and not rebasing.is_material
    assert apply_rebasing(frame, rebasing) is frame


def test_apply_rebasing_will_not_be_forced_past_a_refusal():
    frame = intraday({d: 100.0 for d in AUGUST}, bars=3)
    refused = measure_rebasing(pd.DataFrame(), frame)
    with pytest.raises(RecoveryError):
        apply_rebasing(frame, refused)


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


def run_recover(stored, incoming, *, expected=None, earliest=date(2026, 7, 26),
                timeframe="5m", invalid=()):
    return recover(
        "NSE:RELIANCE", timeframe, stored, incoming,
        expected_sessions=expected if expected is not None else AUGUST,
        earliest_available=earliest,
        invalid_timestamps=invalid,
    )


def test_recovery_appends_only_what_comes_after_the_stored_tail():
    stored = intraday({d: 103.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    fresh, report = run_recover(stored, incoming)

    assert report.ok
    assert report.candles == 3 * len(AUGUST)
    assert list(session_bar_counts(fresh).index) == AUGUST
    # The stored sessions were used as evidence and then declined, not merged.
    assert report.duplicates_declined == 3 * len(JULY)
    # And they came back on the stored basis, not Yahoo's.
    assert fresh["close"].iloc[0] == pytest.approx(103.0)


def test_recovery_never_lets_the_second_feed_restate_a_stored_session():
    # The store's own candles stay the record: a backtest has already run on
    # them, and a silent restatement changes results with nothing to show why.
    stored = intraday({d: 103.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY},
                         **{d: 100.0 for d in AUGUST}}, bars=3)
    fresh, _ = run_recover(stored, incoming)
    assert fresh.index.min() > stored.index.max()


def test_recovery_reports_a_session_the_provider_skipped():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    served = {**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST[:-1]}}
    fresh, report = run_recover(stored, intraday(served, bars=3))
    assert report.sessions_missing == (AUGUST[-1],)
    assert report.candles == 3 * (len(AUGUST) - 1)


def test_recovery_reports_a_thin_session_rather_than_hiding_it():
    stored = intraday({d: 100.0 for d in JULY}, bars=EXPECTED_BARS["5m"])
    full = intraday({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST[:-1]}},
                    bars=EXPECTED_BARS["5m"])
    thin = session_frame(AUGUST[-1], bars=10)
    _, report = run_recover(stored, pd.concat([full, thin]))
    assert report.thin_sessions == {AUGUST[-1]: 10}


def test_recovery_drops_candles_outside_the_session_and_says_how_many():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    incoming = pd.concat([
        intraday({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST}}, bars=3),
        session_frame(AUGUST[-1], bars=1, first_ist=time(15, 30)),
    ]).sort_index()
    _, report = run_recover(stored, incoming)
    assert report.out_of_session_dropped == 1


def test_recovery_drops_the_candles_the_caller_found_impossible():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST}},
                        bars=3)
    doomed = [incoming.index[-1].to_pydatetime()]
    fresh, report = run_recover(stored, incoming, invalid=doomed)
    assert report.invalid_dropped == 1
    assert doomed[0] not in fresh.index


def test_recovery_warns_about_a_step_at_the_join_the_overlap_did_not_explain():
    # A corporate action inside the gap has no shared session to be measured
    # from, so it survives rebasing and shows up here.
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY},
                         **{d: 50.0 for d in AUGUST}}, bars=3)
    _, report = run_recover(stored, incoming)
    assert report.join_ratio == pytest.approx(0.5)
    assert abs(report.join_ratio - 1.0) > JOIN_BREAK_RATIO
    assert "WARNING" in report.render()


def test_recovery_refuses_rather_than_guessing_when_there_is_no_overlap():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=3)
    fresh, report = run_recover(stored, incoming)
    assert fresh.empty
    assert not report.ok
    assert report.candles == 0


def test_recovery_reports_sessions_already_past_the_providers_window():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    incoming = intraday({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST}},
                        bars=3)
    lost = [date(2026, 6, 1), date(2026, 6, 2)]
    _, report = run_recover(stored, incoming, expected=[*lost, *AUGUST])
    assert report.sessions_unreachable == tuple(lost)
    # Unreachable is not the same as missing: nobody could have fetched these.
    assert not set(lost) & set(report.sessions_missing)


def test_recovery_of_an_empty_response_is_reported_not_silent():
    stored = intraday({d: 100.0 for d in JULY}, bars=3)
    fresh, report = run_recover(stored, pd.DataFrame())
    assert fresh.empty
    assert "no candles at all" in report.problems[0]


def test_daily_recovery_lands_on_the_stored_time_of_day():
    stored = daily({d: 100.0 for d in JULY}, at_ist=time(15, 30))
    incoming = daily({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST}},
                     at_ist=time(0, 0))
    fresh, report = run_recover(stored, incoming, timeframe="day")
    assert report.ok
    assert {ts.tz_convert(IST).time() for ts in fresh.index} == {time(15, 30)}
    assert [ts.tz_convert(IST).date() for ts in fresh.index] == AUGUST


def test_daily_recovery_does_not_call_one_candle_a_day_thin():
    stored = daily({d: 100.0 for d in JULY})
    incoming = daily({**{d: 100.0 for d in JULY}, **{d: 100.0 for d in AUGUST}})
    _, report = run_recover(stored, incoming, timeframe="day")
    assert report.thin_sessions == {}


def test_alignment_does_not_punish_a_store_with_a_short_last_session():
    # The check is against the session grid, not against whatever the stored
    # sample happens to contain. Comparing the two sets directly refused any
    # symbol whose stored tail was short - a refusal against a deadline, for
    # nothing.
    stored = intraday({d: 100.0 for d in JULY}, bars=6)          # 09:15-09:40
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=EXPECTED_BARS["5m"])
    assert check_alignment(stored, incoming, "5m").ok


def test_alignment_rejects_an_offset_inside_the_session_but_off_the_grid():
    stored = intraday({d: 100.0 for d in JULY}, bars=6)
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=6, first_ist=time(9, 17))
    alignment = check_alignment(stored, incoming, "5m")
    assert not alignment.ok
    assert "grid anchored at 09:15" in alignment.note


def test_alignment_rejects_a_grid_the_resampler_does_not_know():
    stored = intraday({d: 100.0 for d in JULY}, bars=6)
    incoming = intraday({d: 100.0 for d in AUGUST}, bars=6)
    alignment = check_alignment(stored, incoming, "15m")
    assert not alignment.ok
    assert "not a stored intraday timeframe" in alignment.note


def test_the_join_session_is_not_called_thin_for_being_split_across_feeds():
    # The store holds the morning of the join session and the recovery brings
    # the rest. Counting only what came back flagged every symbol's first
    # recovered session as thin - 200 false flags on one day, which is how a
    # real thin session goes unnoticed.
    join = date(2026, 8, 3)
    morning = session_frame(join, bars=20)
    stored = pd.concat([intraday({d: 100.0 for d in JULY}, bars=EXPECTED_BARS["5m"]),
                        morning]).sort_index()
    rest = session_frame(join, bars=EXPECTED_BARS["5m"])   # whole session
    incoming = pd.concat([
        intraday({d: 100.0 for d in JULY}, bars=EXPECTED_BARS["5m"]), rest,
    ]).sort_index()

    fresh, report = run_recover(stored, incoming, expected=[join])
    assert len(fresh) == EXPECTED_BARS["5m"] - 20      # only the afternoon is new
    assert report.thin_sessions == {}                  # together they are a full day


def test_a_genuinely_thin_session_is_still_reported_after_the_join_fix():
    join = date(2026, 8, 3)
    stored = pd.concat([intraday({d: 100.0 for d in JULY}, bars=EXPECTED_BARS["5m"]),
                        session_frame(join, bars=5)]).sort_index()
    incoming = pd.concat([
        intraday({d: 100.0 for d in JULY}, bars=EXPECTED_BARS["5m"]),
        session_frame(join, bars=20),
    ]).sort_index()
    _, report = run_recover(stored, incoming, expected=[join])
    assert report.thin_sessions == {join: 20}          # 5 stored + 15 recovered


def _ratio_case(ratios: list[float]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A stored/incoming pair whose per-session close ratios are `ratios`."""
    days: list[date] = []
    day = date(2026, 7, 1)
    while len(days) < len(ratios):
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    stored = intraday({d: 100.0 * r for d, r in zip(days, ratios)}, bars=3)
    incoming = pd.concat([
        intraday({d: 100.0 for d in days}, bars=3),
        intraday({d: 100.0 for d in AUGUST}, bars=3),
    ]).sort_index()
    return stored, incoming


def test_a_wobbly_but_centred_overlap_is_accepted_not_refused():
    # Two feeds always price a session's close slightly differently. That is
    # scatter about one basis, and the median is the right estimator for it -
    # the earlier range test called this a corporate action and refused.
    stored, incoming = _ratio_case([1.000, 1.004, 0.996, 1.003, 0.997, 1.002,
                                    0.998, 1.005, 0.995, 1.001])
    rebasing = measure_rebasing(stored, incoming)
    assert rebasing.ok
    assert rebasing.factor == pytest.approx(1.0, abs=0.005)
    assert rebasing.dispersion < MAX_RATIO_DISPERSION


def test_more_shared_sessions_no_longer_make_refusal_more_likely():
    # The range grows with the sample, so the symbols with the BEST evidence
    # were the ones most likely to be refused. The scatter measure does not.
    noise = [1.000, 1.004, 0.996, 1.003, 0.997, 1.002, 0.998, 1.005, 0.995]
    short = measure_rebasing(*_ratio_case(noise[:5]))
    long = measure_rebasing(*_ratio_case(noise * 3))
    assert short.ok and long.ok
    assert long.dispersion == pytest.approx(short.dispersion, abs=0.004)


def test_one_odd_session_does_not_condemn_the_whole_symbol():
    # A single halted or thinly-traded close used to set max/min by itself.
    stored, incoming = _ratio_case([1.000, 1.001, 0.999, 1.000, 1.030,
                                    1.000, 0.999, 1.001, 1.000])
    rebasing = measure_rebasing(stored, incoming)
    assert rebasing.ok
    assert rebasing.factor == pytest.approx(1.0, abs=0.002)
    # The outlier is still visible in the range, it just does not decide.
    assert rebasing.spread > 0.02


def test_a_sustained_step_is_still_refused_however_quiet_the_rest_is():
    stored, incoming = _ratio_case([1.000, 1.001, 0.999, 1.000,
                                    1.050, 1.051, 1.049, 1.050])
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    assert "shifts" in rebasing.reason


def test_a_genuinely_scattered_overlap_is_still_refused():
    # No step, but the ratios do not describe one basis at all.
    stored, incoming = _ratio_case([1.00, 1.02, 0.97, 1.03, 0.98, 1.04, 0.96])
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    assert "scatter" in rebasing.reason


def test_a_clean_step_is_reported_as_a_step_not_as_scatter():
    # A step inflates the overall scatter - the median sits between the two
    # levels - so a scatter-first check labelled every corporate action as
    # noise. The step wins when each side is tight about its own level.
    stored, incoming = _ratio_case([1.000, 1.001, 0.999, 1.000,
                                    1.050, 1.051, 1.049, 1.050])
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    assert "shifts" in rebasing.reason
    assert "scatter" not in rebasing.reason


def test_the_reported_step_date_is_where_the_level_actually_changes():
    stored, incoming = _ratio_case([1.00, 1.00, 1.00, 1.00,
                                    1.08, 1.08, 1.08, 1.08])
    rebasing = measure_rebasing(stored, incoming)
    assert not rebasing.ok
    # Fifth weekday from 1 Jul 2026: 1,2,3 Jul then 6,7 Jul.
    assert "2026-07-07" in rebasing.reason
