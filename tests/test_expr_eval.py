"""Evaluating a v3 expression AST against real candles.

The parser guarantees an expression is well-formed. This layer decides what
the names MEAN, and it is where two classes of bug would live: a name that
silently resolves to nothing (returning NaN forever, so a condition is simply
never true and nobody notices), and a higher-timeframe or structural reference
that leaks a bar from the future.

Both are tested explicitly rather than trusted.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from expr_eval import EvaluationError, evaluate  # noqa: E402
from strategy.expr import parse_expression  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def frame(closes, opens=None, highs=None, lows=None, volumes=None, step_min=15,
          start=None) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    start = start or datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(n)],
        name="ts",
    )
    return pd.DataFrame(
        {
            "open": np.asarray(opens, dtype=float) if opens is not None else closes,
            "high": np.asarray(highs, dtype=float) if highs is not None else closes + 1,
            "low": np.asarray(lows, dtype=float) if lows is not None else closes - 1,
            "close": closes,
            "volume": np.asarray(volumes, dtype=float) if volumes is not None
            else np.full(n, 1000.0),
        },
        index=index,
    )


def run(source: str, df: pd.DataFrame, **kwargs) -> pd.Series:
    return evaluate(parse_expression(source), df, **kwargs)


# --- price series -----------------------------------------------------------


def test_a_price_series_resolves() -> None:
    df = frame([100, 101, 102])
    assert list(run("close", df)) == [100.0, 101.0, 102.0]


def test_arithmetic_on_series() -> None:
    df = frame([100, 200])
    assert list(run("close / 2", df)) == [50.0, 100.0]


def test_series_arithmetic_with_two_series() -> None:
    df = frame([100, 200], highs=[110, 220], lows=[90, 180])
    assert list(run("high - low", df)) == [20.0, 40.0]


def test_the_audit_abc_expression_evaluates() -> None:
    """`(B * C) / A` with the three levels held as variables."""
    df = frame([1, 1])
    out = run("(b * c) / a", df, variables={"a": 2.0, "b": 6.0, "c": 4.0})
    assert list(out) == [12.0, 12.0]


def test_precedence_is_honoured_numerically() -> None:
    df = frame([0])
    assert run("1 + 2 * 3", df).iloc[0] == 7.0
    assert run("(1 + 2) * 3", df).iloc[0] == 9.0


# --- comparisons and booleans ----------------------------------------------


def test_comparison_yields_booleans() -> None:
    df = frame([100, 110])
    assert list(run("close > 105", df)) == [False, True]


def test_and_or_not() -> None:
    df = frame([100, 110, 120])
    assert list(run("close > 105 and close < 115", df)) == [False, True, False]
    assert list(run("close < 105 or close > 115", df)) == [True, False, True]
    assert list(run("not close > 105", df)) == [True, False, False]


def test_nan_never_reads_as_true() -> None:
    """The rule the whole engine depends on: absence is not a signal."""
    df = frame([100, 101, 102])
    # rsi(14) cannot have a value this early, so every bar must be False.
    assert not run("rsi(14) > 0", df).any()


# --- candle vocabulary ------------------------------------------------------


def test_candle_direction() -> None:
    df = frame([102, 98], opens=[100, 100])
    assert list(run("candle.is_bullish", df)) == [True, False]
    assert list(run("candle.is_bearish", df)) == [False, True]


def test_candle_body_and_range() -> None:
    df = frame([104], opens=[100], highs=[110], lows=[96])
    assert run("candle.body", df).iloc[0] == 4.0
    assert run("candle.range", df).iloc[0] == 14.0


def test_a_doji_is_neither_bullish_nor_bearish() -> None:
    df = frame([100], opens=[100])
    assert run("candle.is_bullish", df).iloc[0] is np.False_
    assert run("candle.is_bearish", df).iloc[0] is np.False_


# --- indicators -------------------------------------------------------------


def test_an_indicator_call_resolves() -> None:
    df = frame(list(range(100, 130)))
    out = run("sma(3)", df)
    assert out.iloc[3] == pytest.approx((101 + 102 + 103) / 3)


def test_a_moving_average_over_another_series() -> None:
    """`sma(volume, 3)` — how "above average volume" is written."""
    df = frame([100] * 6, volumes=[10, 20, 30, 40, 50, 60])
    out = run("sma(volume, 3)", df)
    assert out.iloc[2] == pytest.approx(20.0)


def test_a_multi_output_indicator_uses_a_dotted_call() -> None:
    df = frame(list(range(100, 160)))
    line = run("macd.line(12, 26, 9)", df)
    signal = run("macd.signal(12, 26, 9)", df)
    assert not line.equals(signal)


def test_bollinger_bands_outputs() -> None:
    df = frame(list(range(100, 140)))
    upper = run("bbands.upper(20, 2)", df)
    lower = run("bbands.lower(20, 2)", df)
    assert (upper.dropna() > lower.dropna()).all()


def test_an_indicator_participates_in_arithmetic() -> None:
    df = frame(list(range(100, 140)))
    combined = run("atr(14) * 2", df)
    plain = run("atr(14)", df)
    pd.testing.assert_series_equal(combined, plain * 2, check_names=False)


# --- offsets ----------------------------------------------------------------


def test_offset_reads_the_previous_bar() -> None:
    df = frame([100, 110, 120])
    assert list(run("close[1]", df).fillna(-1)) == [-1.0, 100.0, 110.0]


def test_offset_on_an_indicator() -> None:
    df = frame(list(range(100, 140)))
    pd.testing.assert_series_equal(
        run("rsi(14)[1]", df).reset_index(drop=True).iloc[1:],
        run("rsi(14)", df).reset_index(drop=True).iloc[:-1].set_axis(range(1, 40)),
        check_names=False,
    )


def test_a_breakout_of_the_previous_bar_high() -> None:
    df = frame([100, 100, 105, 100], highs=[101, 101, 106, 101])
    assert list(run("close > high[1]", df)) == [False, False, True, False]


# --- variables --------------------------------------------------------------


def test_a_variable_resolves() -> None:
    df = frame([100, 110])
    assert list(run("close > level", df, variables={"level": 105.0})) == [False, True]


def test_a_variable_may_be_a_series() -> None:
    """A variable captured per bar, not a single constant."""
    df = frame([100, 110])
    captured = pd.Series([90.0, 120.0], index=df.index)
    assert list(run("close > level", df, variables={"level": captured})) == [True, False]


def test_an_unknown_name_is_an_error_not_a_nan() -> None:
    """Silently returning NaN would make the condition never fire, and look
    exactly like a strategy that simply found no setups."""
    df = frame([100])
    with pytest.raises(EvaluationError) as exc:
        run("close > nonsense", df)
    assert "nonsense" in str(exc.value)


def test_an_unknown_function_is_an_error() -> None:
    df = frame([100])
    with pytest.raises(EvaluationError) as exc:
        run("supersignal(9)", df)
    assert "supersignal" in str(exc.value)


def test_wrong_argument_count_is_an_error() -> None:
    df = frame([100])
    with pytest.raises(EvaluationError):
        run("rsi()", df)


# --- higher timeframe, and the absence of look-ahead ------------------------


def sessions(days: int, per_day: int = 25) -> pd.DataFrame:
    """`days` sessions of 15m bars, each day higher than the last."""
    rows, index = [], []
    for d in range(days):
        day_start = datetime(2026, 7, 6 + d, 9, 15, tzinfo=IST)
        for b in range(per_day):
            price = 100.0 + d * 10 + b
            rows.append(price)
            index.append((day_start + timedelta(minutes=15 * b)).astimezone(UTC))
    closes = np.asarray(rows)
    return pd.DataFrame(
        {"open": closes, "high": closes + 1, "low": closes - 1,
         "close": closes, "volume": np.full(len(closes), 1000.0)},
        index=pd.DatetimeIndex(index, name="ts"),
    )


def test_prev_day_high_is_yesterdays_high() -> None:
    df = sessions(3)
    out = run("prev_day.high", df)
    # Day 0 has no previous day.
    assert out.iloc[:25].isna().all()
    # Day 1 sees day 0's high: 100 + 24 + 1 = 125.
    assert (out.iloc[25:50] == 125.0).all()
    # Day 2 sees day 1's high: 110 + 24 + 1 = 135.
    assert (out.iloc[50:75] == 135.0).all()


def test_prev_day_high_does_not_change_during_the_session() -> None:
    """A rule that means something different at 15:15 than at 09:30 is a trap,
    even when the late value is not strictly look-ahead."""
    df = sessions(3)
    out = run("prev_day.high", df)
    for day in range(1, 3):
        chunk = out.iloc[day * 25:(day + 1) * 25]
        assert chunk.nunique() == 1


@pytest.mark.parametrize("source", [
    "prev_day.high",
    "prev_day.low",
    "close > prev_day.high",
    "rsi(14)",
    "atr(14)",
    "sma(20)",
    "vwap()",
    "close[3]",
    "macd.line(12, 26, 9)",
    "close > prev_day.high and rsi(14) > 50",
])
def test_no_expression_can_see_the_future(source: str) -> None:
    """The definition of look-ahead freedom, tested directly.

    If evaluating over the whole frame gives a bar a different answer than
    evaluating over history up to that bar, the full-frame run used data that
    did not exist yet. This is stronger than checking any particular value,
    and it holds for any expression, so new vocabulary is covered by adding a
    line here rather than reasoning about it afresh.
    """
    df = sessions(4)
    full = run(source, df)
    # Every 7th bar past warm-up: enough coverage to catch an off-by-one at a
    # session boundary without evaluating the expression a hundred times.
    for i in range(40, len(df), 7):
        truncated = run(source, df.iloc[:i + 1])
        a, b = full.iloc[i], truncated.iloc[i]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == b, f"{source} differs at bar {i}: {a} full vs {b} truncated"


def test_a_multi_timeframe_confluence_expression() -> None:
    """A daily filter and an intraday trigger in one sentence — the thing v2
    could not say at all."""
    df = sessions(3)
    out = run("close > prev_day.high and candle.is_bullish", df)
    assert out.dtype == bool
    assert out.iloc[:25].sum() == 0        # no previous day to break


# --- shape and hygiene ------------------------------------------------------


def test_result_is_always_aligned_to_the_input_index() -> None:
    df = frame([100, 101, 102])
    for source in ["close", "close > 1", "rsi(14)", "prev_day.high", "1 + 1"]:
        out = run(source, df)
        assert out.index.equals(df.index), source


def test_a_constant_expression_broadcasts() -> None:
    df = frame([100, 101, 102])
    out = run("42", df)
    assert list(out) == [42.0, 42.0, 42.0]


def test_an_empty_frame_yields_an_empty_series() -> None:
    empty = frame([]).iloc[0:0]
    assert len(run("close > 1", empty)) == 0


# --- higher-timeframe indicators --------------------------------------------


def test_a_daily_indicator_is_computed_on_daily_bars() -> None:
    """`daily.sma(2)` is a 2-DAY average, not a 2-bar one relabelled."""
    df = sessions(4)
    out = run("daily.sma(2)", df)
    # Day highs/closes rise 10 per day; each session's last close is
    # 100 + d*10 + 24. The 2-day SMA available during day 3 averages the
    # closes of days 1 and 2: (134 + 144) / 2 = 139.
    assert out.iloc[75] == pytest.approx((134 + 144) / 2)


def test_daily_close_matches_prev_day_close() -> None:
    """Two names for the same last-closed daily bar."""
    df = sessions(3)
    pd.testing.assert_series_equal(
        run("daily.close", df), run("prev_day.close", df), check_names=False
    )


def test_a_daily_indicator_holds_steady_within_a_session() -> None:
    df = sessions(4)
    out = run("daily.sma(2)", df)
    for day in range(2, 4):
        chunk = out.iloc[day * 25:(day + 1) * 25]
        assert chunk.nunique() == 1


@pytest.mark.parametrize("source", ["daily.sma(2)", "daily.rsi(2)", "daily.atr(2)"])
def test_daily_indicators_cannot_see_the_future(source: str) -> None:
    df = sessions(5)
    full = run(source, df)
    for i in range(50, len(df), 7):
        truncated = run(source, df.iloc[:i + 1])
        a, b = full.iloc[i], truncated.iloc[i]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == pytest.approx(b), f"{source} differs at bar {i}"


def test_an_offset_on_a_daily_reference_steps_back_a_day() -> None:
    """`prev_day.high[1]` is the day BEFORE yesterday, not 15 minutes ago."""
    df = sessions(4)
    out = run("prev_day.high[1]", df)
    # Day highs are 100+d*10+24+1. During day 2, prev_day.high is day 1's
    # (135) and prev_day.high[1] is day 0's (125).
    assert (out.iloc[50:75] == 125.0).all()
    assert (out.iloc[75:100] == 135.0).all()


def test_a_daily_offset_still_cannot_see_the_future() -> None:
    df = sessions(5)
    full = run("prev_day.high[1]", df)
    for i in range(50, len(df), 7):
        truncated = run("prev_day.high[1]", df.iloc[:i + 1])
        a, b = full.iloc[i], truncated.iloc[i]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == b, f"differs at bar {i}"
