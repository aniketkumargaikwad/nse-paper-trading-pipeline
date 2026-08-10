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
