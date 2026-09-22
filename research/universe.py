"""What a research run tests: which symbols, on which timeframes.

Indexes are tested on 60m and daily only because Yahoo keeps only about 60
days of 5-, 15- and 30-minute index history; only its 60-minute series (a
rolling 730 days) and daily series reach back far enough to test. A strategy
whose rules read volume is not tested on indexes at all: Yahoo's index
"volume" is not real exchange volume, and a result built on it would look
like evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

# Every stock timeframe the store can produce, lowest first. This is the
# WHITELIST - what a caller is allowed to ask for - not what a research day
# tests. `research.atlas` still measures baselines across all six, because the
# atlas is the evidence that justifies narrowing the daily sweep.
STOCK_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "25m", "30m", "60m", "day")

# What one research DAY tests. Sub-hour bars are gone deliberately, not for
# speed: the atlas (16 Sep 2026) measured every sub-hour reading of every
# baseline between -0.2% and -38% a month, because a round trip pays about
# 0.08% in fees plus 0.10% in slippage and these rules churn. Five days of
# proposals since then have each spent one of their five lessons re-deriving
# that ("sub-hourly should be dropped outright", 20 Sep), which is a lesson
# slot and a fifth of the day's AI allowance spent on a settled question.
#
# 60m stays. It is the only intraday resolution left, and three of the
# directions in research.directions - a same-day short, gap behaviour at the
# open, time of day - need SOME intraday bar to exist at all.
#
# Not a permanent verdict: `--stock-timeframes 5m,15m,25m,30m,60m,day`
# re-opens the full grid for a one-off run, and research.evaluate and
# research.atlas still default to the whole whitelist.
SWEEP_TIMEFRAMES: tuple[str, ...] = ("60m", "day")

INDEX_TIMEFRAMES: tuple[str, ...] = ("60m", "day")

# Store symbol -> Yahoo symbol. Store symbols are rows already in the
# `instruments` table, so each index gets a real instrument_id and an ordinary
# parquet folder like any stock.
INDEXES: dict[str, str] = {
    "NSE:NIFTY": "^NSEI",
    "NSE:BANKNIFTY": "^NSEBANK",
    "NSE:NIFTYIT": "^CNXIT",
    "NSE:NIFTYAUTO": "^CNXAUTO",
    "NSE:NIFTYPHARMA": "^CNXPHARMA",
    "NSE:NIFTYFMCG": "^CNXFMCG",
    "NSE:NIFTYMETAL": "^CNXMETAL",
    "NSE:FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NSE:NIFTYMID100FREE": "NIFTY_MIDCAP_100.NS",
}

# Indexes whose Yahoo DAILY series is missing 17-31 July 2026 - the last two
# weeks of the locked year. Measured 2026-09-12: 234 daily bars inside the
# locked year against NIFTY's 245, while their 60-minute series are complete.
# A daily result here would be scored over a year missing its own ending.
INDEX_DAILY_UNRELIABLE: frozenset[str] = frozenset({
    "NSE:NIFTYAUTO",
    "NSE:NIFTYFMCG",
    "NSE:NIFTYMETAL",
    "NSE:FINNIFTY",
    "NSE:NIFTYMID100FREE",
})

# Everything that reads volume, by name, in either strategy format. Kept as a
# public constant (rather than embedded in the regex) so a test can check it
# against strategy.vocabulary and catch a new volume-based indicator that
# would otherwise slip past the index skip unnoticed.
VOLUME_INPUTS: frozenset[str] = frozenset({"volume", "vwap", "obv", "mfi", "cmf", "vwma"})
_VOLUME_WORDS = re.compile(r"\b(" + "|".join(sorted(VOLUME_INPUTS)) + r")\b", re.IGNORECASE)

# Only the parts of a document that are rules. A strategy NAMED "VOLUME-IDEA"
# must not lose its index tests.
_RULE_KEYS = ("entry", "exit", "states")


@dataclass(frozen=True)
class Combo:
    symbol: str
    timeframe: str
    is_index: bool


def _mentions_volume(node: Any) -> bool:
    if isinstance(node, str):
        return bool(_VOLUME_WORDS.search(node))
    if isinstance(node, Mapping):
        return any(_mentions_volume(k) or _mentions_volume(v) for k, v in node.items())
    if isinstance(node, Sequence):
        return any(_mentions_volume(item) for item in node)
    return False


def document_uses_volume(doc: Mapping[str, Any]) -> bool:
    """True when any rule in a v2 or v3 strategy document reads volume."""
    return any(_mentions_volume(doc.get(key)) for key in _RULE_KEYS)


def research_combos(
    stocks: Iterable[str],
    *,
    include_indexes: bool,
    stock_timeframes: Sequence[str] = STOCK_TIMEFRAMES,
) -> list[Combo]:
    """Every stock x timeframe, then every index x timeframe. Order is stable."""
    if isinstance(stock_timeframes, str):
        raise TypeError(
            f"stock_timeframes must be a sequence of timeframes, not a bare "
            f"string {stock_timeframes!r} — pass a tuple such as (\"day\",)"
        )
    unknown = [tf for tf in stock_timeframes if tf not in STOCK_TIMEFRAMES]
    if unknown:
        raise ValueError(
            f"unknown stock timeframe(s) {unknown!r}; allowed: {STOCK_TIMEFRAMES!r}"
        )
    combos = [Combo(s, tf, False) for s in stocks for tf in stock_timeframes]
    if include_indexes:
        combos += [
            Combo(s, tf, True)
            for s in INDEXES
            for tf in INDEX_TIMEFRAMES
            if tf != "day" or s not in INDEX_DAILY_UNRELIABLE
        ]
    return combos


def lowest_timeframe(timeframes: Sequence[str] = SWEEP_TIMEFRAMES) -> str:
    """The shortest bar in `timeframes`, by the whitelist's own order.

    research.checker validates a proposal against one placeholder timeframe,
    and an operand may only reference a HIGHER timeframe than the strategy's
    own. Validating against a bar SHORTER than anything the sweep runs would
    let a rule reading, say, 15-minute closes pass the checker and then fail
    to simulate on every combination - a whole version spent on 1,213 skips.
    """
    for candidate in STOCK_TIMEFRAMES:
        if candidate in timeframes:
            return candidate
    raise ValueError(f"no known stock timeframe among {timeframes!r}")
