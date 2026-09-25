"""Apply the research rules to whatever Opus proposes (design 5.6).

The rules are applied, not requested: a proposal that names its own symbols or
sizing is corrected rather than rejected, because those are the sweep's job and
arguing about them in the prompt wastes a version. What IS rejected is a
strategy that cannot be parsed, or a short strategy with no square-off - an
overnight short is a different risk from the one being researched.

A sweep of DAILY bars only (research.universe.SWEEP_TIMEFRAMES, since 25 Sep
2026) rejects three more things, each of which would otherwise run without an
error and quietly mean something else. Daily candles are stamped at midnight
IST, so:

* a short is never squared off - `00:00 >= 15:10` is never true - and would be
  carried for days, which a cash-equity short cannot be;
* a session time does nothing, or everything: `no_entry_before: "09:30"`
  blocks every entry, `square_off` never fires;
* an `hourly.` reference builds each "hour" from one daily bar and reads the
  daily close back under another name.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from config import TIMEFRAME_MINUTES
from research.universe import STOCK_TIMEFRAMES, lowest_timeframe
from strategy.v3 import is_v3_document, parse_machine
from strategy.vocabulary import HIGHER_TIMEFRAME_PREFIXES
from strategy_schema import parse_strategy_dict

# The sweep supplies the real symbols and timeframe per combination. The
# placeholder timeframe is the LOWEST bar the sweep runs, so a rule referencing
# any higher timeframe is still legal and one referencing a lower one is not.
PLACEHOLDER_TIMEFRAME = "5m"        # the lowest stock timeframe; the default
PLACEHOLDER_INSTRUMENTS = ["NSE:RELIANCE"]
NOTIONAL_PER_TRADE = 100000

_SESSION_TIMES = ("no_entry_before", "no_entry_after", "square_off")
_RULE_KEYS = ("entry", "exit", "states")


class CheckError(ValueError):
    """A proposal that cannot be used. The message is sent back to Opus."""


@dataclass(frozen=True)
class CheckedProposal:
    document: dict[str, Any]
    strategy: Any


def research_name(day: date, idea: str, version: int) -> str:
    """R-YYYYMMDD-<idea>-vN, so a library row says where it came from."""
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:40] or "idea"
    return f"R-{day:%Y%m%d}-{slug}-v{version}"


def _strings(node: Any) -> list[str]:
    """Every string anywhere in `node`, keys included."""
    if isinstance(node, str):
        return [node]
    if isinstance(node, Mapping):
        return [s for k, v in node.items() for s in (*_strings(k), *_strings(v))]
    if isinstance(node, Sequence):
        return [s for item in node for s in _strings(item)]
    return []


def _refuse_on_daily_bars(doc: Mapping[str, Any], strategy: Any) -> None:
    """The rules a daily-only sweep cannot honour. See the module docstring."""
    if getattr(strategy, "position_type", "long") != "long":
        raise CheckError(
            "this research day tests DAILY bars only, so the strategy must be LONG only. "
            "A short in NSE cash equities must be closed the same day, and a daily bar "
            "has no time of day to close it at - it would be carried overnight."
        )
    session = doc.get("session") or {}
    set_times = [k for k in _SESSION_TIMES if isinstance(session, Mapping) and session.get(k)]
    if set_times:
        raise CheckError(
            f"remove session.{', session.'.join(set_times)}: this research day tests DAILY "
            "bars only, and a daily bar has no time of day. Every daily candle is stamped "
            "at midnight, so a session time either blocks every entry or never fires."
        )
    lower = sorted(
        prefix for prefix, tf in HIGHER_TIMEFRAME_PREFIXES.items()
        if TIMEFRAME_MINUTES[tf] < TIMEFRAME_MINUTES["day"]
    )
    if lower:
        pattern = re.compile(r"\b(" + "|".join(map(re.escape, lower)) + r")\.")
        used = sorted({m.group(1) for key in _RULE_KEYS
                       for text in _strings(doc.get(key)) for m in pattern.finditer(text)})
        if used:
            raise CheckError(
                f"remove every {', '.join(p + '.' for p in used)} reference: this research "
                "day tests DAILY bars only, and a rule may read its own bar or a HIGHER "
                "timeframe, never a lower one. Use daily./prev_day. or the bar itself."
            )


def check_proposal(
    strategy_yaml: str, *, idea: str, day: date, version: int,
    timeframes: Sequence[str] = STOCK_TIMEFRAMES,
) -> CheckedProposal:
    """Validate a proposal and force the research rules onto it.

    `timeframes` is what the sweep will run it on. The proposal is parsed at the
    lowest of them, and a daily-only sweep adds the refusals in the module
    docstring.
    """
    try:
        doc = yaml.safe_load(strategy_yaml)
    except yaml.YAMLError as exc:
        raise CheckError(f"the strategy is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CheckError("the strategy must be a mapping of key: value lines")

    placeholder = lowest_timeframe(timeframes)
    doc = dict(doc)
    doc["name"] = research_name(day, idea, version)
    doc["enabled"] = False
    doc["sizing"] = {"type": "notional", "notional_per_trade": NOTIONAL_PER_TRADE}
    doc["timeframe"] = placeholder
    doc.pop("universe", None)
    doc["instruments"] = list(PLACEHOLDER_INSTRUMENTS)

    if placeholder != "day" and doc.get("position_type") == "short":
        session = doc.get("session") or {}
        if not session.get("square_off"):
            raise CheckError(
                'a short strategy needs session.square_off, e.g. "15:10": an '
                "overnight short is a different risk from the one being researched."
            )

    try:
        strategy = (
            parse_machine(doc) if is_v3_document(doc)
            else parse_strategy_dict(doc, where="strategy")
        )
    except ValueError as exc:
        raise CheckError(str(exc)) from exc

    if placeholder == "day":
        _refuse_on_daily_bars(doc, strategy)

    return CheckedProposal(document=doc, strategy=strategy)
