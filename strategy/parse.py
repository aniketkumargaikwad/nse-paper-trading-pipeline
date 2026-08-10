"""Validation: a raw strategy document becomes a checked Strategy object.

Deliberately strict and deliberately hand-rolled. Every failure names the exact
location (e.g. ``strategies[0].entry.all[1].operator``) and says how to fix it.

Those messages are not a nicety — strategies now arrive pasted in from external
AI tools and are EXPECTED to be wrong on the first attempt, so the correction
loop is the common path rather than the rare one. That is also why this module
is not built on pydantic or jsonschema: their errors are cryptic to a reader who
is not a Python developer.

PURE MODULE: no network, no database, no clock. Validating a pasted strategy
must work offline. In particular, a `universe:` name is validated for SHAPE
here; whether that universe actually exists is checked at save time by the
caller, which has database access (see db.SupabaseStore.save_strategy_document).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Union

import yaml

from config import SUPPORTED_TIMEFRAMES
from strategy.vocabulary import (
    ALL_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)


class StrategyConfigError(ValueError):
    """Raised when strategies.yaml is malformed. Message says where and why."""


# ---------------------------------------------------------------------------
# In-memory representation (what the rest of the system consumes)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Operand:
    """One side of a condition: an indicator (or raw series) to evaluate."""

    indicator: str
    params: dict[str, Any] = field(default_factory=dict)
    source: str = "close"   # only meaningful for ema/sma
    output: str | None = None  # only meaningful for multi-output indicators


@dataclass(frozen=True)
class Condition:
    """`left <operator> (value | right)` evaluated on closed candles only."""

    left: Operand
    operator: str
    value: float | None = None      # exactly one of value / right is set
    right: Operand | None = None


@dataclass(frozen=True)
class ConditionGroup:
    """AND/OR combination of conditions; groups may nest arbitrarily."""

    logic: str  # "all" (AND) or "any" (OR)
    items: tuple[Union[Condition, "ConditionGroup"], ...]


@dataclass(frozen=True)
class RiskConfig:
    stop_loss_pct: float
    target_pct: float


@dataclass(frozen=True)
class SizingConfig:
    type: str
    quantity: int


@dataclass(frozen=True)
class Strategy:
    name: str
    enabled: bool
    position_type: str
    timeframe: str
    instruments: tuple[str, ...]
    entry: ConditionGroup
    exit: ConditionGroup
    risk: RiskConfig
    sizing: SizingConfig
    max_cycles_per_day: int


# ---------------------------------------------------------------------------
# Validation helpers. Every helper takes `where`, a human-readable path like
# "strategies[0].entry.all[1]" used to build precise error messages.
# ---------------------------------------------------------------------------


def _fail(where: str, message: str) -> None:
    raise StrategyConfigError(f"{where}: {message}")


def _require_mapping(node: Any, where: str) -> dict:
    if not isinstance(node, dict):
        _fail(where, f"expected a mapping (key: value lines), got {type(node).__name__}")
    return node


def _require_keys(node: dict, where: str, required: set[str], optional: set[str]) -> None:
    """Reject missing required keys AND unknown keys (typo protection).

    Both problems are reported in ONE message: a typo like `perod: 14`
    produces both a missing key (period) and an unknown key (perod), and
    seeing them side by side is what makes the typo obvious.
    """
    problems: list[str] = []
    missing = sorted(required - node.keys())
    if missing:
        problems.append(f"missing required key(s): {', '.join(missing)}")
    unknown = sorted(node.keys() - required - optional)
    if unknown:
        problems.append(
            f"unknown key(s): {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(required | optional))}"
        )
    if problems:
        _fail(where, "; ".join(problems))


def _parse_operand(node: Any, where: str) -> Operand:
    node = _require_mapping(node, where)
    _require_keys(node, where, required={"indicator"}, optional={"params", "source", "output"})

    indicator = node["indicator"]
    if indicator not in INDICATOR_PARAMS:
        _fail(
            where,
            f"unknown indicator {indicator!r}. "
            f"Supported: {', '.join(sorted(INDICATOR_PARAMS))}",
        )

    # --- params ---
    expected_params = INDICATOR_PARAMS[indicator]
    raw_params = node.get("params") or {}
    if raw_params:
        raw_params = _require_mapping(raw_params, f"{where}.params")
    if expected_params or raw_params:
        # Rejects both missing params (e.g. rsi without period) and unknown
        # ones (e.g. the typo `perod: 14`).
        _require_keys(raw_params, f"{where}.params", required=set(expected_params), optional=set())
    params: dict[str, Any] = {}
    for pname, ptypes in expected_params.items():
        pval = raw_params[pname]
        # bool is a subclass of int in Python; reject it explicitly so
        # `period: true` doesn't silently become period=1.
        if isinstance(pval, bool) or not isinstance(pval, ptypes):
            _fail(
                f"{where}.params.{pname}",
                f"expected {' or '.join(t.__name__ for t in ptypes)}, got {pval!r}",
            )
        if pval <= 0:
            _fail(f"{where}.params.{pname}", f"must be > 0, got {pval}")
        params[pname] = pval

    # --- source (volume SMA etc.) ---
    source = node.get("source", "close")
    if "source" in node:
        if indicator not in SOURCE_ALLOWED_FOR:
            _fail(
                f"{where}.source",
                f"'source' is only allowed on {', '.join(sorted(SOURCE_ALLOWED_FOR))}, "
                f"not on {indicator!r}",
            )
        if source not in PRICE_SOURCES:
            _fail(
                f"{where}.source",
                f"unknown source {source!r}. Allowed: {', '.join(sorted(PRICE_SOURCES))}",
            )

    # --- output (multi-output indicators) ---
    output = node.get("output")
    if "output" in node:
        allowed = INDICATOR_OUTPUTS.get(indicator)
        if allowed is None:
            _fail(
                f"{where}.output",
                f"'output' is not applicable to {indicator!r} "
                f"(only {', '.join(sorted(INDICATOR_OUTPUTS))} have multiple outputs)",
            )
        if output not in allowed:
            _fail(
                f"{where}.output",
                f"unknown output {output!r} for {indicator}. Allowed: {', '.join(allowed)}",
            )
    elif indicator in DEFAULT_OUTPUT:
        output = DEFAULT_OUTPUT[indicator]

    # MACD's slow period must exceed fast, or the indicator is meaningless.
    if indicator == "macd" and params["fast"] >= params["slow"]:
        _fail(f"{where}.params", f"macd 'fast' ({params['fast']}) must be < 'slow' ({params['slow']})")

    return Operand(indicator=indicator, params=params, source=source, output=output)


def _parse_condition(node: dict, where: str) -> Condition:
    # A condition node carries the left operand's keys inline plus
    # operator + (value | compare_to).
    _require_keys(
        node, where,
        required={"indicator", "operator"},
        optional={"params", "source", "output", "value", "compare_to"},
    )

    operator = node["operator"]
    if operator not in ALL_OPERATORS:
        _fail(
            f"{where}.operator",
            f"unknown operator {operator!r}. "
            f"Allowed: {', '.join(sorted(ALL_OPERATORS))}. "
            "Tip: quote comparison operators in YAML, e.g. operator: \">\"",
        )

    has_value = "value" in node
    has_compare = "compare_to" in node
    if has_value == has_compare:  # both present or both absent
        _fail(
            where,
            "a condition needs exactly ONE of 'value' (a fixed number) or "
            "'compare_to' (another indicator)",
        )

    left_keys = {k: node[k] for k in ("indicator", "params", "source", "output") if k in node}
    left = _parse_operand(left_keys, where)

    value: float | None = None
    right: Operand | None = None
    if has_value:
        raw = node["value"]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(f"{where}.value", f"expected a number, got {raw!r}")
        value = float(raw)
    else:
        right = _parse_operand(node["compare_to"], f"{where}.compare_to")

    return Condition(left=left, operator=operator, value=value, right=right)


def _parse_condition_group(node: Any, where: str) -> ConditionGroup:
    node = _require_mapping(node, where)
    if set(node.keys()) not in ({"all"}, {"any"}):
        _fail(
            where,
            f"expected exactly one of 'all:' (AND) or 'any:' (OR) at the top, "
            f"got key(s): {', '.join(sorted(node.keys())) or '(none)'}",
        )
    logic = next(iter(node))
    raw_items = node[logic]
    if not isinstance(raw_items, list) or not raw_items:
        _fail(f"{where}.{logic}", "expected a non-empty list of conditions")

    items: list[Condition | ConditionGroup] = []
    for i, item in enumerate(raw_items):
        item_where = f"{where}.{logic}[{i}]"
        item = _require_mapping(item, item_where)
        # A nested group is a mapping whose only key is all/any.
        if set(item.keys()) <= {"all", "any"} and item:
            items.append(_parse_condition_group(item, item_where))
        else:
            items.append(_parse_condition(item, item_where))
    return ConditionGroup(logic=logic, items=tuple(items))


def _parse_risk(node: Any, where: str) -> RiskConfig:
    node = _require_mapping(node, where)
    _require_keys(node, where, required={"stop_loss_pct", "target_pct"}, optional=set())
    values: dict[str, float] = {}
    for key in ("stop_loss_pct", "target_pct"):
        raw = node[key]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            _fail(f"{where}.{key}", f"expected a number, got {raw!r}")
        if not 0 < raw <= 50:
            # 50% is an arbitrary sanity ceiling: a wider stop on an intraday
            # system is almost certainly a typo (e.g. 70 instead of 0.7).
            _fail(f"{where}.{key}", f"must be between 0 and 50 (percent), got {raw}")
        values[key] = float(raw)
    return RiskConfig(stop_loss_pct=values["stop_loss_pct"], target_pct=values["target_pct"])


def _parse_sizing(node: Any, where: str) -> SizingConfig:
    node = _require_mapping(node, where)
    _require_keys(node, where, required={"type", "quantity"}, optional=set())
    stype = node["type"]
    if stype not in SIZING_TYPES:
        _fail(f"{where}.type", f"unknown sizing type {stype!r}. Allowed: {', '.join(sorted(SIZING_TYPES))}")
    qty = node["quantity"]
    if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
        _fail(f"{where}.quantity", f"expected a whole number >= 1, got {qty!r}")
    return SizingConfig(type=stype, quantity=qty)


def _parse_strategy(node: Any, where: str) -> Strategy:
    node = _require_mapping(node, where)
    _require_keys(
        node, where,
        required={
            "name", "enabled", "position_type", "timeframe",
            "instruments", "entry", "exit", "risk",
        },
        optional={"sizing", "max_cycles_per_day"},
    )

    name = node["name"]
    if not isinstance(name, str) or not name.strip():
        _fail(f"{where}.name", f"expected a non-empty string, got {name!r}")
    name = name.strip()

    enabled = node["enabled"]
    if not isinstance(enabled, bool):
        _fail(f"{where}.enabled", f"expected true or false, got {enabled!r}")

    position_type = node["position_type"]
    if position_type not in POSITION_TYPES:
        _fail(
            f"{where}.position_type",
            f"expected one of {', '.join(sorted(POSITION_TYPES))}, got {position_type!r}",
        )

    timeframe = node["timeframe"]
    if timeframe not in SUPPORTED_TIMEFRAMES:
        _fail(
            f"{where}.timeframe",
            f"unsupported timeframe {timeframe!r}. "
            f"Allowed (15-minute and higher only): {', '.join(SUPPORTED_TIMEFRAMES)}",
        )

    raw_instruments = node["instruments"]
    if not isinstance(raw_instruments, list) or not raw_instruments:
        _fail(f"{where}.instruments", "expected a non-empty list like [NSE:RELIANCE]")
    instruments: list[str] = []
    for i, inst in enumerate(raw_instruments):
        if not isinstance(inst, str) or not INSTRUMENT_RE.match(inst):
            _fail(
                f"{where}.instruments[{i}]",
                f"expected 'EXCHANGE:TRADINGSYMBOL' in capitals "
                f"(e.g. NSE:RELIANCE), got {inst!r}",
            )
        if inst in instruments:
            _fail(f"{where}.instruments[{i}]", f"duplicate instrument {inst!r}")
        instruments.append(inst)

    max_cycles = node.get("max_cycles_per_day", 1)
    if isinstance(max_cycles, bool) or not isinstance(max_cycles, int) or max_cycles < 1:
        _fail(f"{where}.max_cycles_per_day", f"expected a whole number >= 1, got {max_cycles!r}")

    sizing = (
        _parse_sizing(node["sizing"], f"{where}.sizing")
        if "sizing" in node
        else SizingConfig(type="fixed_quantity", quantity=1)
    )

    return Strategy(
        name=name,
        enabled=enabled,
        position_type=position_type,
        timeframe=timeframe,
        instruments=tuple(instruments),
        entry=_parse_condition_group(node["entry"], f"{where}.entry"),
        exit=_parse_condition_group(node["exit"], f"{where}.exit"),
        risk=_parse_risk(node["risk"], f"{where}.risk"),
        sizing=sizing,
        max_cycles_per_day=max_cycles,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_strategies(data: Any) -> list[Strategy]:
    """Validate an already-loaded YAML document and return Strategy objects."""
    root = _require_mapping(data, "(top level)")
    _require_keys(root, "(top level)", required={"version", "strategies"}, optional=set())

    if root["version"] != 1:
        _fail("version", f"unsupported version {root['version']!r}; this code understands version 1")

    raw_strategies = root["strategies"]
    if not isinstance(raw_strategies, list) or not raw_strategies:
        _fail("strategies", "expected a non-empty list of strategies")

    strategies: list[Strategy] = []
    seen_names: set[str] = set()
    for i, raw in enumerate(raw_strategies):
        strategy = _parse_strategy(raw, f"strategies[{i}]")
        if strategy.name in seen_names:
            _fail(
                f"strategies[{i}].name",
                f"duplicate strategy name {strategy.name!r} — names must be "
                "unique because they key positions and trades in the database",
            )
        seen_names.add(strategy.name)
        strategies.append(strategy)
    return strategies


def parse_strategy_dict(raw: Any, where: str = "strategy") -> Strategy:
    """Validate ONE raw strategy dict (the shape of a strategies.yaml entry).

    Used by the database layer and the UI builder, so anything stored or
    entered through a form passes exactly the same validation as the file.
    """
    return _parse_strategy(raw, where)


def strategy_to_raw(strategy: Strategy) -> dict[str, Any]:
    """Convert a Strategy back into its raw dict form.

    Round-trips through parse_strategy_dict, so the UI can load an existing
    strategy into an edit form and the app can export back to YAML.
    """

    def operand_to_raw(op: Operand, *, inline: bool) -> dict[str, Any]:
        node: dict[str, Any] = {"indicator": op.indicator}
        if op.params:
            node["params"] = dict(op.params)
        if op.source != "close":
            node["source"] = op.source
        # Only emit `output` when it differs from the implicit default.
        if op.output is not None and op.output != DEFAULT_OUTPUT.get(op.indicator):
            node["output"] = op.output
        return node

    def condition_to_raw(cond: Condition) -> dict[str, Any]:
        node = operand_to_raw(cond.left, inline=True)
        node["operator"] = cond.operator
        if cond.right is not None:
            node["compare_to"] = operand_to_raw(cond.right, inline=False)
        else:
            node["value"] = cond.value
        return node

    def group_to_raw(group: ConditionGroup) -> dict[str, Any]:
        return {
            group.logic: [
                group_to_raw(item) if isinstance(item, ConditionGroup) else condition_to_raw(item)
                for item in group.items
            ]
        }

    return {
        "name": strategy.name,
        "enabled": strategy.enabled,
        "position_type": strategy.position_type,
        "timeframe": strategy.timeframe,
        "instruments": list(strategy.instruments),
        "entry": group_to_raw(strategy.entry),
        "exit": group_to_raw(strategy.exit),
        "risk": {
            "stop_loss_pct": strategy.risk.stop_loss_pct,
            "target_pct": strategy.risk.target_pct,
        },
        "sizing": {"type": strategy.sizing.type, "quantity": strategy.sizing.quantity},
        "max_cycles_per_day": strategy.max_cycles_per_day,
    }


def load_strategy_documents(path: str = "strategies.yaml") -> list[dict[str, Any]]:
    """Load the raw (but validated) strategy dicts from a YAML file.

    Used to seed the database on first run: the DB stores these raw dicts so
    they can be re-validated on every read by the same code path.
    """
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    parse_strategies(data)  # validate, discard the objects
    return list(data["strategies"])


def load_strategies(path: str = "strategies.yaml") -> list[Strategy]:
    """Load and validate strategies from a YAML file.

    Raises:
        StrategyConfigError: on any structural problem, with location + fix.
        FileNotFoundError: if the file does not exist.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        # PyYAML errors already contain line/column info; wrap for consistency.
        raise StrategyConfigError(
            f"{path} is not valid YAML: {exc}\n"
            "Common causes: inconsistent indentation, a missing ':', or an "
            "unquoted '>' operator (write operator: \">\")."
        ) from exc
    return parse_strategies(data)
