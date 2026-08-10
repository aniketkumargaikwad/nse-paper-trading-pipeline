"""The vocabulary a strategy document may use — the single source of truth.

Pure data, no logic and no imports beyond the standard library, so it can be
read by the parser, by the migration, and by the format-doc generator without
any risk of a circular import.

Anything added here becomes legal in strategies.yaml AND appears in
docs/STRATEGY_FORMAT.md automatically, because that document is generated from
this module. A test asserts the two agree — see tests/test_strategy_format_doc.py.
"""

from __future__ import annotations

import re

# Raw price/volume series need no params.
PRICE_SOURCES: frozenset[str] = frozenset({"close", "open", "high", "low", "volume"})

# indicator name -> {param name -> allowed types}. A param listed here is
# REQUIRED; unknown params are rejected (catches typos like `perod: 14`).
INDICATOR_PARAMS: dict[str, dict[str, tuple[type, ...]]] = {
    "close": {},
    "open": {},
    "high": {},
    "low": {},
    "volume": {},
    "ema": {"period": (int,)},
    "sma": {"period": (int,)},
    "rsi": {"period": (int,)},
    "macd": {"fast": (int,), "slow": (int,), "signal": (int,)},
    "supertrend": {"period": (int,), "multiplier": (int, float)},
    "vwap": {},
    "bbands": {"period": (int,), "std": (int, float)},
    "atr": {"period": (int,)},
}

# Indicators that produce multiple series and therefore accept an `output` key.
INDICATOR_OUTPUTS: dict[str, tuple[str, ...]] = {
    "macd": ("line", "signal", "histogram"),
    "bbands": ("upper", "middle", "lower"),
    "supertrend": ("line", "direction"),
}
DEFAULT_OUTPUT: dict[str, str] = {
    "macd": "line",
    "bbands": "middle",
    "supertrend": "line",
}

# Only moving averages may be computed over a non-close series (this is how
# "volume SMA" is expressed: {indicator: sma, source: volume, ...}).
SOURCE_ALLOWED_FOR: frozenset[str] = frozenset({"ema", "sma"})

COMPARISON_OPERATORS: frozenset[str] = frozenset({">", "<", ">=", "<="})
CROSS_OPERATORS: frozenset[str] = frozenset({"crosses_above", "crosses_below"})
ALL_OPERATORS: frozenset[str] = COMPARISON_OPERATORS | CROSS_OPERATORS

POSITION_TYPES: frozenset[str] = frozenset({"long", "short"})
SIZING_TYPES: frozenset[str] = frozenset({"fixed_quantity"})

# "EXCHANGE:TRADINGSYMBOL", e.g. NSE:RELIANCE or NSE:M&M or NFO:NIFTY24AUGFUT.
INSTRUMENT_RE = re.compile(r"^[A-Z]+:[A-Z0-9&\-]+$")
