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


# The intraday half (design 2026-10-19 §6): the classic same-day option trades.
#
# SIZED BY WHAT ONE STOP COSTS, not a quarter of the balance. The quarter rule
# is for positions held overnight, whose gaps no stop can catch; and at Rs 5
# lakh it cannot buy one NIFTY straddle set on expiry day (about Rs 2.5 lakh of
# margin), so it would measure nothing. Each strategy below is sized so a
# stopped-out trade costs roughly 1-3% of the account: sold straddles and
# strangles up to all the margin (one or two sets; a 30-50% stop is Rs 5-10k a
# set), the iron fly and the bought options at a tenth (their margin is small,
# so full margin would be 14 iron flies, or the whole account in premium).
# Fixed before any intraday result was seen.
EXIT_TIME = "15:15"
INTRADAY: dict[str, dict[str, Any]] = {
    "straddle-0920": {"window": ("09:20", "09:21"),
                      "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0})],
                      "exit": {"stop_pct": 30, "target_pct": 50}, "margin_pct": 100.0},
    "strangle-0930": {"window": ("09:30", "09:31"),
                      "legs": [_leg("sell", "CE", {"delta": 0.20}), _leg("sell", "PE", {"delta": -0.20})],
                      "exit": {"stop_pct": 50, "target_pct": 50}, "margin_pct": 100.0},
    "expiry-day-straddle": {"window": ("09:20", "09:21"), "when": "expiry_dte == 0",
                            "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0})],
                            "exit": {"stop_pct": 30, "target_pct": 50}, "margin_pct": 100.0},
    "iron-fly": {"window": ("09:20", "09:21"),
                 "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0}),
                          _leg("buy", "CE", {"otm_pct": 2.0}), _leg("buy", "PE", {"otm_pct": 2.0})],
                 "exit": {"stop_pct": 50, "target_pct": 40}, "margin_pct": 10.0},
    "orb-call": {"window": ("09:45", "13:00"), "when": "close > or_high",
                 "legs": [_leg("buy", "CE", {"atm": 0})], "exit": {"stop_pct": 30, "target_pct": 60},
                 "margin_pct": 10.0},
    "orb-put": {"window": ("09:45", "13:00"), "when": "close < or_low",
                "legs": [_leg("buy", "PE", {"atm": 0})], "exit": {"stop_pct": 30, "target_pct": 60},
                "margin_pct": 10.0},
}


def intraday_document(name: str, underlying: str) -> str:
    spec = INTRADAY[name]
    entry: dict[str, Any] = {"window": {"from": spec["window"][0], "to": spec["window"][1]}}
    if "when" in spec:
        entry["when"] = spec["when"]
    doc = {"version": VERSION, "name": f"atlas-intraday-{name}-{underlying.lower()}",
           "underlying": underlying, "mode": "intraday", "expiry": {"cycle": "weekly", "which": 0},
           "entry": entry, "legs": [dict(leg) for leg in spec["legs"]],
           "exit": {**spec["exit"], "time": EXIT_TIME}, "sizing": {"max_margin_pct": spec["margin_pct"]}}
    return yaml.safe_dump(doc, sort_keys=False)


def intraday_documents() -> list[str]:
    return [intraday_document(name, u) for u in UNDERLYINGS for name in INTRADAY]
