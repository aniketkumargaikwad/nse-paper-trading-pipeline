"""Backwards-compatible entry point for strategy validation.

The implementation moved to the `strategy/` package. This module re-exports the
public API so existing imports (backtest.py, paper_engine.py, db.py, app_pages/)
and the CLI keep working unchanged::

    .venv\\Scripts\\python.exe strategy_schema.py [path/to/strategies.yaml]

New code should import from `strategy` directly.
"""

from __future__ import annotations

import sys

from strategy.parse import (
    Condition,
    ConditionGroup,
    Operand,
    RiskConfig,
    SizingConfig,
    Strategy,
    StrategyConfigError,
    load_strategies,
    load_strategy_documents,
    parse_strategies,
    parse_strategy_dict,
    strategy_to_raw,
)
from strategy.vocabulary import (
    ALL_OPERATORS,
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    DEFAULT_OUTPUT,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    INSTRUMENT_RE,
    POSITION_TYPES,
    PRICE_SOURCES,
    SIZING_TYPES,
    SOURCE_ALLOWED_FOR,
)

__all__ = [
    "ALL_OPERATORS", "COMPARISON_OPERATORS", "CROSS_OPERATORS",
    "Condition", "ConditionGroup", "DEFAULT_OUTPUT", "INDICATOR_OUTPUTS",
    "INDICATOR_PARAMS", "INSTRUMENT_RE", "Operand", "POSITION_TYPES",
    "PRICE_SOURCES", "RiskConfig", "SIZING_TYPES", "SOURCE_ALLOWED_FOR",
    "SizingConfig", "Strategy", "StrategyConfigError", "load_strategies",
    "load_strategy_documents", "parse_strategies", "parse_strategy_dict",
    "strategy_to_raw",
]


def main(argv: list[str]) -> int:
    """CLI entry point: validate a strategies file and print a summary."""
    path = argv[1] if len(argv) > 1 else "strategies.yaml"
    try:
        strategies = load_strategies(path)
    except (StrategyConfigError, FileNotFoundError) as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1

    print(f"OK: {path} is valid. {len(strategies)} strateg{'y' if len(strategies) == 1 else 'ies'} defined:")
    for s in strategies:
        state = "enabled" if s.enabled else "DISABLED"
        print(
            f"  - {s.name} [{state}] {s.position_type} {s.timeframe} "
            f"on {len(s.instruments)} instrument(s), "
            f"SL {s.risk.stop_loss_pct}% / target {s.risk.target_pct}%, "
            f"max {s.max_cycles_per_day} cycle(s)/day"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
