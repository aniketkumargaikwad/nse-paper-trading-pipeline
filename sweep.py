"""Parameter sweeps: one strategy expanded into many, PURE — no I/O, no clock.

A sweep answers "which settings would have worked", and it is the fastest way
to fool yourself in this entire system. Trying 24 combinations and keeping the
best one is not research; it is picking the luckiest sample from a distribution
you generated on purpose. The more combinations tried, the more certain it
becomes that SOMETHING looks good.

So this module does two things and refuses a third. It expands a strategy into
variants, and it says how many chances at a false positive that expansion
bought. It does NOT pick a winner — that judgement belongs to a person looking
at the dispersion and the kill rules, with the count in front of them.

Expansion works on the strategy DOCUMENT (the raw mapping) rather than the
parsed object, so every variant goes through the same validation a hand-written
strategy does. A sweep over `risk.stop_loss.value` that produced an impossible
stop would be caught by the parser, not discovered halfway through a run.
"""

from __future__ import annotations

import copy
from itertools import product
from typing import Any

import yaml

from strategy_schema import Strategy, parse_strategy_dict, strategy_to_raw

# Above this many variants a sweep is almost certainly fishing rather than
# testing a hypothesis. Not a hard law - the CLI lets it be raised deliberately
# - but the default should make the expensive, self-deceiving case a choice.
DEFAULT_MAX_VARIANTS = 24


class SweepError(ValueError):
    """A sweep specification that cannot be applied. Message names the fix."""


def parse_spec(spec: str) -> tuple[list[str | int], list[Any]]:
    """`"risk.stop_loss.value=0.5,0.7,1.0"` -> (path, [0.5, 0.7, 1.0]).

    Values are read as YAML scalars, so `14` is an int, `0.7` a float and
    `"09:30"` a string. Typing them any other way would make `period=14` a
    string and fail validation with a confusing message about the wrong thing.
    """
    if "=" not in spec:
        raise SweepError(
            f"{spec!r} is not a sweep. Write it as path=value1,value2 - "
            "for example risk.stop_loss.value=0.5,0.7,1.0"
        )
    raw_path, raw_values = spec.split("=", 1)
    path = _parse_path(raw_path.strip(), spec)

    values = [v.strip() for v in raw_values.split(",")]
    if not all(values):
        raise SweepError(
            f"{spec!r} has an empty value. Two commas together, or a trailing "
            "comma, usually means one was typed by accident."
        )
    parsed = [yaml.safe_load(v) for v in values]

    seen: list[Any] = []
    for value in parsed:
        if value in seen:
            raise SweepError(
                f"{spec!r} lists {value!r} twice. A repeated value doubles the "
                "run time and adds nothing."
            )
        seen.append(value)
    return path, parsed


def _parse_path(raw: str, spec: str) -> list[str | int]:
    if not raw:
        raise SweepError(f"{spec!r} has no path before the '='.")
    out: list[str | int] = []
    for part in raw.split("."):
        if not part:
            raise SweepError(
                f"{spec!r} has an empty step in its path ('..' or a leading dot)."
            )
        # A digit-only step indexes a list, which is how a condition inside
        # `entry.all` is reached: entry.all.0.params.period.
        out.append(int(part) if part.isdigit() else part)
    return out


def apply_override(document: Any, path: list[str | int], value: Any) -> Any:
    """A COPY of `document` with `path` set to `value`.

    The original is never touched: a sweep builds many variants from one
    document, and mutating it in place would make every variant inherit the
    previous one's values - a bug that produces plausible, wrong results
    rather than an error.
    """
    out = copy.deepcopy(document)
    node: Any = out
    for step in path[:-1]:
        node = _step_into(node, step, path)
    _assign(node, path[-1], path, value)
    return out


def _step_into(node: Any, step: str | int, path: list[str | int]) -> Any:
    where = ".".join(str(p) for p in path)
    if isinstance(step, int):
        if not isinstance(node, list):
            raise SweepError(
                f"{where}: step {step!r} indexes a list, but this is a "
                f"{type(node).__name__}."
            )
        if step >= len(node):
            raise SweepError(
                f"{where}: index {step} is past the end - there "
                f"{'is' if len(node) == 1 else 'are'} {len(node)}."
            )
        return node[step]
    if not isinstance(node, dict):
        raise SweepError(
            f"{where}: step {step!r} expects a mapping, but this is a "
            f"{type(node).__name__}."
        )
    if step not in node:
        available = ", ".join(sorted(str(k) for k in node)) or "(nothing)"
        raise SweepError(
            f"{where}: no key {step!r} here. Available: {available}. "
            "A sweep changes an existing setting; it does not add one."
        )
    return node[step]


