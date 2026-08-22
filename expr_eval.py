"""Evaluating a v3 expression AST against candles.

`strategy/expr.py` turns text into a tree and knows nothing about what the
names mean. This module owns the namespace: which names exist, what they
resolve to, and — the part worth reading carefully — how a higher-timeframe
reference is aligned so it cannot see a bar that has not closed.

Everything is vectorized: an expression becomes one pandas Series over the
whole frame, exactly like `signals.operand_series`, so the backtester keeps
evaluating a run in one pass instead of looping bars.

THE NAMESPACE
-------------
    close open high low volume        the traded series
    candle.is_bullish / .is_bearish   direction of the current bar
    candle.body / .range              size of the current bar
    candle.upper_wick / .lower_wick
    prev_day.open/high/low/close      the last CLOSED daily bar
    sma(n) ema(n) rsi(n) atr(n)       indicators over close
    sma(series, n) ema(series, n)     a moving average of anything
    vwap()
    macd.line/.signal/.histogram(f,s,g)
    bbands.upper/.middle/.lower(n,std)
    supertrend.line/.direction(n,mult)
    <anything else>                   a variable supplied by the caller

An unknown name is an ERROR, never a NaN. A NaN would make the condition
simply never true, which is indistinguishable from a strategy that honestly
found no setups — the kind of failure that wastes weeks.
"""

from __future__ import annotations

from typing import Any, Mapping

import numpy as np
import pandas as pd

import indicators
from config import TIMEFRAME_MINUTES
from resample import aggregate_for_reference
from strategy.expr import Binary, Call, Expr, Literal, Name, Offset, Unary
from strategy.vocabulary import (
    EXPR_MULTI_OUTPUT,
    EXPR_SIMPLE_INDICATORS,
    HIGHER_TIMEFRAME_PREFIXES,
)


class EvaluationError(ValueError):
    """An expression is well-formed but cannot be evaluated as written."""


# The bare price series, usable anywhere a number is.
_PRICE_SERIES = frozenset({"open", "high", "low", "close", "volume"})

# Which higher timeframe each dotted prefix refers to. Defined in the pure
# vocabulary module so the validator and the evaluator cannot disagree about
# which names exist — they did, once, and `daily.high` evaluated correctly
# while failing validation.
#
# `prev_day` and `daily` are the same timeframe under two names, and both are
# worth having. By the last-closed rule below, the newest closed daily bar
# during today's session IS yesterday's — so `prev_day.high` says what it
# means for a price field, while `daily.ema(20)` reads better for an
# indicator nobody thinks of as "yesterday's".
_HIGHER_TIMEFRAME_PREFIXES = HIGHER_TIMEFRAME_PREFIXES


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def evaluate(
    node: Expr,
    df: pd.DataFrame,
    variables: Mapping[str, Any] | None = None,
) -> pd.Series:
    """Evaluate `node` over `df`, returning a Series on `df`'s index.

    `variables` supplies any name not in the built-in namespace — the state
    machine's `set:` bindings. A value may be a scalar (broadcast to every
    bar) or a Series already aligned to `df`.
    """
    result = _eval(node, df, variables or {})
    return _as_series(result, df)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def _eval(node: Expr, df: pd.DataFrame, variables: Mapping[str, Any]) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, Name):
        return _eval_name(node, df, variables)
    if isinstance(node, Call):
        return _eval_call(node, df, variables)
    if isinstance(node, Offset):
        inner = node.operand
        if (
            isinstance(inner, (Name, Call))
            and len(inner.path) > 1
            and inner.path[0] in _HIGHER_TIMEFRAME_PREFIXES
        ):
            timeframe = _HIGHER_TIMEFRAME_PREFIXES[inner.path[0]]
            stripped: Expr = (
                Name(inner.path[1:]) if isinstance(inner, Name)
                else Call(inner.path[1:], inner.args)
            )
            return _on_higher_timeframe(
                df, timeframe, stripped, variables, shift=node.bars
            )
        return _as_series(_eval(node.operand, df, variables), df).shift(node.bars)
    if isinstance(node, Unary):
        return _eval_unary(node, df, variables)
    if isinstance(node, Binary):
        return _eval_binary(node, df, variables)
    raise EvaluationError(f"cannot evaluate node of type {type(node).__name__}")


def _eval_unary(node: Unary, df: pd.DataFrame, variables: Mapping[str, Any]) -> Any:
    value = _eval(node.operand, df, variables)
    if node.op == "-":
        return -_as_series(value, df)
    # `not` on a float series is meaningless, so coerce through the same
    # NaN-is-false rule every condition uses.
    return ~_as_bool(value, df)


