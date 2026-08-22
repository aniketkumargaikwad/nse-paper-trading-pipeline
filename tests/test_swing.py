"""Swing points: the vocabulary with a look-ahead trap built into it.

A swing high at bar t is only a swing high once n LATER bars have failed to
exceed it. So at bar t nobody knows yet — the pivot is confirmed at bar t+n,
and anything that reports it earlier is reading the future.

This is the most dangerous kind of indicator to add, because the naive
implementation (a centred rolling max) is both the obvious one and silently
wrong: it produces a beautiful backtest that buys every high before the high
has happened. The confirmation delay is the whole implementation, and the
truncation property below is what proves it.
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


def frame(highs, lows=None) -> pd.DataFrame:
    highs = np.asarray(highs, dtype=float)
    lows = np.asarray(lows, dtype=float) if lows is not None else highs - 2.0
    n = len(highs)
    start = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=15 * i)).astimezone(UTC) for i in range(n)],
        name="ts",
    )
    mid = (highs + lows) / 2
    return pd.DataFrame(
        {"open": mid, "high": highs, "low": lows, "close": mid,
         "volume": np.full(n, 1000.0)},
        index=index,
    )


def run(source: str, df: pd.DataFrame) -> pd.Series:
    return evaluate(parse_expression(source), df)


# --- what a swing point is --------------------------------------------------


def test_a_swing_high_is_found() -> None:
    #        0    1    2    3    4    5    6
    highs = [10,  11,  15,  11,  10,  10,  10]
    out = run("swing.high(2)", frame(highs))
    # The pivot at bar 2 needs 2 bars either side, so it is confirmed at bar 4.
    assert out.iloc[4] == 15.0


def test_a_swing_high_is_not_reported_before_it_is_confirmed() -> None:
    """THE test. At bar 2 the high has printed, but nothing yet says it was a
    high — bars 3 and 4 have not happened."""
    highs = [10, 11, 15, 11, 10, 10, 10]
    out = run("swing.high(2)", frame(highs))
    assert pd.isna(out.iloc[2]), "reported the pivot on the pivot's own bar"
    assert pd.isna(out.iloc[3]), "reported it one bar early"
    assert out.iloc[4] == 15.0


def test_a_swing_low_is_found_and_delayed_too() -> None:
    lows = [20, 19, 15, 19, 20, 20, 20]
    out = run("swing.low(2)", frame([x + 2 for x in lows], lows))
    assert pd.isna(out.iloc[3])
    assert out.iloc[4] == 15.0


def test_the_most_recent_confirmed_swing_is_held() -> None:
    """Between pivots the value persists — it is a level, not an event."""
    highs = [10, 11, 15, 11, 10, 10, 10, 10]
    out = run("swing.high(2)", frame(highs))
    assert out.iloc[4] == 15.0
    assert out.iloc[7] == 15.0


def test_a_later_swing_replaces_an_earlier_one() -> None:
    highs = [10, 11, 15, 11, 10, 12, 20, 12, 10, 10]
    out = run("swing.high(2)", frame(highs))
    assert out.iloc[4] == 15.0        # first pivot
    assert out.iloc[8] == 20.0        # second pivot, confirmed at bar 8


def test_a_rise_with_no_pullback_has_no_swing() -> None:
    out = run("swing.high(2)", frame(list(range(10, 30))))
    assert out.isna().all()


def test_early_bars_have_no_swing() -> None:
    highs = [10, 11, 15, 11, 10]
    out = run("swing.high(2)", frame(highs))
    assert out.iloc[:4].isna().all()


def test_a_wider_lookback_confirms_later() -> None:
    highs = [10, 11, 12, 20, 12, 11, 10, 10, 10, 10]
    narrow = run("swing.high(1)", frame(highs))
    wide = run("swing.high(3)", frame(highs))
    assert narrow.iloc[4] == 20.0     # confirmed 1 bar after the pivot
    assert pd.isna(wide.iloc[4])      # still unconfirmed at 3 bars
    assert wide.iloc[6] == 20.0


# --- no look-ahead ----------------------------------------------------------


def wandering(n: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.5, size=n))
    return frame(closes + 1.0, closes - 1.0)


@pytest.mark.parametrize("source", [
    "swing.high(2)", "swing.low(2)", "swing.high(5)", "swing.low(8)",
    "close > swing.high(3)",
    "swing.high(3) - swing.low(3)",
])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_swings_cannot_see_the_future(source: str, seed: int) -> None:
    """Evaluating over the whole frame must give a bar the same answer as
    evaluating over history up to that bar. A centred rolling max fails this
    on every single bar."""
    df = wandering(200, seed)
    full = run(source, df)
    for i in range(40, len(df), 11):
        truncated = run(source, df.iloc[:i + 1])
        a, b = full.iloc[i], truncated.iloc[i]
        if pd.isna(a) and pd.isna(b):
            continue
        assert a == b, f"{source} differs at bar {i}: {a} full vs {b} truncated"


# --- usable in real expressions ---------------------------------------------


def test_a_swing_participates_in_arithmetic() -> None:
    """`swing.low(20)` as a target, the audit's own example."""
    df = wandering(120, seed=4)
    out = run("close - swing.low(5)", df)
    assert out.notna().any()


def test_a_swing_can_be_compared_to_price() -> None:
    df = wandering(120, seed=6)
    out = run("close > swing.high(5)", df)
    assert out.dtype == bool


def test_a_swing_period_must_be_a_literal() -> None:
    df = wandering(50, seed=1)
    with pytest.raises(EvaluationError):
        run("swing.high(close)", df)


def test_an_unknown_swing_output_is_rejected() -> None:
    df = wandering(50, seed=1)
    with pytest.raises(EvaluationError) as exc:
        run("swing.middle(3)", df)
    assert "middle" in str(exc.value)
