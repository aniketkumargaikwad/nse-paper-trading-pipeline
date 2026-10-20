"""Apply the options research rules to whatever the AI proposes (design §3).

Like the stock checker (research.checker), the rules are applied, not
requested: the name and the index are the tool's job, so they are replaced
rather than argued about. A missing sizing takes the atlas convention. What
is REFUSED - with a reason the AI can act on - is a document the parser
refuses, and two things the minute data cannot test: an intraday strike
further out than the ±5% band Upstox minutes were fetched for, and an
intraday expiry beyond the next one (minutes cover only an expiry's last
10-20 sessions).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

import yaml

from fno import atlas, minute
from fno.strategy import OptionsStrategy, StrategyError, parse

DEFAULT_MARGIN_PCT = {"positional": atlas.MARGIN_PCT, "intraday": 100.0}
MAX_INTRADAY_WHICH = 1


class CheckError(ValueError):
    """A proposal that cannot be tested. The message is sent back to the AI."""


@dataclass(frozen=True)
class CheckedOptions:
    document: dict[str, Any]
    mode: str

    def for_index(self, underlying: str) -> OptionsStrategy:
        """The strategy as tested on `underlying` - the only thing that changes per index."""
        return parse(yaml.safe_dump({**self.document, "underlying": underlying}, sort_keys=False))


def research_name(day: date, idea: str, version: int) -> str:
    """O-YYYYMMDD-<idea>-vN: an options research version, and where it came from."""
    slug = re.sub(r"[^a-z0-9]+", "-", idea.lower()).strip("-")[:40] or "idea"
    return f"O-{day:%Y%m%d}-{slug}-v{version}"


def check_proposal(strategy_yaml: str, *, idea: str, day: date, version: int) -> CheckedOptions:
    try:
        doc = yaml.safe_load(strategy_yaml)
    except yaml.YAMLError as exc:
        raise CheckError(f"the strategy is not valid YAML: {exc}") from exc
    if not isinstance(doc, dict):
        raise CheckError("the strategy must be ONE YAML mapping (version, mode, expiry, entry, legs, exit)")
    doc = dict(doc)
    doc["name"] = research_name(day, idea, version)
    doc["underlying"] = atlas.UNDERLYINGS[0]
    mode = doc.get("mode")
    sizing = doc.get("sizing")
    if mode in DEFAULT_MARGIN_PCT and not (isinstance(sizing, dict) and "max_margin_pct" in sizing):
        doc["sizing"] = {**(sizing if isinstance(sizing, dict) else {}),
                         "max_margin_pct": DEFAULT_MARGIN_PCT[mode]}
    checked = CheckedOptions(doc, str(mode))
    try:
        strategies = [checked.for_index(u) for u in atlas.UNDERLYINGS]
    except StrategyError as exc:
        raise CheckError(str(exc)) from exc
    strategy = strategies[0]
    if strategy.mode == "intraday":
        band = minute.BAND_PCT
        for i, leg in enumerate(strategy.legs):
            if leg.rule == "otm_pct" and abs(leg.value) > band:
                raise CheckError(
                    f"legs[{i}].strike.otm_pct {leg.value:g}: intraday option minutes exist only for "
                    f"strikes within {band:g}% of the index, so a strike further out cannot be tested. "
                    f"Use otm_pct of {band:g} or less, or mode: positional.")
        if strategy.which > MAX_INTRADAY_WHICH:
            raise CheckError(
                f"expiry.which {strategy.which}: intraday option minutes cover only each expiry's last "
                f"10 (weekly) or 20 (monthly) sessions, so use which: 0 or 1.")
    return checked
