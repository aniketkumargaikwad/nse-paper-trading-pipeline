"""Rescaling intraday candles onto the adjusted daily basis.

This is the riskiest code in the project: it changes prices. A wrong factor
does not error — it silently produces a different, plausible, wrong history,
and every result built on it inherits the error.

So the tests are mostly about the ways it could be wrong QUIETLY: reading
ordinary closing-auction noise as a corporate action, adjusting a symbol that
never needed it, or getting the direction of the correction backwards.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from price_adjust import (  # noqa: E402
    NEGLIGIBLE,
    STEP_THRESHOLD,
    Adjustment,
    apply_adjustments,
    daily_ratio,
    find_segments,
    material_adjustments,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def days(n: int, start=date(2020, 1, 1)) -> list[date]:
    return [start + timedelta(days=i) for i in range(n)]


def series(values, start=date(2020, 1, 1)) -> pd.Series:
    return pd.Series(values, index=days(len(values), start), dtype="float64")


def candles(n: int, price: float = 100.0, volume: float = 1000.0,
            start=datetime(2020, 1, 1, 9, 15, tzinfo=IST)) -> pd.DataFrame:
    """One candle per DAY, so the date masks in apply_adjustments are exercised."""
    index = pd.DatetimeIndex(
        [(start + timedelta(days=i)).astimezone(UTC) for i in range(n)], name="ts")
    return pd.DataFrame(
        {"open": price, "high": price * 1.01, "low": price * 0.99,
         "close": price, "volume": volume},
        index=index,
    )


# --- measuring the gap ------------------------------------------------------


def test_the_ratio_is_daily_over_intraday() -> None:
    """A 1:10 split leaves intraday ten times too high, so the correction is
    a tenth. Getting this backwards would multiply the error by 100."""
    ratio = daily_ratio(series([2178.0]), series([217.8]))
    assert ratio.iloc[0] == pytest.approx(0.1)


def test_only_days_present_in_both_are_used() -> None:
    intra = series([100.0, 100.0, 100.0])
    daily = pd.Series([100.0], index=[date(2020, 1, 2)], dtype="float64")
    assert list(daily_ratio(intra, daily).index) == [date(2020, 1, 2)]


def test_zero_or_negative_prices_are_skipped() -> None:
    """A zero would make the ratio infinite and poison the segment median."""
    intra = series([100.0, 0.0, 100.0])
    daily = series([100.0, 100.0, 100.0])
    assert len(daily_ratio(intra, daily)) == 2


def test_no_overlap_gives_nothing_rather_than_raising() -> None:
    daily = pd.Series([100.0], index=[date(2021, 5, 5)], dtype="float64")
    assert daily_ratio(series([100.0]), daily).empty


# --- finding the steps ------------------------------------------------------


def test_a_flat_ratio_is_a_single_segment() -> None:
    segments = find_segments(series([1.0] * 20))
    assert len(segments) == 1
    assert segments[0].price_factor == pytest.approx(1.0)


def test_a_split_is_found_as_two_segments() -> None:
    # Ten days needing a tenth, then ten days needing nothing.
    segments = find_segments(series([0.1] * 10 + [1.0] * 10))
    assert len(segments) == 2
    assert segments[0].price_factor == pytest.approx(0.1)
    assert segments[1].price_factor == pytest.approx(1.0)


def test_two_corporate_actions_give_three_segments() -> None:
    """WIPRO's real shape: a bonus, then another adjustment two years later,
    so the ratio steps twice and never returns to its first level."""
    segments = find_segments(series([0.375] * 10 + [0.75] * 10 + [1.0] * 10))
    assert [round(s.price_factor, 3) for s in segments] == [0.375, 0.75, 1.0]


def test_closing_auction_noise_is_not_mistaken_for_a_split() -> None:
    """Measured on clean symbols, the daily gap moves up to ~1.1%. Reading
    that as a corporate action would rescale a year of candles for nothing."""
    rng = np.random.default_rng(11)
    noisy = 1.0 + rng.normal(0.0, 0.004, size=200)
    noisy = np.clip(noisy, 0.989, 1.011)
    segments = find_segments(series(list(noisy)))
    assert len(segments) == 1, "noise was read as a corporate action"
    assert segments[0].price_factor == pytest.approx(1.0, abs=0.005)


def test_the_median_ignores_a_single_wild_day() -> None:
    values = [0.5] * 10
    values[4] = 0.9           # one bad print
    segments = find_segments(series(values), threshold=0.9)
    assert segments[0].price_factor == pytest.approx(0.5)


def test_a_very_short_plateau_is_not_trusted() -> None:
    """Two events days apart would otherwise leave a two-day 'segment' whose
    median is meaningless."""
    segments = find_segments(series([0.25, 0.5] + [1.0] * 10), threshold=0.1)
    assert all(s.sample_days >= 3 for s in segments)


def test_volume_is_measured_over_the_price_segments() -> None:
    """A split moves both at once. Segmenting them separately could adjust
    price on a day where volume was left alone."""
    price = series([0.1] * 10 + [1.0] * 10)
    volume = series([10.0] * 10 + [1.0] * 10)
    segments = find_segments(price, volume)
    assert segments[0].volume_factor == pytest.approx(10.0)
    assert segments[1].volume_factor == pytest.approx(1.0)


def test_a_demerger_leaves_volume_alone() -> None:
    """Price drops, share count does not change — so volume must not be
    touched, unlike a split."""
    price = series([0.6] * 10 + [1.0] * 10)
    volume = series([1.0] * 20)
    segments = find_segments(price, volume)
    assert segments[0].price_factor == pytest.approx(0.6)
    assert segments[0].volume_factor == pytest.approx(1.0)


def test_an_empty_history_gives_no_segments() -> None:
    assert find_segments(pd.Series(dtype="float64")) == []


# --- deciding what is worth correcting --------------------------------------


def test_a_factor_of_one_is_not_worth_applying() -> None:
    segments = find_segments(series([1.0] * 20))
    assert material_adjustments(segments) == []


def test_a_real_split_is_worth_applying() -> None:
    segments = find_segments(series([0.5] * 10 + [1.0] * 10))
    assert len(material_adjustments(segments)) == 1


def test_a_tiny_drift_is_left_alone() -> None:
    """Below the threshold, correcting adds rounding noise to every candle
    and removes nothing."""
    segments = find_segments(series([1.0 + NEGLIGIBLE / 2] * 20))
    assert material_adjustments(segments) == []


# --- applying it ------------------------------------------------------------


def test_prices_are_scaled_inside_the_period() -> None:
    frame = candles(10, price=2000.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 5), 0.5, 1.0, 5)])
    assert adjusted["close"].iloc[0] == pytest.approx(1000.0)
    assert adjusted["open"].iloc[0] == pytest.approx(1000.0)
    assert adjusted["high"].iloc[0] == pytest.approx(2000.0 * 1.01 * 0.5)


def test_candles_outside_the_period_are_untouched() -> None:
    frame = candles(10, price=2000.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 5), 0.5, 1.0, 5)])
    assert adjusted["close"].iloc[-1] == pytest.approx(2000.0)


def test_volume_is_left_alone_entirely() -> None:
    """Deliberate. A split really does change volume, but the measured volume
    gap turned out to track feed changes rather than corporate actions — see
    the note on Adjustment. Scaling it would have doubled nine years of
    RELIANCE volume for no reason."""
    frame = candles(10, volume=500.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 5), 0.5, 2.0, 5)])
    assert adjusted["volume"].iloc[0] == pytest.approx(500.0)
    assert adjusted["volume"].iloc[-1] == pytest.approx(500.0)


def test_the_high_low_ordering_survives() -> None:
    """Scaling must not turn a valid candle into an impossible one."""
    frame = candles(6, price=1500.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 6), 0.1, 10.0, 6)])
    assert (adjusted["high"] >= adjusted["close"]).all()
    assert (adjusted["low"] <= adjusted["close"]).all()
    assert (adjusted["high"] >= adjusted["low"]).all()


def test_the_caller_s_frame_is_not_mutated() -> None:
    """Reads are cached and shared; mutating in place would corrupt whatever
    else is holding the same frame."""
    frame = candles(5, price=1000.0)
    before = frame["close"].copy()
    apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 5), 0.5, 1.0, 5)])
    pd.testing.assert_series_equal(frame["close"], before)


def test_no_adjustments_returns_the_frame_unchanged() -> None:
    frame = candles(5)
    assert apply_adjustments(frame, []) is frame


def test_an_immaterial_adjustment_changes_nothing() -> None:
    frame = candles(5, price=100.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 5), 1.0, 1.0, 5)])
    assert adjusted["close"].iloc[0] == pytest.approx(100.0)


# --- the property that matters ----------------------------------------------


def test_a_split_stops_looking_like_a_crash() -> None:
    """The whole point. Before adjustment the overnight move is -90%; after
    it, the two days are continuous."""
    pre = candles(5, price=21780.0, start=datetime(2020, 8, 17, 9, 15, tzinfo=IST))
    post = candles(5, price=2178.0, start=datetime(2020, 8, 24, 9, 15, tzinfo=IST))
    frame = pd.concat([pre, post])

    raw_move = frame["close"].iloc[5] / frame["close"].iloc[4] - 1
    assert raw_move < -0.85, "precondition: the raw data shows a crash"

    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 8, 17), date(2020, 8, 21), 0.1, 10.0, 5)])
    fixed_move = adjusted["close"].iloc[5] / adjusted["close"].iloc[4] - 1
    assert abs(fixed_move) < 0.01, f"still a fake jump of {fixed_move:.1%}"


# --- not fragmenting on noise -----------------------------------------------


def test_neighbouring_periods_that_agree_are_merged() -> None:
    """A noisy symbol trips the step test again and again without anything
    happening. BHARTIARTL produced 23 periods all around 0.90 where the truth
    is about three, and each fragment's slightly different median would add
    small jumps of its own."""
    # BHARTIARTL's actual fragments, which differ from each other by ~0.3%.
    wobble = ([0.90179] * 4 + [0.90061] * 4 + [0.90283] * 4
              + [0.90299] * 4 + [0.90150] * 4)
    segments = find_segments(series(wobble), threshold=0.001)
    assert len(segments) == 1, "still fragmenting on noise"
    assert segments[0].price_factor == pytest.approx(0.9019, abs=0.002)


def test_a_real_step_still_separates_after_merging() -> None:
    segments = find_segments(series([0.5] * 10 + [1.0] * 10))
    assert len(segments) == 2


def test_merging_weights_by_length() -> None:
    """A three-day fragment must not outvote a long plateau."""
    long_then_short = [0.50] * 30 + [0.505] * 3
    segments = find_segments(series(long_then_short), threshold=0.005)
    assert len(segments) == 1
    # 30 days at 0.50 against 3 at 0.505 lands near 0.5005, not near 0.5025.
    assert segments[0].price_factor == pytest.approx(0.5005, abs=0.0005)


def test_volume_is_never_applied_even_when_measured() -> None:
    """RELIANCE's volume gap is 0.50 over nine years and 1.01 today, with no
    price change at all — the feeds simply count volume differently. Applying
    it would have doubled nine years of volume for no reason."""
    frame = candles(6, price=100.0, volume=1000.0)
    adjusted = apply_adjustments(frame, [Adjustment(
        date(2020, 1, 1), date(2020, 1, 6), 0.5, 2.0, 6)])
    assert adjusted["close"].iloc[0] == pytest.approx(50.0)
    assert adjusted["volume"].iloc[0] == pytest.approx(1000.0), "volume was scaled"