def _eval_binary(node: Binary, df: pd.DataFrame, variables: Mapping[str, Any]) -> Any:
    op = node.op
    if op in ("and", "or"):
        left = _as_bool(_eval(node.left, df, variables), df)
        right = _as_bool(_eval(node.right, df, variables), df)
        return (left & right) if op == "and" else (left | right)

    left = _as_series(_eval(node.left, df, variables), df)
    right = _as_series(_eval(node.right, df, variables), df)

    if op == "+":
        return left + right
    if op == "-":
        return left - right
    if op == "*":
        return left * right
    if op == "/":
        # A zero divisor yields NaN rather than inf. inf compares as greater
        # than every threshold, so it would fire conditions rather than
        # withhold them — absence must never present as a signal.
        return (left / right.replace(0.0, np.nan)) if isinstance(right, pd.Series) \
            else (left / right if right != 0 else _nan_series(df))
    if op == "<":
        return left < right
    if op == ">":
        return left > right
    if op == "<=":
        return left <= right
    if op == ">=":
        return left >= right
    if op == "==":
        return left == right
    if op == "!=":
        return left != right
    raise EvaluationError(f"unknown operator {op!r}")


# ---------------------------------------------------------------------------
# Names
# ---------------------------------------------------------------------------


def _eval_name(node: Name, df: pd.DataFrame, variables: Mapping[str, Any]) -> Any:
    path = node.path

    if len(path) == 1:
        name = path[0]
        if name in _PRICE_SERIES:
            return df[name].astype(float)
        if name in variables:
            return variables[name]
        raise EvaluationError(
            f"unknown name {name!r}. Known series: "
            f"{', '.join(sorted(_PRICE_SERIES))}"
            + (f"; known variables: {', '.join(sorted(variables))}"
               if variables else "; no variables are defined here")
        )

    if len(path) == 2:
        prefix, attr = path
        if prefix == "candle":
            return _candle_attribute(attr, df)
        if prefix in _HIGHER_TIMEFRAME_PREFIXES:
            return _higher_timeframe(
                df, _HIGHER_TIMEFRAME_PREFIXES[prefix], attr
            )

    dotted = ".".join(path)
    raise EvaluationError(
        f"unknown name {dotted!r}. Known prefixes: candle, "
        f"{', '.join(sorted(_HIGHER_TIMEFRAME_PREFIXES))}"
    )


def _candle_attribute(attr: str, df: pd.DataFrame) -> pd.Series:
    open_, high = df["open"].astype(float), df["high"].astype(float)
    low, close = df["low"].astype(float), df["close"].astype(float)

    if attr == "is_bullish":
        return close > open_
    if attr == "is_bearish":
        return close < open_
    if attr == "body":
        return (close - open_).abs()
    if attr == "range":
        return high - low
    if attr == "upper_wick":
        return high - pd.concat([open_, close], axis=1).max(axis=1)
    if attr == "lower_wick":
        return pd.concat([open_, close], axis=1).min(axis=1) - low
    raise EvaluationError(
        f"unknown candle attribute {attr!r}. Known: is_bullish, is_bearish, "
        "body, range, upper_wick, lower_wick"
    )


def _on_higher_timeframe(
    df: pd.DataFrame, timeframe: str, inner: Expr,
    variables: Mapping[str, Any], shift: int = 0,
) -> pd.Series:
    """Evaluate `inner` on a higher timeframe, then align it back.

    The indicator is computed on the AGGREGATED bars, not on the strategy's
    own bars — `daily.ema(20)` is a 20-DAY average, which is the whole reason
    to ask for it. Computing ema(20) on 15m bars and relabelling it would be a
    different number wearing the same name.
    """
    if df.empty:
        return _nan_series(df)
    higher = aggregate_for_reference(df, timeframe)
    if higher.empty:
        return _nan_series(df)
    values = _as_series(_eval(inner, higher, variables), higher)
    if shift:
        # Shifted on the HIGHER timeframe, so `daily.close[1]` steps back a
        # DAY. Shifting the aligned series instead would step back one
        # strategy bar — `prev_day.high[1]` would mean "yesterday's high, 15
        # minutes ago", which is the same number and a different sentence.
        values = values.shift(shift)
    return _align_from_higher(df, higher, values, timeframe)