def _assign(node: Any, step: str | int, path: list[str | int], value: Any = ...) -> None:
    """Check the final step exists, and set it when a value is given.

    Sweeping a key that does not exist is refused rather than created. A typo
    like `risk.stop_los.value` would otherwise add a key the parser rejects
    with a message about the typo, not about the sweep - or worse, be accepted
    somewhere unvalidated and quietly change nothing.
    """
    where = ".".join(str(p) for p in path)
    if isinstance(step, int):
        if not isinstance(node, list) or step >= len(node):
            raise SweepError(f"{where}: index {step} does not exist.")
        if value is not ...:
            node[step] = value
        return
    if not isinstance(node, dict):
        raise SweepError(
            f"{where}: expected a mapping at the end of the path, found a "
            f"{type(node).__name__}."
        )
    if step not in node:
        available = ", ".join(sorted(str(k) for k in node)) or "(nothing)"
        raise SweepError(
            f"{where}: no key {step!r} to sweep. Available here: {available}. "
            "A sweep changes an existing setting; it does not add one."
        )
    if value is not ...:
        node[step] = value


def variant_name(base: str, assignments: list[tuple[str, Any]]) -> str:
    """`"EMA-RSI [stop_loss.value=0.7, target.value=1.5]"`.

    The full path is shortened to its last two steps: `risk.stop_loss.value`
    and `entry.all.0.params.period` both stay legible while remaining specific
    enough to tell two swept parameters apart in a results table.
    """
    parts = []
    for path, value in assignments:
        steps = path.split(".")
        label = ".".join(steps[-2:]) if len(steps) > 1 else path
        parts.append(f"{label}={value}")
    return f"{base} [{', '.join(parts)}]"


def expand(strategy: Strategy, specs: list[str]) -> list[Strategy]:
    """One strategy and N sweep specs -> the full grid of variants.

    Every variant is re-parsed, so a combination that produces an invalid
    strategy fails HERE, naming the parameter, rather than partway through a
    run that has already spent minutes fetching candles.
    """
    if not specs:
        return [strategy]

    parsed = [parse_spec(s) for s in specs]
    paths = [".".join(str(p) for p in path) for path, _ in parsed]

    if len(set(paths)) != len(paths):
        repeated = sorted({p for p in paths if paths.count(p) > 1})
        raise SweepError(
            f"the same parameter is swept twice: {', '.join(repeated)}. "
            "List all of its values in one --sweep instead."
        )

    base_document = strategy_to_raw(strategy)
    variants: list[Strategy] = []
    for combination in product(*[values for _, values in parsed]):
        document = base_document
        for (path, _), value in zip(parsed, combination):
            document = apply_override(document, path, value)
        assignments = list(zip(paths, combination))
        document["name"] = variant_name(strategy.name, assignments)
        variants.append(parse_strategy_dict(document, where=document["name"]))
    return variants


def count_variants(specs: list[str]) -> int:
    """How many runs a sweep would perform, without building them."""
    total = 1
    for spec in specs:
        _, values = parse_spec(spec)
        total *= len(values)
    return total


def false_positive_warning(variants: int, kill_rule_threshold: float = 0.05) -> str:
    """What trying `variants` combinations does to a passing result.

    Stated as a probability rather than a caution, because "be careful of
    overfitting" is advice everyone agrees with and nobody acts on, while "16
    tries makes a 1-in-20 fluke more likely than not" is a number that changes
    a decision.
    """
    if variants < 2:
        return ""
    chance = 1.0 - (1.0 - kill_rule_threshold) ** variants
    # Tense-neutral on purpose: the same sentence is shown before a sweep, as a
    # reason to narrow the grid, and after it, as a caveat on the winner.
    return (
        f"Across {variants} combinations: if any single one has a "
        f"{kill_rule_threshold:.0%} chance of passing by luck alone, the "
        f"chance that AT LEAST ONE of them does is about {chance:.0%}. "
        "Treat the best result as a hypothesis to test on a period this "
        "sweep never saw - not as a finding."
    )
