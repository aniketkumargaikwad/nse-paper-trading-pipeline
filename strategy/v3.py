"""The v3 strategy document: a state machine over expressions.

WHY THIS SHAPE
--------------
v2 models a strategy as a boolean evaluated per candle. That is exactly right
for "RSI above 55" and structurally incapable of "wait for the sweep, then
watch for a confirming candle, then enter on a break of THAT candle's low" —
because there is nowhere to keep "that candle". The entry rule and the stop
rule both need to refer to a bar the strategy noticed earlier, and a boolean
has no memory.

So v3 keeps the YAML surface and changes the execution model:

    states + transitions + named variables + expressions

Everything v2 could say, v3 can say as a one-state machine, which is what
makes the migration mechanical rather than a rewrite.

WHAT IS VALIDATED HERE, AND WHY EACH ONE
----------------------------------------
Every check below exists because the failure it prevents is SILENT:

* unknown `goto`        -> the machine strands in a state it can never leave,
                           and reports "no trades" like an honest strategy
* unknown `initial`     -> same, from bar zero
* unreachable state     -> a rename applied in one place; the branch you
                           think you are testing never runs
* variable read before
  any branch sets it    -> resolves to nothing, condition never fires, and
                           the backtest looks like a strategy with no setups
* duplicate state name  -> the second silently shadows the first

None of these would raise at runtime. All of them would produce a clean,
plausible, wrong result — which is the only kind of bug that really costs you.

Pure module: no pandas, no I/O. Execution lives in `state_runner.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from strategy.expr import (
    Expr,
    ExpressionError,
    calls_used,
    names_used,
    parse_expression,
)
from strategy.parse import (
    RiskConfig,
    SessionConfig,
    SizingConfig,
    StrategyConfigError,
    _parse_risk,
    _parse_session,
    _parse_sizing,
)
from config import SUPPORTED_TIMEFRAMES
from strategy.vocabulary import (
    CANDLE_FIELDS,
    EXPR_MULTI_OUTPUT,
    EXPR_SIMPLE_INDICATORS,
    EXPR_STRUCTURE,
    HIGHER_TIMEFRAME_PREFIXES,
    INSTRUMENT_RE,
    UNIVERSE_RE,
)
from strategy.vocabulary import POSITION_FIELDS as _VOCAB_POSITION_FIELDS

CURRENT_V3_VERSION = 3

# What a machine may ask about the position it is managing. Without these a
# machine has no way to notice that a stop already took it out, and would sit
# in `in_position` forever while the simulator moved on — the two halves of
# the system disagreeing about reality, silently.
POSITION_FIELDS = _VOCAB_POSITION_FIELDS

# Sides a transition may open. Kept separate from v2's POSITION_TYPES because
# a machine may enter either way regardless of a document-level declaration.
ENTRY_SIDES = frozenset({"long", "short"})


class StateMachineError(ValueError):
    """A v3 document is not a runnable machine. The message names the state."""


# ---------------------------------------------------------------------------
# In-memory model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EntryAction:
    """Open a position. `stop` and `target` are expressions, not percentages.

    This is the point of the whole exercise: a stop can be "confirm_high" —
    a level the strategy remembered — instead of only a percentage from entry.
    """

    side: str
    stop: Expr | None = None
    target: Expr | None = None


@dataclass(frozen=True)
class ExitAction:
    reason: str = "rule"


@dataclass(frozen=True)
class Transition:
    when: Expr
    goto: str
    sets: tuple[tuple[str, Expr], ...] = ()
    enter: EntryAction | None = None
    exit: ExitAction | None = None


@dataclass(frozen=True)
class Timeout:
    bars: int
    goto: str


@dataclass(frozen=True)
class State:
    name: str
    transitions: tuple[Transition, ...] = ()
    timeout: Timeout | None = None


@dataclass(frozen=True)
class StateMachine:
    name: str
    timeframe: str
    initial: str
    states: tuple[State, ...]
    variables: frozenset[str] = field(default_factory=frozenset)
    # Where the machine goes when the position closes for ANY reason — its own
    # `exit:`, or a stop, target or square-off applied by the simulator.
    # Defaults to `initial`, so a machine that never mentions it still cannot
    # strand itself after a stop-out.
    on_position_closed: str = ""
    # Execution config, parsed by v2's own validators so the two formats
    # cannot disagree about what a valid risk block or sizing rule is.
    risk: RiskConfig | None = None
    sizing: SizingConfig | None = None
    session: SessionConfig | None = None
    max_cycles_per_day: int = 1
    instruments: tuple[str, ...] = ()
    universe: str | None = None
    enabled: bool = True
    raw: Mapping[str, Any] = field(default_factory=dict)

    def state(self, name: str) -> State:
        for candidate in self.states:
            if candidate.name == name:
                return candidate
        raise StateMachineError(f"no state named {name!r}")

    @property
    def position_type(self) -> str:
        """'long', 'short', or 'both'.

        Derived rather than declared: a machine enters per transition, so
        unlike a v2 strategy it can legitimately do both. Reporting a single
        declared side would be a claim the document never made — 'both' says
        what is true, and the per-trade side is recorded on each trade anyway.
        """
        sides = {
            t.enter.side
            for state in self.states
            for t in state.transitions
            if t.enter is not None
        }
        if len(sides) == 1:
            return next(iter(sides))
        return "both" if sides else "long"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _fail(where: str, message: str) -> None:
    raise StateMachineError(f"{where}: {message}")


def _mapping(node: Any, where: str) -> dict:
    if not isinstance(node, Mapping):
        _fail(where, f"expected a mapping, got {type(node).__name__}")
    return dict(node)


def _expression(source: Any, where: str) -> Expr:
    if not isinstance(source, str):
        _fail(where, f"expected an expression in quotes, got {source!r}")
    try:
        return parse_expression(source)
    except ExpressionError as exc:
        # The parser's message already carries the position and the text; the
        # state name is what it cannot know and what the reader needs most.
        _fail(where, str(exc))
        raise  # unreachable, keeps type checkers honest


def parse_machine(doc: Any) -> StateMachine:
    """Validate a v3 document into a runnable StateMachine."""
    doc = _mapping(doc, "strategy")

    version = doc.get("version")
    if version != CURRENT_V3_VERSION:
        raise StateMachineError(
            f"expected version {CURRENT_V3_VERSION}, got {version!r}. "
            "A v2 document is migrated with strategy.migrate, not parsed here."
        )

    name = doc.get("name")
    if not isinstance(name, str) or not name.strip():
        raise StateMachineError("strategy: 'name' must be a non-empty string")

    timeframe = doc.get("timeframe")
    if timeframe not in SUPPORTED_TIMEFRAMES:
        _fail(
            f"strategy {name!r}",
            f"unsupported timeframe {timeframe!r}. "
            f"Allowed: {', '.join(SUPPORTED_TIMEFRAMES)}",
        )

    raw_states = doc.get("states")
    if not isinstance(raw_states, list) or not raw_states:
        _fail(f"strategy {name!r}", "'states' must be a non-empty list")

    states: list[State] = []
    seen: set[str] = set()
    for i, raw_state in enumerate(raw_states):
        state = _parse_state(raw_state, f"strategy {name!r}.states[{i}]")
        if state.name in seen:
            _fail(
                f"strategy {name!r}",
                f"two states are both named {state.name!r}; the second would "
                "silently shadow the first",
            )
        seen.add(state.name)
        states.append(state)

    initial = doc.get("initial")
    if initial not in seen:
        _fail(
            f"strategy {name!r}",
            f"'initial' is {initial!r}, which is not one of the declared "
            f"states: {', '.join(sorted(seen))}",
        )

    on_closed = doc.get("on_position_closed", initial)
    if on_closed not in seen:
        _fail(
            f"strategy {name!r}",
            f"'on_position_closed' is {on_closed!r}, which is not one of the "
            f"declared states: {', '.join(sorted(seen))}",
        )

    _check_transitions_target_real_states(states, seen, name)
    _check_every_state_is_reachable(states, initial, name, extra_roots={on_closed})
    variables = _check_variables_are_set_before_use(states, initial, name)

    where = f"strategy {name!r}"
    try:
        risk = _parse_risk(doc.get("risk"), f"{where}.risk")
        sizing = _parse_sizing(doc.get("sizing"), f"{where}.sizing")
        session = _parse_session(doc.get("session", {}), f"{where}.session")
    except StrategyConfigError as exc:
        # v2's validators raise their own type; a caller parsing a v3
        # document should only ever have to catch StateMachineError.
        raise StateMachineError(str(exc)) from exc

    instruments, universe = _parse_symbols(doc, where)

    max_cycles = doc.get("max_cycles_per_day", 1)
    if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1:
        _fail(f"{where}.max_cycles_per_day", f"expected a whole number >= 1, got {max_cycles!r}")

    enabled = doc.get("enabled", True)
    if not isinstance(enabled, bool):
        _fail(f"{where}.enabled", f"expected true or false, got {enabled!r}")

    return StateMachine(
        name=name,
        timeframe=timeframe,
        initial=initial,
        states=tuple(states),
        variables=frozenset(variables),
        on_position_closed=on_closed,
        risk=risk,
        sizing=sizing,
        session=session,
        max_cycles_per_day=max_cycles,
        instruments=tuple(instruments),
        universe=universe,
        enabled=enabled,
        raw=doc,
    )


def _parse_symbols(doc: Mapping[str, Any], where: str) -> tuple[list[str], str | None]:
    """Exactly one of 'universe' or 'instruments', same rule as v2."""
    has_universe = "universe" in doc
    has_instruments = "instruments" in doc
    if has_universe == has_instruments:
        _fail(
            where,
            "a strategy needs exactly ONE of 'universe' (a named symbol group "
            "like NIFTY100) or 'instruments' (an explicit list)",
        )

    if has_universe:
        universe = doc["universe"]
        if not isinstance(universe, str) or not UNIVERSE_RE.match(universe):
            _fail(f"{where}.universe", f"expected a universe name like NIFTY100, got {universe!r}")
        return [], universe

    raw = doc["instruments"]
    if not isinstance(raw, list) or not raw:
        _fail(f"{where}.instruments", "expected a non-empty list like [NSE:RELIANCE]")
    instruments: list[str] = []
    for i, inst in enumerate(raw):
        if not isinstance(inst, str) or not INSTRUMENT_RE.match(inst):
            _fail(
                f"{where}.instruments[{i}]",
                f"expected 'EXCHANGE:TRADINGSYMBOL' (e.g. NSE:RELIANCE), got {inst!r}",
            )
        if inst in instruments:
            _fail(f"{where}.instruments[{i}]", f"duplicate instrument {inst!r}")
        instruments.append(inst)
    return instruments, None


def _parse_state(node: Any, where: str) -> State:
    node = _mapping(node, where)
    name = node.get("name")
    if not isinstance(name, str) or not name.strip():
        _fail(where, "'name' must be a non-empty string")

    # YAML 1.1 reads a bare `on:` as the boolean True, so a document written
    # with `on:` arrives here with True as a key and its transitions
    # invisible — the state would silently have none and the machine would
    # strand. Caught by name rather than left to fail as "no transitions".
    if True in node:
        _fail(
            where,
            "found a key that YAML read as the boolean `true` — almost "
            "certainly `on:`, which YAML 1.1 treats as true rather than as "
            "the word. Use `transitions:` instead.",
        )

    unknown = set(node) - {"name", "transitions", "timeout"}
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(sorted(str(u) for u in unknown))}")

    raw_transitions = node.get("transitions") or []
    if not isinstance(raw_transitions, list):
        _fail(f"{where}.transitions", "expected a list of transitions")

    transitions = tuple(
        _parse_transition(item, f"state {name!r}.transitions[{i}]")
        for i, item in enumerate(raw_transitions)
    )

    timeout = None
    if "timeout" in node:
        timeout = _parse_timeout(node["timeout"], f"state {name!r}.timeout")

    return State(name=name, transitions=transitions, timeout=timeout)


def _parse_timeout(node: Any, where: str) -> Timeout:
    node = _mapping(node, where)
    unknown = set(node) - {"bars", "goto"}
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(sorted(unknown))}")
    bars = node.get("bars")
    if isinstance(bars, bool) or not isinstance(bars, int) or bars <= 0:
        _fail(where, f"'bars' must be a whole number greater than 0, got {bars!r}")
    goto = node.get("goto")
    if not isinstance(goto, str):
        _fail(where, "'goto' is required")
    return Timeout(bars=bars, goto=goto)


def _parse_transition(node: Any, where: str) -> Transition:
    node = _mapping(node, where)
    unknown = set(node) - {"when", "goto", "set", "enter", "exit"}
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(sorted(unknown))}")

    if "when" not in node:
        _fail(where, "'when' is required")
    when = _expression(node["when"], f"{where}.when")

    goto = node.get("goto")
    if not isinstance(goto, str) or not goto.strip():
        _fail(where, "'goto' is required — say which state this moves to")

    sets: list[tuple[str, Expr]] = []
    for key, source in _mapping(node.get("set") or {}, f"{where}.set").items():
        if not isinstance(key, str) or not key.isidentifier() or key.startswith("_"):
            _fail(f"{where}.set", f"{key!r} is not a usable variable name")
        sets.append((key, _expression(source, f"{where}.set.{key}")))

    enter = None
    if "enter" in node:
        enter = _parse_entry(node["enter"], f"{where}.enter")

    exit_action = None
    if "exit" in node:
        exit_node = _mapping(node["exit"], f"{where}.exit")
        unknown_exit = set(exit_node) - {"reason"}
        if unknown_exit:
            _fail(f"{where}.exit", f"unknown key(s): {', '.join(sorted(unknown_exit))}")
        exit_action = ExitAction(reason=str(exit_node.get("reason", "rule")))

    if enter is not None and exit_action is not None:
        _fail(where, "a transition cannot both enter and exit on the same bar")

    return Transition(
        when=when, goto=goto, sets=tuple(sets),
        enter=enter, exit=exit_action,
    )


def _parse_entry(node: Any, where: str) -> EntryAction:
    node = _mapping(node, where)
    unknown = set(node) - {"side", "stop", "target"}
    if unknown:
        _fail(where, f"unknown key(s): {', '.join(sorted(unknown))}")

    side = node.get("side")
    if side not in ENTRY_SIDES:
        _fail(where, f"'side' must be one of {', '.join(sorted(ENTRY_SIDES))}, got {side!r}")

    return EntryAction(
        side=side,
        stop=_expression(node["stop"], f"{where}.stop") if "stop" in node else None,
        target=_expression(node["target"], f"{where}.target") if "target" in node else None,
    )


# ---------------------------------------------------------------------------
# Whole-machine checks
# ---------------------------------------------------------------------------


def _check_transitions_target_real_states(
    states: list[State], known: set[str], name: str
) -> None:
    for state in states:
        for transition in state.transitions:
            if transition.goto not in known:
                _fail(
                    f"strategy {name!r}.{state.name}",
                    f"goto {transition.goto!r} is not a declared state "
                    f"(have: {', '.join(sorted(known))})",
                )
        if state.timeout and state.timeout.goto not in known:
            _fail(
                f"strategy {name!r}.{state.name}.timeout",
                f"goto {state.timeout.goto!r} is not a declared state",
            )


def _check_every_state_is_reachable(
    states: list[State], initial: str, name: str,
    extra_roots: set[str] | None = None,
) -> None:
    """A state nothing can reach is a branch that will never be tested.

    In practice this is a rename applied in one place, and the symptom is a
    strategy that quietly behaves like a simpler one.
    """
    reachable = {initial} | (extra_roots or set())
    frontier = list(reachable)
    by_name = {s.name: s for s in states}
    while frontier:
        state = by_name[frontier.pop()]
        targets = [t.goto for t in state.transitions]
        if state.timeout:
            targets.append(state.timeout.goto)
        for target in targets:
            if target not in reachable:
                reachable.add(target)
                frontier.append(target)

    orphans = sorted({s.name for s in states} - reachable)
    if orphans:
        _fail(
            f"strategy {name!r}",
            f"state(s) {', '.join(repr(o) for o in orphans)} cannot be reached "
            f"from {initial!r}. Nothing would ever run them.",
        )


def _check_variables_are_set_before_use(
    states: list[State], initial: str, name: str
) -> set[str]:
    """Every variable read must be set on some path that reaches the reader.

    Approximated deliberately: a variable counts as available if ANY branch
    sets it before the state that reads it is reachable. Proving it on every
    path would need full dataflow analysis, and the failure this guards
    against — a name nothing ever sets — is caught by the looser rule.
    """
    assigned: set[str] = set()
    for state in states:
        for transition in state.transitions:
            assigned.update(key for key, _ in transition.sets)

    builtin_prefixes = {"candle", "position", *HIGHER_TIMEFRAME_PREFIXES}
    for state in states:
        for transition in state.transitions:
            sources = [transition.when]
            sources.extend(expr for _, expr in transition.sets)
            if transition.enter:
                sources.extend(
                    e for e in (transition.enter.stop, transition.enter.target)
                    if e is not None
                )
            for expr in sources:
                for path in calls_used(expr):
                    _check_call(path, f"strategy {name!r}.{state.name}")
                for path in names_used(expr):
                    if len(path) > 1:
                        if path[0] not in builtin_prefixes:
                            _fail(
                                f"strategy {name!r}.{state.name}",
                                f"unknown reference {'.'.join(path)!r}",
                            )
                        if path[0] == "candle" and path[1] not in CANDLE_FIELDS:
                            _fail(
                                f"strategy {name!r}.{state.name}",
                                f"unknown candle field {path[1]!r}. Known: "
                                f"{', '.join(sorted(CANDLE_FIELDS))}",
                            )
                        if path[0] == "position" and path[1] not in POSITION_FIELDS:
                            _fail(
                                f"strategy {name!r}.{state.name}",
                                f"unknown position field {path[1]!r}. Known: "
                                f"{', '.join(sorted(POSITION_FIELDS))}",
                            )
                        continue
                    bare = path[0]
                    if bare in _BUILTIN_SERIES or bare in assigned:
                        continue
                    _fail(
                        f"strategy {name!r}.{state.name}",
                        f"{bare!r} is read but never set by any transition. "
                        "A variable nothing assigns evaluates to nothing, so "
                        "the condition simply never fires.",
                    )
    return assigned


_BUILTIN_SERIES = frozenset({"open", "high", "low", "close", "volume"})


def _known_calls() -> list[str]:
    """Every function spelling an expression may use."""
    names = [f"{n}()" for n in sorted(EXPR_SIMPLE_INDICATORS)]
    for family, outputs in sorted(EXPR_MULTI_OUTPUT.items()):
        names += [f"{family}.{o}()" for o in outputs]
    for family, outputs in sorted(EXPR_STRUCTURE.items()):
        names += [f"{family}.{o}()" for o in outputs]
    return names


def _check_call(path: tuple[str, ...], where: str) -> None:
    """Reject an unknown function at PARSE time.

    It would otherwise raise mid-backtest, after minutes of fetching, on
    whichever symbol happened to reach that transition first — and a machine
    whose entry rule cannot evaluate is not a strategy that found no setups.
    """
    # A timeframe prefix wraps a call rather than being one: daily.ema(20) is
    # ema(20) evaluated on daily bars.
    if path and path[0] in HIGHER_TIMEFRAME_PREFIXES:
        path = path[1:]
        if not path:
            _fail(where, "a timeframe prefix needs something after it")

    if len(path) == 1:
        if path[0] in EXPR_SIMPLE_INDICATORS:
            return
        if path[0] in EXPR_MULTI_OUTPUT:
            _fail(
                where,
                f"{path[0]} produces several series, so it needs one named: "
                f"write {path[0]}.{EXPR_MULTI_OUTPUT[path[0]][0]}(...)",
            )
    elif len(path) == 2:
        family, output = path
        for registry in (EXPR_MULTI_OUTPUT, EXPR_STRUCTURE):
            if family in registry:
                if output in registry[family]:
                    return
                _fail(
                    where,
                    f"unknown output {output!r} for {family}. Known: "
                    f"{', '.join(registry[family])}",
                )

    _fail(
        where,
        f"unknown function {'.'.join(path)!r}. Known: "
        f"{', '.join(_known_calls())}",
    )
