"""Strategy authoring: vocabulary, validation, and version migration.

Import from here rather than from the submodules::

    from strategy import Strategy, StrategyConfigError, parse_strategy_dict
"""

from __future__ import annotations

from strategy.parse import (
    Condition,
    ConditionGroup,
    Operand,
    RiskConfig,
    SizingConfig,
    StopSpec,
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
    "ALL_OPERATORS",
    "COMPARISON_OPERATORS",
    "Condition",
    "ConditionGroup",
    "CROSS_OPERATORS",
    "DEFAULT_OUTPUT",
    "INDICATOR_OUTPUTS",
    "INDICATOR_PARAMS",
    "INSTRUMENT_RE",
    "Operand",
    "POSITION_TYPES",
    "PRICE_SOURCES",
    "RiskConfig",
    "SIZING_TYPES",
    "SizingConfig",
    "StopSpec",
    "SOURCE_ALLOWED_FOR",
    "Strategy",
    "StrategyConfigError",
    "load_strategies",
    "load_strategy_documents",
    "parse_strategies",
    "parse_strategy_dict",
    "strategy_to_raw",
]
