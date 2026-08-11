"""Condition evaluator: turns validated strategy rules into boolean signals.

Pure functions, no I/O. The input DataFrame must contain CLOSED candles only —
kite_client guarantees that with its closed_only default, and the backtester
iterates historical (hence closed) candles. Nothing in this module can see a
forming candle, so there is no repaint/look-ahead path.

Two consumption styles, both from the same code path:

* Paper engine:  entry_signal(df, strategy)  -> bool for the LAST closed candle.
* Backtester:    entry_series(df, strategy)  -> boolean Series over all candles.

Semantics
---------
* Comparison operators (>, <, >=, <=) evaluate on the same candle.
* crosses_above/crosses_below compare the PREVIOUS candle to the current one:
  "was on/under the other side, now strictly beyond it".
* Any NaN involved (indicator warm-up, short history) makes that condition
  False — insufficient data is "no signal", never a guess and never an error.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import indicators
from risk_levels import atr_periods_for
from strategy_schema import Condition, ConditionGroup, Operand, Strategy

# ---------------------------------------------------------------------------
# Operand -> Series
# ---------------------------------------------------------------------------

# Cache type: one dict per evaluation call, keyed by the operand's identity.
# EMA(9) referenced in both entry and exit rules is computed exactly once.
_Cache = dict


def _cache_key(op: Operand) -> tuple:
    return (op.indicator, tuple(sorted(op.params.items())), op.source, op.output)


def operand_series(df: pd.DataFrame, op: Operand, cache: _Cache | None = None) -> pd.Series:
    """Materialize one operand (indicator or raw series) as a float Series."""
    if cache is not None:
        key = _cache_key(op)
        if key in cache:
            return cache[key]

    series = _compute_operand(df, op)

    if cache is not None:
        cache[_cache_key(op)] = series
    return series


def _compute_operand(df: pd.DataFrame, op: Operand) -> pd.Series:
    name = op.indicator
    if name in ("close", "open", "high", "low", "volume"):
        return df[name].astype(float)
    if name == "ema":
        return indicators.ema(df[op.source].astype(float), op.params["period"])
    if name == "sma":
        return indicators.sma(df[op.source].astype(float), op.params["period"])
    if name == "rsi":
        return indicators.rsi(df["close"], op.params["period"])
    if name == "macd":
        frame = indicators.macd(
            df["close"], op.params["fast"], op.params["slow"], op.params["signal"]
        )
        return frame[op.output]
    if name == "bbands":
        frame = indicators.bollinger_bands(df["close"], op.params["period"], op.params["std"])
        return frame[op.output]
    if name == "supertrend":
        frame = indicators.supertrend(df, op.params["period"], op.params["multiplier"])
        return frame[op.output]
    if name == "atr":
        return indicators.atr(df, op.params["period"])
    if name == "vwap":
        return indicators.vwap(df)
    # Unreachable if strategy_schema validated the config — kept as a guard
    # against the two modules drifting out of sync.
    raise ValueError(f"signals.py has no implementation for indicator {name!r}")


# ---------------------------------------------------------------------------
# Condition / group -> boolean Series
# ---------------------------------------------------------------------------


def condition_series(df: pd.DataFrame, cond: Condition, cache: _Cache) -> pd.Series:
    """Vectorized truth series for one condition. NaN anywhere -> False."""
    left = operand_series(df, cond.left, cache)
    if cond.right is not None:
        right = operand_series(df, cond.right, cache)
    else:
        right = pd.Series(cond.value, index=df.index, dtype=float)

    if cond.operator in (">", "<", ">=", "<="):
        valid = left.notna() & right.notna()
        if cond.operator == ">":
            raw = left > right
        elif cond.operator == "<":
            raw = left < right
        elif cond.operator == ">=":
            raw = left >= right
        else:
            raw = left <= right
        return raw & valid

    # Cross operators: previous candle on/under the line, current strictly
    # beyond it. All four values must exist or the answer is False.
    prev_left, prev_right = left.shift(1), right.shift(1)
    valid = left.notna() & right.notna() & prev_left.notna() & prev_right.notna()
    if cond.operator == "crosses_above":
        raw = (prev_left <= prev_right) & (left > right)
    elif cond.operator == "crosses_below":
        raw = (prev_left >= prev_right) & (left < right)
    else:  # unreachable after schema validation; loud guard for drift
        raise ValueError(f"signals.py has no implementation for operator {cond.operator!r}")
    return raw & valid


def group_series(df: pd.DataFrame, group: ConditionGroup, cache: _Cache) -> pd.Series:
    """AND/OR combination of member conditions (and nested groups)."""
    members = [
        group_series(df, item, cache)
        if isinstance(item, ConditionGroup)
        else condition_series(df, item, cache)
        for item in group.items
    ]
    combined = members[0]
    for series in members[1:]:
        combined = (combined & series) if group.logic == "all" else (combined | series)
    return combined


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def entry_series(df: pd.DataFrame, strategy: Strategy) -> pd.Series:
    """Boolean Series: True where the entry rules fire (backtester's view)."""
    _validate_frame(df)
    cache: _Cache = {}
    return group_series(df, strategy.entry, cache)


def exit_series(df: pd.DataFrame, strategy: Strategy) -> pd.Series:
    """Boolean Series: True where the exit rules fire (backtester's view)."""
    _validate_frame(df)
    cache: _Cache = {}
    return group_series(df, strategy.exit, cache)


def entry_signal(df: pd.DataFrame, strategy: Strategy) -> bool:
    """Did the entry rules fire on the most recent CLOSED candle?"""
    if df.empty:
        return False
    return bool(entry_series(df, strategy).iloc[-1])


def exit_signal(df: pd.DataFrame, strategy: Strategy) -> bool:
    """Did the exit rules fire on the most recent CLOSED candle?"""
    if df.empty:
        return False
    return bool(exit_series(df, strategy).iloc[-1])


def min_candles_required(strategy: Strategy) -> int:
    """How many closed candles the engine should fetch for this strategy.

    5x the largest indicator lookback, because recursive indicators (EMA,
    RSI, ATR, and everything built on them) need several multiples of their
    period to converge to the values a chart would show — feeding an EMA(21)
    exactly 21 candles produces a subtly wrong number, which is worse than
    an obviously missing one. +10 covers the cross operators' shift(1) and
    general slack. Over-fetching is cheap; silently-wrong indicators are not.

    ATR-based risk periods (stop, target, trailing) count too, even though
    they never appear in entry/exit conditions: the paper engine computes
    those ATR series from this SAME fetched frame (see
    paper_engine._stop_target_levels), so an entry rule with a short
    lookback must not starve a much longer ATR stop period of history —
    that would turn a perfectly good strategy into a hard error every run
    for a reason that isn't obvious from the strategy definition itself.
    """
    lookbacks = [_operand_lookback(op) for op in _walk_operands(strategy)]
    # Sourced from risk_levels rather than re-listed here: that module is
    # what actually builds these series, and a second list would drift the
    # moment a new risk field is added (trailing_stop already proved it).
    lookbacks += [period + 1 for period in atr_periods_for(strategy)]
    return 5 * max(lookbacks) + 10


def _operand_lookback(op: Operand) -> int:
    name = op.indicator
    if name in ("close", "open", "high", "low", "volume"):
        return 1
    if name == "vwap":
        return 26  # one full 15m session (25 candles) + 1
    if name == "macd":
        return op.params["slow"] + op.params["signal"]
    if name in ("rsi", "atr", "supertrend"):
        return op.params["period"] + 1  # these diff/shift one extra candle
    return op.params["period"]  # ema, sma, bbands


def _walk_operands(strategy: Strategy):
    """Yield every operand in the strategy's entry and exit trees."""

    def walk(group: ConditionGroup):
        for item in group.items:
            if isinstance(item, ConditionGroup):
                yield from walk(item)
            else:
                yield item.left
                if item.right is not None:
                    yield item.right

    yield from walk(strategy.entry)
    yield from walk(strategy.exit)


def _validate_frame(df: pd.DataFrame) -> None:
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise ValueError(
            f"Candle frame is missing column(s): {sorted(missing)}. "
            "Build frames with kite_client.candles_to_dataframe."
        )
    if len(df) > 1 and not df.index.is_monotonic_increasing:
        raise ValueError(
            "Candle frame index is not sorted ascending — signals would be "
            "evaluated out of order. Sort the frame before evaluating."
        )
