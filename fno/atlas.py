"""The options atlas: classic positional strategies, measured before the AI runs (design §7).

Each is the textbook version - no filters - so the AI starts from what the
plain building blocks earned on this data. "Weekly" lands on the nearest
listed expiry, which is the monthly before NIFTY's weeklies began (Feb 2019)
and after BANKNIFTY's ended (Nov 2024).

SIZED AT A QUARTER of the balance in margin (MARGIN_PCT). At 100% every
short-option strategy ruins itself - the BANKNIFTY straddle ran at about 12x
leverage and lost 64% in the first two weeks of 2016 - so the figures would
measure leverage, not the strategy. A quarter is a measuring convention, not
advice: a strategy the AI writes sets its own sizing to meet the 10% worst-day
rule, and returns scale roughly with it until a bad day compounds.

Measured on the TRAINING years only (fno.market.training_window): the exam
year is cut out at load and never read.
"""

from __future__ import annotations

from typing import Any

import yaml

from fno.strategy import VERSION

UNDERLYINGS = ("NIFTY", "BANKNIFTY")
HOLD = {"target_pct": 50, "stop_pct": 100}
MARGIN_PCT = 25.0


def _leg(side: str, typ: str, strike: dict[str, Any]) -> dict[str, Any]:
    return {"side": side, "type": typ, "strike": strike, "lots": 1}


ATLAS: dict[str, dict[str, Any]] = {
    "short-straddle": {"legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0})]},
    "short-strangle-15d": {"legs": [_leg("sell", "CE", {"delta": 0.15}), _leg("sell", "PE", {"delta": -0.15})]},
    "short-strangle-20d": {"legs": [_leg("sell", "CE", {"delta": 0.20}), _leg("sell", "PE", {"delta": -0.20})]},
    "short-strangle-30d": {"legs": [_leg("sell", "CE", {"delta": 0.30}), _leg("sell", "PE", {"delta": -0.30})]},
    "iron-condor": {"legs": [_leg("sell", "CE", {"delta": 0.20}), _leg("sell", "PE", {"delta": -0.20}),
                             _leg("buy", "CE", {"delta": 0.05}), _leg("buy", "PE", {"delta": -0.05})]},
    "bull-put-spread": {"legs": [_leg("sell", "PE", {"delta": -0.30}), _leg("buy", "PE", {"delta": -0.10})]},
    "bear-call-spread": {"legs": [_leg("sell", "CE", {"delta": 0.30}), _leg("buy", "CE", {"delta": 0.10})]},
    # Bought at the close two sessions before a Budget / RBI / election-result
    # day, so the fill (next open) is the session before it; sold at the close
    # of the event day, filled the next open. The weekly after next, so the
    # options are still alive through the event.
    "long-straddle-before-events": {
        "legs": [_leg("buy", "CE", {"atm": 0}), _leg("buy", "PE", {"atm": 0})],
        "which": 1, "entry": {"when": "sessions_to_event == 2"}, "exit": {"when": "event_today"}},
}


def document(name: str, underlying: str) -> str:
    spec = ATLAS[name]
    doc: dict[str, Any] = {"version": VERSION, "name": f"atlas-{name}-{underlying.lower()}",
                           "underlying": underlying, "mode": "positional",
                           "expiry": {"cycle": "weekly", "which": spec.get("which", 0)}}
    if "entry" in spec:
        doc["entry"] = dict(spec["entry"])
    doc["legs"] = [dict(leg) for leg in spec["legs"]]
    doc["exit"] = dict(spec.get("exit", HOLD))
    doc["sizing"] = {"max_margin_pct": MARGIN_PCT}
    return yaml.safe_dump(doc, sort_keys=False)


def documents() -> list[str]:
    return [document(name, u) for u in UNDERLYINGS for name in ATLAS]
