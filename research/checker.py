"""Apply the research rules to whatever Opus proposes (design 5.6).

The rules are applied, not requested: a proposal that names its own symbols or
sizing is corrected rather than rejected, because those are the sweep's job and
arguing about them in the prompt wastes a version. What IS rejected is a
strategy that cannot be parsed, or a short strategy with no square-off - an
overnight short is a different risk from the one being researched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from strategy.v3 import is_v3_document, parse_machine
from strategy_schema import parse_strategy_dict

# The sweep supplies the real symbols and timeframe per combination. These
# placeholders only have to parse: 5m is the lowest stock timeframe, so a rule
# referencing any higher timeframe is still legal.
PLACEHOLDER_TIMEFRAME = "5m"
PLACEHOLDER_INSTRUMENTS = ["NSE:RELIANCE"]
NOTIONAL_PER_TRADE = 100000


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


def check_proposal(
    strategy_yaml: str, *, idea: str, day: date, version: int
) -> CheckedProposal:
    """Validate a proposal and force the research rules onto it."""
    try:
        doc = yaml.safe_load(strategy_yaml)
    except yaml.YAMLError as exc:
        raise CheckError(f"the strategy is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CheckError("the strategy must be a mapping of key: value lines")

    doc = dict(doc)
    doc["name"] = research_name(day, idea, version)
    doc["enabled"] = False
    doc["sizing"] = {"type": "notional", "notional_per_trade": NOTIONAL_PER_TRADE}
    doc["timeframe"] = PLACEHOLDER_TIMEFRAME
    doc.pop("universe", None)
    doc["instruments"] = list(PLACEHOLDER_INSTRUMENTS)

    if doc.get("position_type") == "short":
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

    return CheckedProposal(document=doc, strategy=strategy)
