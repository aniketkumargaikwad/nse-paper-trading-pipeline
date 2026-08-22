"""Running a v3 state machine over candles.

WHY THIS IS NOT VECTORIZED, AND WHY IT IS STILL FAST
----------------------------------------------------
v2 evaluates a strategy as one boolean Series over the whole frame. A state
machine cannot work that way: which transition is even *considered* at bar i
depends on where the machine was at bar i-1, and a variable captured at bar 3
changes what bar 40 compares against. The bar loop is not an implementation
shortcut, it is the model.

Evaluating each expression bar-by-bar from scratch would be ruinous, though —
`rsi(14)` recomputed 3,000 times per symbol. So this splits the work:

* Any subtree that does NOT mention a variable is computed ONCE over the whole
  frame and memoized. That is every indicator, every price series, every
  `prev_day.*` reference — the expensive part.
* Only the nodes that DO mention a variable are walked per bar, and by then
  they are combining scalars.

The AST nodes are frozen dataclasses, so they hash by value and two identical
subtrees in different transitions share one computed Series for free.

THE SEQUENCING RULE
-------------------
At most one transition fires per bar, and the state it moves to is evaluated
starting on the NEXT bar. Two events a strategy waits for occur on different
bars by definition, so cascading gains nothing — while a cycle in the state
graph would spin forever inside a single candle.

NO LOOK-AHEAD
-------------
Bar i is decided using rows 0..i only. The precomputed series are causal
(that property is tested directly in tests/test_expr_eval.py), and the loop
never reads past its own index.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from expr_eval import evaluate
from strategy.expr import Binary, Call, Expr, Literal, Name, Offset, Unary
from strategy.v3 import StateMachine

# The names that come from the frame rather than from a `set:`.
_BUILTIN_BARE = frozenset({"open", "high", "low", "close", "volume"})


@dataclass(frozen=True)
class PositionView:
    """What the machine is allowed to know about the position it manages.

    The simulator owns the position — it decides fills, stops and square-off —
    so the machine must be able to ASK rather than assume. Without this a
    machine sits in `in_position` after a stop already took it out, and the
    two halves of the system disagree about reality while both look healthy.
    """

    is_open: bool = False
    is_long: bool = False
    entry_price: float = float("nan")
    bars_held: int = 0
    last_price: float = float("nan")

    def field(self, name: str) -> Any:
        if name == "is_open":
            return self.is_open
        if name == "is_long":
            return self.is_open and self.is_long
        if name == "is_short":
            return self.is_open and not self.is_long
        if name == "bars_held":
            return float(self.bars_held)
        if name == "entry_price":
            return self.entry_price
        if name == "pnl_pct":
            if not self.is_open or not self.entry_price or np.isnan(self.entry_price):
                return float("nan")
            move = (self.last_price - self.entry_price) / self.entry_price * 100.0
            return move if self.is_long else -move
        return float("nan")


FLAT_POSITION = PositionView()


@dataclass(frozen=True)
class EntryEvent:
    bar: int
    ts: pd.Timestamp
    side: str
    price: float
    stop: float | None = None
    target: float | None = None
    from_state: str = ""


@dataclass(frozen=True)
class ExitEvent:
    bar: int
    ts: pd.Timestamp
    price: float
    reason: str = "rule"
    from_state: str = ""
    # Below 1 this is a partial: close that share of what is open and let the
    # rest run.
    fraction: float = 1.0


@dataclass
class MachineRun:
    """What the machine did, bar by bar.

    `states[i]` is the state the machine was in ON ENTRY to bar i — that is,
    before bar i's transitions are considered. It therefore reflects only
    information from bars before i, which is what makes it safe to inspect.
    """

    states: list[str] = field(default_factory=list)
    entries: list[EntryEvent] = field(default_factory=list)
    exits: list[ExitEvent] = field(default_factory=list)
    variables_at_end: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Expression evaluation, split by whether variables are involved
# ---------------------------------------------------------------------------


class _Evaluator:
    """Evaluates expression nodes at a single bar, reusing whole-frame work."""

    def __init__(self, df: pd.DataFrame, variable_names: frozenset[str]) -> None:
        self._df = df
        self._variables = variable_names
        self._series: dict[Expr, pd.Series] = {}
        self._var_free: dict[Expr, bool] = {}

    # -- classification -----------------------------------------------------

    def is_variable_free(self, node: Expr) -> bool:
        cached = self._var_free.get(node)
        if cached is not None:
            return cached
        result = self._compute_variable_free(node)
        self._var_free[node] = result
        return result

    def _compute_variable_free(self, node: Expr) -> bool:
        if isinstance(node, Literal):
            return True
        if isinstance(node, Name):
            if len(node.path) == 1 and node.path[0] not in _BUILTIN_BARE:
                return False        # a variable
            if node.path[0] == "position":
                # Only known while stepping: whether a position is open at bar
                # i depends on fills and stops that the simulator decides as
                # it goes, so this can never be precomputed over the frame.
                return False
            return True
        if isinstance(node, Call):
            return all(self.is_variable_free(a) for a in node.args)
        if isinstance(node, (Offset, Unary)):
            return self.is_variable_free(node.operand)
        if isinstance(node, Binary):
            return self.is_variable_free(node.left) and self.is_variable_free(node.right)
        return True

    # -- whole-frame path ---------------------------------------------------

    def series(self, node: Expr) -> pd.Series:
        """The full-frame Series for a variable-free subtree, computed once."""
        cached = self._series.get(node)
        if cached is None:
            cached = evaluate(node, self._df)
            self._series[node] = cached
        return cached

    # -- per-bar path -------------------------------------------------------

    def value_at(
        self, node: Expr, i: int, variables: dict[str, float],
        position: "PositionView | None" = None,
    ) -> Any:
        if self.is_variable_free(node):
            return self.series(node).iloc[i]

        if isinstance(node, Name):
            if node.path[0] == "position":
                view = position if position is not None else FLAT_POSITION
                return view.field(node.path[1])
            # Only bare variable names reach here; anything else is
            # variable-free and took the branch above.
            return variables.get(node.path[0], np.nan)

        if isinstance(node, Offset):
            # A variable is a scalar in force from the bar that set it, not a
            # series with history, so offsetting one has no meaning we could
            # define honestly. The parser cannot see this (it does not know
            # which names are variables), so it is refused here.
            raise ValueError(
                "an offset cannot be applied to an expression that reads a "
                "variable: a variable holds one captured value, not a series "
                "with its own history"
            )

        if isinstance(node, Unary):
            inner = self.value_at(node.operand, i, variables, position)
            if node.op == "-":
                return -_as_float(inner)
            return not _as_bool(inner)

        if isinstance(node, Binary):
            return self._binary_at(node, i, variables, position)

        if isinstance(node, Call):
            raise ValueError(
                f"{'.'.join(node.path)}() cannot take a variable as an "
                "argument: an indicator period must be the same on every bar"
            )

        raise ValueError(f"cannot evaluate {type(node).__name__} at a single bar")

    def _binary_at(
        self, node: Binary, i: int, variables: dict[str, float],
        position: "PositionView | None" = None,
    ) -> Any:
        op = node.op
        if op in ("and", "or"):
            left = _as_bool(self.value_at(node.left, i, variables, position))
            # Short-circuit: mirrors what the vectorized path computes, and
            # skips work when the answer is already settled.
            if op == "and" and not left:
                return False
            if op == "or" and left:
                return True
            return _as_bool(self.value_at(node.right, i, variables, position))

        left = _as_float(self.value_at(node.left, i, variables, position))
        right = _as_float(self.value_at(node.right, i, variables, position))

        if np.isnan(left) or np.isnan(right):
            # Absence is never a signal. A comparison against a variable that
            # has not been captured yet must be False, not an exception and
            # not True.
            return False if op not in ("+", "-", "*", "/") else np.nan

        if op == "+":
            return left + right
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return np.nan if right == 0 else left / right
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
        raise ValueError(f"unknown operator {op!r}")


def _as_float(value: Any) -> float:
    if value is None:
        return float("nan")
    if isinstance(value, (bool, np.bool_)):
        return 1.0 if value else 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _as_bool(value: Any) -> bool:
    """The engine-wide rule: NaN is False, so absence cannot open a trade."""
    if value is None:
        return False
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    number = _as_float(value)
    return False if np.isnan(number) else bool(number)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """What the machine did on one bar."""

    state_on_entry: str
    entry: EntryEvent | None = None
    exit: ExitEvent | None = None
    moved_to: str | None = None


class MachineStepper:
    """Drives a machine one bar at a time.

    Exists as a class rather than a loop so the backtester can interleave the
    machine with its own position management: the simulator needs to apply a
    stop DURING a candle and have the machine see the consequence at that same
    candle's close. A function that owned the loop could not offer that.
    """

    def __init__(self, machine: StateMachine, df: pd.DataFrame) -> None:
        self._machine = machine
        self._df = df
        self._by_name = {state.name: state for state in machine.states}
        self._evaluator = _Evaluator(df, machine.variables)
        self._closes = df["close"].astype(float).to_numpy() if not df.empty else []
        self.state = machine.initial
        self.variables: dict[str, float] = {}
        self.bars_in_state = 0

    def restore(
        self, *, state: str, variables: dict, bars_in_state: int
    ) -> None:
        """Resume a machine that was left mid-setup by an earlier run.

        The paper engine is stateless — it rebuilds its world from the
        database on every tick — so a machine hunting a setup across
        several candles has to be put back exactly where it was, variables
        and timeout counter included. Restoring the state alone would
        restart every `timeout:` and lose every captured level.
        """
        if state not in self._by_name:
            raise ValueError(
                f"cannot resume in unknown state {state!r}; the strategy "
                "was probably edited since this state was saved"
            )
        self.state = state
        self.variables = dict(variables)
        self.bars_in_state = int(bars_in_state)

    def position_closed(self) -> None:
        """Tell the machine the position is gone, however it went.

        Called for a stop, a target, a square-off or an end-of-data close —
        every path except the machine's own `exit:`, which already moved it.
        The default target is the initial state, so a machine that never
        mentions `on_position_closed` still cannot strand itself.
        """
        self.state = self._machine.on_position_closed or self._machine.initial
        self.bars_in_state = 0

    def step(self, i: int, position: PositionView | None = None) -> StepResult:
        """Consider bar i. At most one transition fires."""
        result = StepResult(state_on_entry=self.state)
        state = self._by_name[self.state]

        fired = None
        for transition in state.transitions:
            if _as_bool(
                self._evaluator.value_at(transition.when, i, self.variables, position)
            ):
                fired = transition
                break

        if fired is not None:
            # Captured BEFORE the actions read them, so an entry can refer to
            # a level the very same transition just set.
            for key, expr in fired.sets:
                self.variables[key] = _as_float(
                    self._evaluator.value_at(expr, i, self.variables, position)
                )

            price = float(self._closes[i])
            if fired.enter is not None:
                result.entry = EntryEvent(
                    bar=i, ts=self._df.index[i], side=fired.enter.side,
                    price=price,
                    stop=self._level(fired.enter.stop, i, position),
                    target=self._level(fired.enter.target, i, position),
                    from_state=self.state,
                )
            if fired.exit is not None:
                result.exit = ExitEvent(
                    bar=i, ts=self._df.index[i], price=price,
                    reason=fired.exit.reason, from_state=self.state,
                    fraction=fired.exit.fraction,
                )

            self.state = fired.goto
            self.bars_in_state = 0
            result.moved_to = self.state
            return result

        # Only when nothing fired. A transition and a timeout on the same bar
        # is not a race: the rule the author wrote wins over the fallback.
        self.bars_in_state += 1
        if state.timeout is not None and self.bars_in_state >= state.timeout.bars:
            self.state = state.timeout.goto
            self.bars_in_state = 0
            result.moved_to = self.state
        return result

    def _level(
        self, expr: Expr | None, i: int, position: PositionView | None
    ) -> float | None:
        if expr is None:
            return None
        value = _as_float(self._evaluator.value_at(expr, i, self.variables, position))
        return None if np.isnan(value) else value


def run_machine(machine: StateMachine, df: pd.DataFrame) -> MachineRun:
    """Step `machine` through every bar of `df`, with no position management.

    Useful for inspecting what a machine WOULD signal. The backtester drives
    MachineStepper itself so that stops and fills feed back into the machine.
    """
    run = MachineRun()
    if df.empty:
        return run

    stepper = MachineStepper(machine, df)
    for i in range(len(df)):
        result = stepper.step(i)
        run.states.append(result.state_on_entry)
        if result.entry is not None:
            run.entries.append(result.entry)
        if result.exit is not None:
            run.exits.append(result.exit)

    run.variables_at_end = dict(stepper.variables)
    return run
