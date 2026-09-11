"""What a research run tests: which symbols, on which timeframes.

Indexes are tested on 60m and daily only, because free intraday index history
does not reach further back than Yahoo's 730-day 60m window. A strategy whose
rules read volume is not tested on indexes at all: Yahoo's index "volume" is
not real exchange volume, and a result built on it would look like evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

STOCK_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "25m", "30m", "60m", "day")
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

# Everything that reads volume, by name, in either strategy format.
_VOLUME_WORDS = re.compile(r"\b(volume|vwap|obv|mfi|cmf|vwma)\b", re.IGNORECASE)

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
    combos = [Combo(s, tf, False) for s in stocks for tf in stock_timeframes]
    if include_indexes:
        combos += [Combo(s, tf, True) for s in INDEXES for tf in INDEX_TIMEFRAMES]
    return combos
