"""Unit tests for signals.py — condition evaluation on synthetic candles."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_schema import parse_strategies  # noqa: E402
import signals  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def make_df(closes, volumes=None) -> pd.DataFrame:
    # Plain arrays, not Series: a Series carries its own RangeIndex, and the
    # DataFrame constructor would REINDEX it against the DatetimeIndex,
    # silently producing all-NaN columns.
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    start = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=15 * i)).astimezone(UTC) for i in range(n)]
    )
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes + 1,
            "low": closes - 1,
            "close": closes,
            "volume": np.asarray(
                volumes if volumes is not None else [1000.0] * n, dtype=float
            ),
        },
        index=index,
    )


def strategy_with(entry: dict | None = None, exit_: dict | None = None, risk: dict | None = None):
    """Build a validated Strategy around given entry/exit condition groups."""
    doc = {
        "version": 2,
        "strategies": [
            {
                "name": "sig-test",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": entry
                or {"all": [{"indicator": "close", "operator": ">", "value": 0}]},
                "exit": exit_
                or {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
                "risk": risk
                or {
                    "stop_loss": {"type": "percent", "value": 0.7},
                    "target": {"type": "percent", "value": 1.5},
                },
                "sizing": {"type": "fixed_quantity", "quantity": 1},
            }
        ],
    }
    return parse_strategies(doc)[0]


# ---------------------------------------------------------------------------
# Comparison operators
# ---------------------------------------------------------------------------


def test_simple_threshold_comparison() -> None:
    strat = strategy_with({"all": [{"indicator": "close", "operator": ">", "value": 105}]})
    df = make_df([100, 104, 106, 103])
    out = signals.entry_series(df, strat)
    assert list(out) == [False, False, True, False]


def test_indicator_vs_indicator_comparison() -> None:
    # close > SMA(3): rising series ends above its own average.
    strat = strategy_with(
        {
            "all": [
                {
                    "indicator": "close",
                    "operator": ">",
                    "compare_to": {"indicator": "sma", "params": {"period": 3}},
                }
            ]
        }
    )
    df = make_df([100, 101, 102, 103, 104])
    out = signals.entry_series(df, strat)
    assert out.iloc[:2].tolist() == [False, False]  # SMA warm-up -> False
    assert out.iloc[-1]  # 104 > mean(102,103,104)=103


# ---------------------------------------------------------------------------
# Cross operators — the exact-candle semantics matter most
# ---------------------------------------------------------------------------


def cross_strategy():
    return strategy_with(
        {
            "all": [
                {
                    "indicator": "sma",
                    "params": {"period": 2},
                    "operator": "crosses_above",
                    "compare_to": {"indicator": "sma", "params": {"period": 4}},
                }
            ]
        }
    )


def test_cross_fires_exactly_once_at_the_cross() -> None:
    # Down then sharply up: fast SMA(2) must cross above slow SMA(4) once.
    df = make_df([110, 108, 106, 104, 102, 110, 118, 126])
    out = signals.entry_series(df, cross_strategy())
    assert out.sum() == 1  # exactly one crossing candle
    fired_at = out.idxmax()
    # Verify by hand: at the fired candle fast>slow, previous candle fast<=slow.
    from indicators import sma

    fast, slow = sma(df["close"], 2), sma(df["close"], 4)
    i = df.index.get_loc(fired_at)
    assert fast.iloc[i] > slow.iloc[i]
    assert fast.iloc[i - 1] <= slow.iloc[i - 1]


def test_no_cross_when_already_above() -> None:
    df = make_df([100, 102, 104, 106, 108, 110])  # fast stays above slow
    out = signals.entry_series(df, cross_strategy())
    assert not out.any()


def test_crosses_below_value_threshold() -> None:
    strat = strategy_with(
        {"all": [{"indicator": "rsi", "params": {"period": 3}, "operator": "crosses_below", "value": 30}]}
    )
    closes = list(np.linspace(100, 120, 10)) + list(np.linspace(120, 90, 15))
    out = signals.entry_series(make_df(closes), strat)
    assert out.sum() >= 1  # RSI fell through 30 somewhere on the way down
    # And it fires on the crossing candle(s), not on every candle below 30.
    below = signals.entry_series(
        make_df(closes),
        strategy_with({"all": [{"indicator": "rsi", "params": {"period": 3}, "operator": "<", "value": 30}]}),
    )
    assert out.sum() < below.sum()


# ---------------------------------------------------------------------------
# NaN discipline: warm-up can never fire a signal
# ---------------------------------------------------------------------------


def test_insufficient_history_gives_no_signal_not_error() -> None:
    strat = strategy_with(
        {"all": [{"indicator": "ema", "params": {"period": 21}, "operator": ">", "value": 0}]}
    )
    df = make_df([100.0] * 5)  # far fewer than 21 candles
    out = signals.entry_series(df, strat)
    assert not out.any()
    assert signals.entry_signal(df, strat) is False


def test_empty_frame_gives_false() -> None:
    strat = strategy_with({"all": [{"indicator": "close", "operator": ">", "value": 0}]})
    assert signals.entry_signal(make_df([]), strat) is False


# ---------------------------------------------------------------------------
# Group logic
# ---------------------------------------------------------------------------


def test_all_group_is_and() -> None:
    strat = strategy_with(
        {
            "all": [
                {"indicator": "close", "operator": ">", "value": 100},
                {"indicator": "volume", "operator": ">", "value": 2000},
            ]
        }
    )
    df = make_df([101, 102, 103], volumes=[1000.0, 3000.0, 1000.0])
    assert list(signals.entry_series(df, strat)) == [False, True, False]


def test_any_group_is_or_and_nesting_works() -> None:
    strat = strategy_with(
        {
            "any": [
                {"indicator": "close", "operator": ">", "value": 1000},  # never
                {
                    "all": [
                        {"indicator": "close", "operator": ">", "value": 100},
                        {"indicator": "volume", "operator": ">", "value": 2000},
                    ]
                },
            ]
        }
    )
    df = make_df([101, 102], volumes=[3000.0, 1000.0])
    assert list(signals.entry_series(df, strat)) == [True, False]


# ---------------------------------------------------------------------------
# The shipped strategy end-to-end on synthetic data
# ---------------------------------------------------------------------------


def test_shipped_strategy_fires_on_engineered_crossover() -> None:
    from strategy_schema import load_strategies

    strat = load_strategies(str(Path(__file__).resolve().parent.parent / "strategies.yaml"))[0]

    # 150 candles: long decline (EMA9 well under EMA21, RSI low), then a
    # strong rally with heavy volume -> at some candle EMA9 crosses above
    # EMA21 with RSI>50 and volume above its SMA(20): entry must fire.
    down = list(np.linspace(120, 100, 100))
    up = list(np.linspace(100, 130, 50))
    volumes = [1000.0] * 100 + [5000.0] * 50
    df = make_df(down + up, volumes=volumes)

    entries = signals.entry_series(df, strat)
    exits = signals.exit_series(df, strat)
    assert entries.any(), "engineered rally must trigger the entry rules"
    assert entries.sum() <= 3, "entry is cross-gated, must not fire on every rally candle"
    # During the decline the exit rules (EMA cross-down OR RSI<40) fire somewhere.
    assert exits.iloc[:100].any()


def test_min_candles_required_scales_with_largest_lookback() -> None:
    from strategy_schema import load_strategies

    strat = load_strategies(str(Path(__file__).resolve().parent.parent / "strategies.yaml"))[0]
    # Largest lookback is EMA(21) -> 5*21+10 = 115.
    assert signals.min_candles_required(strat) == 115


def test_unsorted_frame_is_rejected() -> None:
    strat = strategy_with({"all": [{"indicator": "close", "operator": ">", "value": 0}]})
    df = make_df([100, 101, 102]).iloc[::-1]  # reversed index
    with pytest.raises(ValueError, match="not sorted"):
        signals.entry_series(df, strat)


# ---------------------------------------------------------------------------
# ATR stop/target periods must count toward the fetch window. The paper engine
# builds those ATR series from the SAME frame this function sizes, so a short
# entry lookback paired with a long ATR stop would otherwise under-fetch and
# fail at run time on history it was never asked to retrieve.
# ---------------------------------------------------------------------------


def test_an_atr_stop_period_extends_the_required_history():
    short_lookback = strategy_with(
        risk={
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        }
    )
    long_atr_stop = strategy_with(
        risk={
            "stop_loss": {"type": "atr", "period": 100, "multiplier": 1.5},
            "target": {"type": "percent", "value": 2.0},
        }
    )
    assert signals.min_candles_required(long_atr_stop) > signals.min_candles_required(short_lookback)
    # 5 x (100 + 1) + 10, matching how an ATR indicator lookback is treated.
    assert signals.min_candles_required(long_atr_stop) >= 100


def test_an_atr_target_period_also_counts():
    atr_target = strategy_with(
        risk={
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "atr", "period": 80, "multiplier": 3},
        }
    )
    assert signals.min_candles_required(atr_target) >= 80


def test_a_trailing_atr_period_counts_too():
    """Trailing behaviour lands later, but its history need is real now."""
    trailing = strategy_with(
        risk={
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
            "trailing_stop": {"type": "atr", "period": 60, "multiplier": 1.0},
        }
    )
    assert signals.min_candles_required(trailing) >= 60


# ---------------------------------------------------------------------------
# Previous-period references (`offset`)
# ---------------------------------------------------------------------------
#
# `offset: N` reads a series N closed bars back. It is what lets a strategy
# say "higher than the previous bar" without duplicating indicators, and it
# is the foundation the higher-timeframe reference is built on.


def test_offset_reads_the_previous_bar() -> None:
    """close[-1], not close."""
    strat = strategy_with(
        {"all": [{"indicator": "close", "offset": 1, "operator": ">", "value": 105}]}
    )
    df = make_df([100, 110, 100, 100])
    series = signals.entry_series(df, strat)
    # Bar 2 sees bar 1's close of 110; bar 1 sees bar 0's close of 100.
    assert list(series) == [False, False, True, False]


def test_offset_zero_is_the_current_bar() -> None:
    strat = strategy_with(
        {"all": [{"indicator": "close", "offset": 0, "operator": ">", "value": 105}]}
    )
    df = make_df([100, 110, 100])
    assert list(signals.entry_series(df, strat)) == [False, True, False]


def test_omitting_offset_matches_offset_zero() -> None:
    """The default must not change behaviour for every existing strategy."""
    df = make_df([100, 110, 100])
    without = signals.entry_series(
        df, strategy_with({"all": [{"indicator": "close", "operator": ">", "value": 105}]})
    )
    with_zero = signals.entry_series(
        df,
        strategy_with(
            {"all": [{"indicator": "close", "offset": 0, "operator": ">", "value": 105}]}
        ),
    )
    assert list(without) == list(with_zero)


def test_offset_before_history_exists_is_false_not_an_error() -> None:
    """No value yet is not a signal. NaN must never read as True."""
    strat = strategy_with(
        {"all": [{"indicator": "close", "offset": 2, "operator": ">", "value": 1}]}
    )
    df = make_df([100, 110, 120])
    assert list(signals.entry_series(df, strat)) == [False, False, True]


def test_offset_applies_to_indicators_not_just_price() -> None:
    """The previous bar's RSI, not the previous bar's close."""
    strat = strategy_with(
        {"all": [{"indicator": "rsi", "params": {"period": 2},
                  "offset": 1, "operator": ">", "value": 0}]}
    )
    df = make_df([100, 101, 102, 103, 104, 105])
    plain = signals.operand_series(
        df, strategy_with(
            {"all": [{"indicator": "rsi", "params": {"period": 2},
                      "operator": ">", "value": 0}]}
        ).entry.items[0].left
    )
    shifted = signals.operand_series(df, strat.entry.items[0].left)
    pd.testing.assert_series_equal(
        shifted.iloc[1:].reset_index(drop=True),
        plain.iloc[:-1].reset_index(drop=True),
        check_names=False,
    )


def test_offset_on_both_sides_of_a_comparison() -> None:
    """'this bar's close above the previous bar's high' — a breakout."""
    strat = strategy_with(
        {"all": [{
            "indicator": "close",
            "operator": ">",
            "compare_to": {"indicator": "high", "offset": 1},
        }]}
    )
    # make_df sets high = close + 1.
    df = make_df([100, 100, 102, 100])
    # Bar 2: close 102 > bar 1's high of 101 -> True.
    assert list(signals.entry_series(df, strat)) == [False, False, True, False]


def test_offset_extends_the_required_history() -> None:
    """A strategy that reads 3 bars back needs 3 more bars before it can fire."""
    base = strategy_with(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 50}]}
    )
    offset = strategy_with(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "offset": 3, "operator": ">", "value": 50}]}
    )
    assert (
        signals.min_candles_required(offset)
        == signals.min_candles_required(base) + 3
    )


# ---------------------------------------------------------------------------
# Higher-timeframe references (`timeframe`)
# ---------------------------------------------------------------------------
#
# The whole risk here is look-ahead. A 15m bar at 10:00 must NOT be able to
# see the daily high, because today's daily bar has not closed — knowing it
# would be knowing the future, and a backtest built on that is worthless.
#
# The rule these tests pin down: an operand on a higher timeframe resolves to
# the most recent higher-timeframe bar that had FULLY CLOSED by the time the
# current bar closed. During today's session that is yesterday's daily bar.

BARS_PER_DAY = 25  # 09:15-15:30 IST in 15m bars


def make_sessions(day_specs: list[dict]) -> pd.DataFrame:
    """Build a multi-session 15m frame.

    Each spec is {"date": date, "closes": [...25 floats...]}.
    """
    stamps: list[datetime] = []
    rows: list[float] = []
    for spec in day_specs:
        d = spec["date"]
        closes = spec["closes"]
        assert len(closes) == BARS_PER_DAY
        for i, close in enumerate(closes):
            start = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST) + timedelta(
                minutes=15 * i
            )
            stamps.append(start.astimezone(UTC))
            rows.append(float(close))
    closes = np.asarray(rows, dtype=float)
    return pd.DataFrame(
        {
            "open": closes,
            "high": closes,      # high == close keeps the arithmetic obvious
            "low": closes,
            "close": closes,
            "volume": np.full(len(closes), 1000.0),
        },
        index=pd.DatetimeIndex(stamps, name="ts"),
    )


def flat_day(d, value: float) -> dict:
    return {"date": d, "closes": [value] * BARS_PER_DAY}


def test_daily_operand_reads_yesterdays_bar_during_todays_session() -> None:
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "high", "timeframe": "day",
                  "operator": ">", "value": 0}]}
    )
    df = make_sessions([
        flat_day(date(2026, 7, 16), 100.0),
        flat_day(date(2026, 7, 17), 200.0),
        flat_day(date(2026, 7, 20), 300.0),
    ])
    series = signals.operand_series(df, strat.entry.items[0].left)

    day2 = series.iloc[BARS_PER_DAY:BARS_PER_DAY * 2]
    day3 = series.iloc[BARS_PER_DAY * 2:]
    # Day 2 sees day 1's high of 100 throughout — never its own 200.
    assert (day2 == 100.0).all()
    assert (day3 == 300.0).sum() == 0
    assert (day3 == 200.0).all()


def test_first_session_has_no_previous_day_and_stays_nan() -> None:
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "high", "timeframe": "day",
                  "operator": ">", "value": 0}]}
    )
    df = make_sessions([
        flat_day(date(2026, 7, 16), 100.0),
        flat_day(date(2026, 7, 17), 200.0),
    ])
    series = signals.operand_series(df, strat.entry.items[0].left)
    assert series.iloc[:BARS_PER_DAY].isna().all()


def test_daily_operand_never_leaks_todays_value_into_any_bar() -> None:
    """The look-ahead test, stated directly."""
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "high", "timeframe": "day",
                  "operator": ">", "value": 0}]}
    )
    days = [flat_day(date(2026, 7, 16), 100.0),
            flat_day(date(2026, 7, 17), 200.0),
            flat_day(date(2026, 7, 20), 300.0)]
    df = make_sessions(days)
    series = signals.operand_series(df, strat.entry.items[0].left)
    ist_dates = df.index.tz_convert(IST).date
    for value, own_date in zip(series, ist_dates):
        if pd.isna(value):
            continue
        # Whatever it read, it must belong to an EARLIER session.
        assert value != {d["date"]: d["closes"][0] for d in days}[own_date]


def test_offset_on_a_daily_operand_counts_in_days() -> None:
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "high", "timeframe": "day", "offset": 1,
                  "operator": ">", "value": 0}]}
    )
    df = make_sessions([
        flat_day(date(2026, 7, 16), 100.0),
        flat_day(date(2026, 7, 17), 200.0),
        flat_day(date(2026, 7, 20), 300.0),
    ])
    series = signals.operand_series(df, strat.entry.items[0].left)
    # Third session: offset 1 steps back past yesterday (200) to 100.
    assert (series.iloc[BARS_PER_DAY * 2:] == 100.0).all()


def test_higher_intraday_timeframe_aligns_to_closed_bars_only() -> None:
    """A 15m strategy reading 60m bars."""
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "close", "timeframe": "60m",
                  "operator": ">", "value": 0}]}
    )
    closes = [float(i) for i in range(BARS_PER_DAY)]
    df = make_sessions([{"date": date(2026, 7, 16), "closes": closes}])
    series = signals.operand_series(df, strat.entry.items[0].left)

    # 60m buckets are session-anchored: bars 0-3 form 09:15-10:15, etc.
    # The first four bars have no closed 60m bar behind them.
    assert series.iloc[:4].isna().all()
    # Bars 4-7 see the first 60m bucket, whose close is bar 3's value.
    assert (series.iloc[4:8] == 3.0).all()


def test_strategy_timeframe_operand_is_unchanged() -> None:
    """Naming your own timeframe must be a no-op, not a shift."""
    from datetime import date

    df = make_sessions([flat_day(date(2026, 7, 16), 100.0)])
    plain = signals.operand_series(
        df,
        strategy_with(
            {"all": [{"indicator": "close", "operator": ">", "value": 0}]}
        ).entry.items[0].left,
    )
    named = signals.operand_series(
        df,
        strategy_with(
            {"all": [{"indicator": "close", "timeframe": "15m",
                      "operator": ">", "value": 0}]}
        ).entry.items[0].left,
    )
    pd.testing.assert_series_equal(plain, named, check_names=False)


def test_daily_indicator_not_just_price() -> None:
    """A daily EMA as a trend filter under a 15m entry — the headline case."""
    from datetime import date

    strat = strategy_with(
        {"all": [{"indicator": "ema", "params": {"period": 2},
                  "timeframe": "day", "operator": ">", "value": 0}]}
    )
    df = make_sessions([
        flat_day(date(2026, 7, 16), 100.0),
        flat_day(date(2026, 7, 17), 200.0),
        flat_day(date(2026, 7, 20), 300.0),
    ])
    series = signals.operand_series(df, strat.entry.items[0].left)
    # Constant within a session: it is a daily value held across the day.
    assert series.iloc[BARS_PER_DAY * 2:].nunique() == 1


def test_higher_timeframe_multiplies_required_history() -> None:
    """A daily EMA(20) under a 15m strategy needs 20 DAYS of 15m bars."""
    intraday = strategy_with(
        {"all": [{"indicator": "ema", "params": {"period": 20},
                  "operator": ">", "value": 0}]}
    )
    daily = strategy_with(
        {"all": [{"indicator": "ema", "params": {"period": 20},
                  "timeframe": "day", "operator": ">", "value": 0}]}
    )
    assert (
        signals.min_candles_required(daily)
        > signals.min_candles_required(intraday) * 20
    )
