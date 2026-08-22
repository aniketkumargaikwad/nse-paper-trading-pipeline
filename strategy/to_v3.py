"""Migrating a v2 strategy into the v3 state-machine format.

THE RULE THAT GOVERNS EVERY CHOICE HERE
---------------------------------------
A migrated strategy must backtest IDENTICALLY to the way it did as v2. Not
"equivalently in spirit" — the same trades, at the same bars, at the same
prices. `tests/test_v2_to_v3.py` asserts exactly that against the engine, and
it is the reason this module refuses rather than approximates whenever a v2
construct has no exact v3 spelling.

That equivalence is also the best evidence available that the v3 stack is
correct: v2's evaluator has 700-odd tests and two years of results behind it,
so a v3 run that matches it trade-for-trade has exercised the expression
parser, the namespace, the state machine and the simulator against a known
good answer.

THE SHAPE
---------
Every v2 strategy is a two-state machine:

    flat    --entry--> holding
    holding --exit-->  flat

with `on_position_closed: flat`, so a stop-out returns it to the same place
the exit rule would have. That is what makes the translation total rather
than clever.
"""

from __future__ import annotations

from typing import Any

from strategy.parse import (
    Condition,
    ConditionGroup,
    Operand,
    Strategy,
    strategy_to_raw,
)
from strategy.v3 import CURRENT_V3_VERSION

# Which expression prefix names each higher timeframe. Only timeframes the
# expression language can actually say are listed; anything else is refused
# below rather than silently dropped, because a trend filter quietly deleted
# from a strategy changes what it trades without changing what it says.
_TIMEFRAME_PREFIX: dict[str, str] = {"day": "daily", "60m": "hourly"}


class MigrationToV3Error(ValueError):
    """A v2 strategy has no exact v3 spelling. Says which part and why."""


# ---------------------------------------------------------------------------
# Operands
# ---------------------------------------------------------------------------


def operand_to_expression(op: Operand, strategy_timeframe: str) -> str:
    """One v2 operand as a v3 expression string."""
    base = _indicator_call(op)

    timeframe = getattr(op, "timeframe", None)
    if timeframe is not None and timeframe != strategy_timeframe:
        prefix = _TIMEFRAME_PREFIX.get(timeframe)
        if prefix is None:
            raise MigrationToV3Error(
                f"operand on timeframe {timeframe!r} cannot be written as a v3 "
                f"expression yet (only {', '.join(sorted(_TIMEFRAME_PREFIX))} "
                "have a name). Migrating it would change what the strategy "
                "tests, so it is refused instead."
            )
        base = f"{prefix}.{base}"

    if op.offset:
        base = f"{base}[{op.offset}]"
    return base


def _indicator_call(op: Operand) -> str:
    name = op.indicator
    params = op.params

    if name in ("close", "open", "high", "low", "volume"):
        return name
    if name == "vwap":
        return "vwap()"
    if name in ("ema", "sma"):
        period = params["period"]
        # A moving average over a non-close series is v2's `source:` key,
        # which v3 spells as an ordinary first argument.
        if op.source and op.source != "close":
            return f"{name}({op.source}, {period})"
        return f"{name}({period})"
    if name in ("rsi", "atr"):
        return f"{name}({params['period']})"
    if name == "macd":
        return (
            f"macd.{op.output}({params['fast']}, {params['slow']}, "
            f"{params['signal']})"
        )
    if name == "bbands":
        return f"bbands.{op.output}({params['period']}, {_number(params['std'])})"
    if name == "supertrend":
        return (
            f"supertrend.{op.output}({params['period']}, "
            f"{_number(params['multiplier'])})"
        )
    raise MigrationToV3Error(f"no v3 spelling for indicator {name!r}")


def _number(value: Any) -> str:
    """Render a number the way the v3 tokenizer reads it back."""
    as_float = float(value)
    return str(int(as_float)) if as_float.is_integer() else repr(as_float)


# ---------------------------------------------------------------------------
# Conditions and groups
# ---------------------------------------------------------------------------


def condition_to_expression(cond: Condition, strategy_timeframe: str) -> str:
    left = operand_to_expression(cond.left, strategy_timeframe)
    if cond.right is not None:
        right = operand_to_expression(cond.right, strategy_timeframe)
    else:
        right = _number(cond.value)

    if cond.operator in (">", "<", ">=", "<="):
        return f"{left} {cond.operator} {right}"

    # A cross is two comparisons: where the pair sat on the previous bar, and
    # where it sits now. v2 spells that as one operator; v3 spells it out.
    # Both sides get the offset, which is why `left`/`right` are rebuilt
    # rather than string-patched — `ema(9)[1]` is correct, `ema(9[1])` is not.
    prev_left = _offset_by_one(cond.left, strategy_timeframe)
    prev_right = (
        _offset_by_one(cond.right, strategy_timeframe)
        if cond.right is not None else right
    )
    if cond.operator == "crosses_above":
        return f"({prev_left} <= {prev_right}) and ({left} > {right})"
    if cond.operator == "crosses_below":
        return f"({prev_left} >= {prev_right}) and ({left} < {right})"
    raise MigrationToV3Error(f"no v3 spelling for operator {cond.operator!r}")


def _offset_by_one(op: Operand, strategy_timeframe: str) -> str:
    """The same operand, read one bar earlier."""
    from dataclasses import replace

    return operand_to_expression(replace(op, offset=op.offset + 1), strategy_timeframe)


def group_to_expression(group: ConditionGroup, strategy_timeframe: str) -> str:
    """An all/any tree as one boolean expression.

    Every member is parenthesised. v2's tree carries its grouping in its
    shape; text carries it only in brackets, and `a and b or c` is not what
    the tree said.
    """
    joiner = " and " if group.logic == "all" else " or "
    parts: list[str] = []
    for item in group.items:
        if isinstance(item, ConditionGroup):
            parts.append(f"({group_to_expression(item, strategy_timeframe)})")
        else:
            parts.append(f"({condition_to_expression(item, strategy_timeframe)})")
    return joiner.join(parts)


# ---------------------------------------------------------------------------
# Whole strategy
# ---------------------------------------------------------------------------


def migrate_v2_to_v3(strategy: Strategy) -> dict[str, Any]:
    """A parsed v2 Strategy as a v3 document (a plain dict, ready to store)."""
    raw = strategy_to_raw(strategy)
    timeframe = strategy.timeframe

    entry = group_to_expression(strategy.entry, timeframe)
    exit_ = group_to_expression(strategy.exit, timeframe)

    doc: dict[str, Any] = {
        "version": CURRENT_V3_VERSION,
        "name": strategy.name,
        "enabled": strategy.enabled,
        "timeframe": timeframe,
        "initial": "flat",
        # A stop-out puts the machine exactly where the exit rule would have.
        "on_position_closed": "flat",
        "states": [
            {
                "name": "flat",
                "on": [{
                    "when": entry,
                    "enter": {"side": strategy.position_type},
                    "goto": "holding",
                }],
            },
            {
                "name": "holding",
                "on": [{"when": exit_, "exit": {}, "goto": "flat"}],
            },
        ],
        "risk": raw["risk"],
        "sizing": raw["sizing"],
        "max_cycles_per_day": strategy.max_cycles_per_day,
    }

    if strategy.universe:
        doc["universe"] = strategy.universe
    else:
        doc["instruments"] = list(strategy.instruments)

    if "session" in raw:
        doc["session"] = raw["session"]

    return doc