def _align_from_higher(
    df: pd.DataFrame, higher: pd.DataFrame, values: pd.Series, timeframe: str
) -> pd.Series:
    """Carry higher-timeframe values down, visible only once fully closed."""
    # Bars are stamped at their START, so a bar's close is start + length.
    closes = higher.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    left = pd.DataFrame({"_at": df.index})
    right = pd.DataFrame({"_at": closes, "_v": values.to_numpy()}).sort_values("_at")
    merged = pd.merge_asof(
        left, right, on="_at", direction="backward", allow_exact_matches=True
    )
    return pd.Series(merged["_v"].to_numpy(), index=df.index, dtype="float64")


def _higher_timeframe(df: pd.DataFrame, timeframe: str, attr: str) -> pd.Series:
    """A higher-timeframe series, aligned so it can only ever look backward.

    THE RULE: a higher-timeframe bar becomes visible only once it has fully
    closed, and visibility is decided against the strategy bar's START.

    Using the start rather than the close costs a sliver of information and
    buys something worth more: the reference means the SAME thing on every bar
    of the session. Deciding at the close would let the last intraday bar of
    the day — the one where the daily bar closes at the same instant — see
    today's daily high while the other twenty-four saw yesterday's. That is
    not look-ahead, but a condition that quietly changes what it tests at
    15:15 is its own kind of trap.

    This mirrors `signals._higher_timeframe_series` deliberately: two
    alignment rules that could drift apart would be worse than one shared one.
    """
    if attr not in _PRICE_SERIES:
        raise EvaluationError(
            f"unknown higher-timeframe field {attr!r}. Known: "
            f"{', '.join(sorted(_PRICE_SERIES))}"
        )
    if df.empty:
        return _nan_series(df)

    higher = aggregate_for_reference(df, timeframe)
    if higher.empty:
        return _nan_series(df)

    values = higher[attr].astype(float)

    # Bars are stamped at their START, so a bar's close is start + length.
    closes = higher.index + pd.Timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    left = pd.DataFrame({"_at": df.index})
    right = pd.DataFrame({"_at": closes, "_v": values.to_numpy()}).sort_values("_at")
    merged = pd.merge_asof(
        left, right, on="_at", direction="backward", allow_exact_matches=True
    )
    return pd.Series(merged["_v"].to_numpy(), index=df.index, dtype="float64")


# ---------------------------------------------------------------------------
# Calls
# ---------------------------------------------------------------------------

# Both defined in the pure vocabulary module, so the validator, this
# evaluator and the generated format document read one list.
_SIMPLE_INDICATORS = EXPR_SIMPLE_INDICATORS
_MULTI_OUTPUT = EXPR_MULTI_OUTPUT


def _eval_call(node: Call, df: pd.DataFrame, variables: Mapping[str, Any]) -> Any:
    path = node.path

    # `daily.ema(20)` / `daily.macd.line(12,26,9)`: strip the timeframe
    # prefix and evaluate what is left on the aggregated frame.
    if len(path) > 1 and path[0] in _HIGHER_TIMEFRAME_PREFIXES:
        return _on_higher_timeframe(
            df, _HIGHER_TIMEFRAME_PREFIXES[path[0]],
            Call(path[1:], node.args), variables,
        )

    if len(path) == 1 and path[0] in _SIMPLE_INDICATORS:
        return _simple_indicator(path[0], node, df, variables)

    if len(path) == 2 and path[0] in _MULTI_OUTPUT:
        family, output = path
        if output not in _MULTI_OUTPUT[family]:
            raise EvaluationError(
                f"unknown output {output!r} for {family}. Known: "
                f"{', '.join(_MULTI_OUTPUT[family])}"
            )
        return _multi_output_indicator(family, output, node, df, variables)

    if len(path) == 1 and path[0] in _MULTI_OUTPUT:
        raise EvaluationError(
            f"{path[0]} produces several series, so it needs one named: "
            f"write {path[0]}.{_MULTI_OUTPUT[path[0]][0]}(...) instead of "
            f"{path[0]}(...)"
        )

    dotted = ".".join(path)
    known = sorted(_SIMPLE_INDICATORS) + [
        f"{fam}.{out}" for fam, outs in _MULTI_OUTPUT.items() for out in outs
    ]
    raise EvaluationError(
        f"unknown function {dotted!r}. Known: {', '.join(known)}"
    )


