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

# How many closed bars back an operand may look. `offset: 1` is the previous
# bar, which is what "breaks yesterday's high" needs on a daily strategy and
# what "higher than the last bar" needs on any timeframe.
#
# There is no negative offset and there never can be: a negative offset reads
# a bar that has not closed, which is look-ahead bias expressed as
# configuration. The parser rejects it rather than trusting anyone to
# remember.
#
# The cap is a typo guard, not a real limit. An offset beyond it is far more
# likely a period pasted into the wrong key than a genuine intent to read
# four years back — and every extra bar of offset costs a bar of warm-up
# before the strategy can fire at all.
MAX_OFFSET = 500

# v3 expression namespaces. Kept HERE, in the pure vocabulary module, because
# two places knowing the list is how `daily.high` ends up evaluating fine and
# failing validation — which is exactly what happened before this moved.
#
# prefix -> the timeframe it aggregates to.
HIGHER_TIMEFRAME_PREFIXES: dict[str, str] = {
    "prev_day": "day",
    "daily": "day",
    "hourly": "60m",
}

# Non-timeframe dotted prefixes an expression may use.
CANDLE_FIELDS: frozenset[str] = frozenset({
    "is_bullish", "is_bearish", "body", "range", "upper_wick", "lower_wick",
})

POSITION_FIELDS: frozenset[str] = frozenset({
    "is_open", "is_long", "is_short", "bars_held", "entry_price", "pnl_pct",
})

# Functions a v3 expression may call. Here rather than in the evaluator for
# the same reason as the prefixes above: the validator, the evaluator and the
# generated format document all read this, and a list kept in three places is
# a list that will disagree with itself.
#
# name -> how many arguments it accepts.
EXPR_SIMPLE_INDICATORS: dict[str, tuple[int, ...]] = {
    "sma": (1, 2),      # sma(20) over close, or sma(volume, 20)
    "ema": (1, 2),
    "rsi": (1,),
    "atr": (1,),
    "vwap": (0,),
}

# name -> the outputs it produces, each spelled as a dotted call.
EXPR_MULTI_OUTPUT: dict[str, tuple[str, ...]] = {
    "macd": ("line", "signal", "histogram"),
    "bbands": ("upper", "middle", "lower"),
    "supertrend": ("line", "direction"),
}

# Operators the expression grammar accepts, loosest-binding first. Used by the
# generated document so the precedence table cannot drift from the parser.
EXPR_PRECEDENCE: tuple[tuple[str, str], ...] = (
    ("or", "either side true"),
    ("and", "both sides true"),
    ("not", "negates what follows"),
    ("< > <= >= == !=", "comparison"),
    ("+ -", "add, subtract"),
    ("* /", "multiply, divide"),
)

COMPARISON_OPERATORS: frozenset[str] = frozenset({">", "<", ">=", "<="})
CROSS_OPERATORS: frozenset[str] = frozenset({"crosses_above", "crosses_below"})
ALL_OPERATORS: frozenset[str] = COMPARISON_OPERATORS | CROSS_OPERATORS

POSITION_TYPES: frozenset[str] = frozenset({"long", "short"})

# 'notional' is the recommended mode: a rupee amount per trade, so cost drag is
# identical on a Rs 200 stock and a Rs 4,000 one and results stay comparable
# across a universe. 'fixed_quantity' is retained for v1 strategies and is
# documented as discouraged — at a fixed share count, ranking a universe partly
# ranks it by share price.
SIZING_TYPES: frozenset[str] = frozenset({"notional", "fixed_quantity"})

SIZING_TYPE_KEYS: dict[str, frozenset[str]] = {
    "notional": frozenset({"notional_per_trade"}),
    "fixed_quantity": frozenset({"quantity"}),
}

# "EXCHANGE:TRADINGSYMBOL", e.g. NSE:RELIANCE or NSE:M&M or NFO:NIFTY24AUGFUT.
INSTRUMENT_RE = re.compile(r"^[A-Z]+:[A-Z0-9&\-]+$")

# A universe name: capitals, digits and underscores. Matches how NSE index
# names are written (NIFTY50, NIFTY_MIDCAP_100) and keeps custom group names
# free of the spaces and punctuation that make them awkward to reference.
UNIVERSE_RE = re.compile(r"^[A-Z0-9_]{2,40}$")

# Stop/target specifications. 'percent' is a flat move from entry; 'atr' scales
# with the symbol's own volatility, which matters across a universe where one
# fixed percentage is too tight for volatile names and too loose for calm ones.
STOP_TYPES: frozenset[str] = frozenset({"percent", "atr"})

# Required keys per stop type. Keys outside these are rejected, so mixing the
# two forms (e.g. {type: atr, value: 1.5}) fails loudly instead of silently
# ignoring the key that does not apply.
STOP_TYPE_KEYS: dict[str, frozenset[str]] = {
    "percent": frozenset({"value"}),
    "atr": frozenset({"period", "multiplier"}),
}

# An intraday stop wider than this is almost certainly a typo (70 for 0.7).
MAX_STOP_PERCENT = 50.0

# The same typo class on the ATR side: `multiplier: 150` almost certainly meant
# 1.5. Deliberately NO ceiling on the ATR `period` — every other indicator
# period in this parser is validated only as > 0, and bounding this one alone
# would be inconsistent.
MAX_ATR_MULTIPLIER = 20.0

# NSE cash session, IST. Session keys are validated against these bounds: a
# square_off of 17:00 would silently never trigger, leaving an "intraday"
# strategy holding overnight — exactly the failure the key exists to prevent.
SESSION_OPEN_HHMM = "09:15"
SESSION_CLOSE_HHMM = "15:30"
SESSION_KEYS: frozenset[str] = frozenset(
    {"no_entry_before", "no_entry_after", "square_off"}
)