def _int_arg(node: Call, value: Any, position: int) -> int:
    name = ".".join(node.path)
    if not isinstance(value, Literal):
        raise EvaluationError(
            f"{name}() argument {position + 1} must be a plain number — an "
            "indicator period cannot vary bar by bar"
        )
        # (A per-bar period would mean a different indicator on every row,
        #  which is not a thing pandas or the reader can follow.)
    if value.value <= 0 or value.value != int(value.value):
        raise EvaluationError(
            f"{name}() argument {position + 1} must be a whole number "
            f"greater than 0, got {value.value:g}"
        )
    return int(value.value)


def _float_arg(node: Call, value: Any, position: int) -> float:
    name = ".".join(node.path)
    if not isinstance(value, Literal):
        raise EvaluationError(
            f"{name}() argument {position + 1} must be a plain number"
        )
    return float(value.value)


def _require_arg_count(node: Call, allowed: tuple[int, ...]) -> None:
    if len(node.args) not in allowed:
        name = ".".join(node.path)
        wanted = " or ".join(str(a) for a in allowed)
        raise EvaluationError(
            f"{name}() takes {wanted} argument(s), got {len(node.args)}"
        )


def _simple_indicator(
    name: str, node: Call, df: pd.DataFrame, variables: Mapping[str, Any]
) -> pd.Series:
    if name == "vwap":
        _require_arg_count(node, (0,))
        return indicators.vwap(df)

    if name in ("sma", "ema"):
        # Two shapes: sma(20) over close, or sma(volume, 20) over anything.
        # The second is how "volume above its own average" is written, and
        # keeping it as a general series argument means it works for any
        # expression, not a fixed list of allowed sources.
        _require_arg_count(node, (1, 2))
        if len(node.args) == 1:
            series = df["close"].astype(float)
            period = _int_arg(node, node.args[0], 0)
        else:
            series = _as_series(_eval(node.args[0], df, variables), df)
            period = _int_arg(node, node.args[1], 1)
        fn = indicators.sma if name == "sma" else indicators.ema
        return fn(series, period)

    if name == "rsi":
        _require_arg_count(node, (1,))
        return indicators.rsi(df["close"].astype(float), _int_arg(node, node.args[0], 0))

    if name == "atr":
        _require_arg_count(node, (1,))
        return indicators.atr(df, _int_arg(node, node.args[0], 0))

    raise EvaluationError(f"no implementation for indicator {name!r}")


def _multi_output_indicator(
    family: str, output: str, node: Call, df: pd.DataFrame,
    variables: Mapping[str, Any],
) -> pd.Series:
    if family == "macd":
        _require_arg_count(node, (3,))
        fast = _int_arg(node, node.args[0], 0)
        slow = _int_arg(node, node.args[1], 1)
        signal = _int_arg(node, node.args[2], 2)
        if fast >= slow:
            raise EvaluationError(
                f"macd fast ({fast}) must be less than slow ({slow})"
            )
        return indicators.macd(df["close"].astype(float), fast, slow, signal)[output]

    if family == "bbands":
        _require_arg_count(node, (2,))
        return indicators.bollinger_bands(
            df["close"].astype(float),
            _int_arg(node, node.args[0], 0),
            _float_arg(node, node.args[1], 1),
        )[output]

    if family == "supertrend":
        _require_arg_count(node, (2,))
        return indicators.supertrend(
            df,
            _int_arg(node, node.args[0], 0),
            _float_arg(node, node.args[1], 1),
        )[output]

    raise EvaluationError(f"no implementation for {family!r}")


# ---------------------------------------------------------------------------
# Coercion
# ---------------------------------------------------------------------------


def _nan_series(df: pd.DataFrame) -> pd.Series:
    return pd.Series(np.nan, index=df.index, dtype="float64")


def _as_series(value: Any, df: pd.DataFrame) -> pd.Series:
    """Bring any evaluated value onto `df`'s index as a Series."""
    if isinstance(value, pd.Series):
        if value.index.equals(df.index):
            return value
        return value.reindex(df.index)
    if isinstance(value, (int, float, np.floating, np.integer, bool, np.bool_)):
        return pd.Series(value, index=df.index)
    raise EvaluationError(
        f"cannot use a value of type {type(value).__name__} in an expression"
    )


def _as_bool(value: Any, df: pd.DataFrame) -> pd.Series:
    """Coerce to booleans under the engine-wide rule: NaN is False.

    Every condition in this system treats an absent value as "no", so a
    warm-up bar cannot open a position. Doing it here keeps `and`/`or`/`not`
    consistent with the v2 condition evaluator.
    """
    series = _as_series(value, df)
    if series.dtype == bool:
        return series
    return series.fillna(0).astype(bool) if series.notna().any() \
        else pd.Series(False, index=df.index)
