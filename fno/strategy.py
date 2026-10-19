"""The options strategy format, version options/1 (design §3): parse and check.

A strategy is a YAML document written by the AI or by hand. Parsing refuses,
with a reason that says what to change, anything the engine could not test
as written: an unknown fact, a strike rule with two answers, a delta on the
wrong side, a typo in a key (a misspelt `stop_pc` silently ignored would run
a strategy with no stop). Whether the DATA can test it - minute coverage,
weeklies on that date - is the checker's job (step 4).

Leg numbers in `from_leg` count from 0, like `expiry.which`.

Positional strategies decide on DAILY bars, so indicators are written bare
(`close > sma(200)`). The `daily.` / `prev_day.` prefixes exist for intraday
bars and are refused here rather than quietly meaning "yesterday".

Times are strings: YAML reads an unquoted 10:00 as the number 600.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import yaml

from fno.contracts import INDEX_UNDERLYINGS, MINUTE_UNDERLYINGS
from strategy.expr import Expr, ExpressionError, names_used, parse_expression

VERSION = "options/1"

# The day's market facts (fno.features) plus two the engine makes per
# strategy and per day (fno.market): days to the strategy's own expiry, and
# sessions until the next scheduled event (0 = today) - and three the intraday
# engine makes from the day so far (fno.intraday): the index's 09:15 open, and
# its 09:15-09:44 high and low, known from 09:45.
FACT_NAMES = frozenset({
    "vix", "vix_chg_pct", "atm_iv", "iv_rank", "iv_pctile", "pcr_oi", "pcr_vol",
    "max_pain", "straddle", "expected_move_pct", "skew_25d", "dte", "weekday",
    "event_today", "basis_pct", "fii_index_fut_net", "spot", "forward",
    "atm_strike", "max_oi_call_strike", "max_oi_put_strike",
    "expiry_dte", "sessions_to_event",
    "day_open", "or_high", "or_low",
})
CANDLE_NAMES = frozenset({"open", "high", "low", "close"})
STRIKE_RULES = ("atm", "otm_pct", "delta", "premium", "max_oi", "from_leg")
MAX_LEGS = 4

_TOP_KEYS = {"version", "name", "underlying", "mode", "expiry", "entry", "legs",
             "exit", "reentry", "sizing"}
_ENTRY_KEYS = {"window", "when"}
_EXIT_KEYS = {"target_pct", "stop_pct", "time", "days_before_expiry", "when"}
_HHMM = re.compile(r"[0-2]\d:[0-5]\d")


class StrategyError(ValueError):
    """A strategy document the engine cannot test as written."""


@dataclass(frozen=True)
class Leg:
    side: str          # buy | sell
    type: str          # CE | PE
    rule: str          # one of STRIKE_RULES
    value: Any         # n, pct, delta, rupees, True, or (leg index, offset points)
    lots: int

    @property
    def sign(self) -> int:
        """+1 for a bought leg, -1 for a sold one: the sign of its value in the account."""
        return 1 if self.side == "buy" else -1


@dataclass(frozen=True)
class Exit:
    target_pct: float | None
    stop_pct: float | None
    days_before_expiry: int
    time: str | None
    when: Expr | None


@dataclass(frozen=True)
class OptionsStrategy:
    name: str
    underlying: str
    mode: str
    cycle: str
    which: int
    window: tuple[str, str] | None
    entry_when: Expr | None
    legs: tuple[Leg, ...]
    exit: Exit
    max_per_day: int
    max_margin_pct: float


def _need(ok: bool, message: str) -> None:
    if not ok:
        raise StrategyError(message)


def _is_number(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _is_whole(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_hhmm(x: Any) -> bool:
    return isinstance(x, str) and _HHMM.fullmatch(x) is not None


def _known_keys(raw: dict, allowed: set[str], where: str) -> None:
    extra = sorted(map(str, set(raw) - allowed))
    _need(not extra, f"{where}: unknown key(s) {', '.join(extra)} "
                     f"(allowed: {', '.join(sorted(allowed))})")


def _expression(text: Any, where: str) -> Expr | None:
    if text is None:
        return None
    _need(isinstance(text, str) and text.strip() != "", f"{where} must be an expression in quotes")
    try:
        node = parse_expression(text)
    except ExpressionError as exc:
        raise StrategyError(f"{where}: {exc}") from None
    for path in sorted(names_used(node)):
        name = ".".join(path)
        _need(len(path) == 1 and path[0] in FACT_NAMES | CANDLE_NAMES,
              f"{where}: unknown name '{name}'. Facts: {', '.join(sorted(FACT_NAMES))}; "
              f"index candles: {', '.join(sorted(CANDLE_NAMES))}")
    return node


def _leg(raw: Any, i: int) -> Leg:
    where = f"legs[{i}]"
    _need(isinstance(raw, dict), f"{where} must be a mapping")
    _known_keys(raw, {"side", "type", "strike", "lots"}, where)
    side, typ, lots = raw.get("side"), raw.get("type"), raw.get("lots", 1)
    _need(side in ("buy", "sell"), f"{where}.side must be buy or sell")
    _need(typ in ("CE", "PE"), f"{where}.type must be CE or PE")
    _need(_is_whole(lots) and lots >= 1, f"{where}.lots must be a whole number, 1 or more")
    strike = raw.get("strike")
    _need(isinstance(strike, dict), f"{where}.strike must be one rule, e.g. {{delta: 0.2}}")
    if "from_leg" in strike:
        _need(set(strike) == {"from_leg", "offset"},
              f"{where}.strike: from_leg takes exactly from_leg and offset")
        j, offset = strike["from_leg"], strike["offset"]
        _need(_is_whole(j) and 0 <= j < i,
              f"{where}.strike.from_leg must name an EARLIER leg (counting from 0)")
        _need(_is_number(offset), f"{where}.strike.offset must be a number of index points")
        return Leg(side, typ, "from_leg", (j, float(offset)), lots)
    _need(len(strike) == 1 and next(iter(strike)) in STRIKE_RULES,
          f"{where}.strike must be exactly one of {', '.join(STRIKE_RULES)}")
    rule, value = next(iter(strike.items()))
    if rule == "atm":
        _need(_is_whole(value), f"{where}.strike.atm is a whole number of strikes from the money")
    elif rule == "max_oi":
        _need(value is True, f"{where}.strike.max_oi takes true")
    else:
        _need(_is_number(value), f"{where}.strike.{rule} must be a number")
        value = float(value)
        if rule == "delta":
            _need(0 < abs(value) < 1, f"{where}.strike.delta must be between -1 and 1, not 0")
            _need((value > 0) == (typ == "CE"),
                  f"{where}.strike.delta: a call's delta is positive, a put's delta is negative")
        if rule == "premium":
            _need(value > 0, f"{where}.strike.premium must be more than 0 rupees")
    return Leg(side, typ, rule, value, lots)


def parse(text: str) -> OptionsStrategy:
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise StrategyError(f"not valid YAML: {exc}") from None
    _need(isinstance(doc, dict), "a strategy is a YAML mapping")
    _known_keys(doc, _TOP_KEYS, "strategy")
    _need(doc.get("version") == VERSION, f"version must be {VERSION}")
    name = doc.get("name")
    _need(isinstance(name, str) and name.strip() != "", "name is required")
    underlying = doc.get("underlying")
    _need(underlying in INDEX_UNDERLYINGS, f"underlying must be one of {', '.join(INDEX_UNDERLYINGS)}")
    mode = doc.get("mode")
    _need(mode in ("intraday", "positional"), "mode must be intraday or positional")
    _need(mode == "positional" or underlying in MINUTE_UNDERLYINGS,
          f"{underlying} has daily data only: mode must be positional")

    expiry = doc.get("expiry") or {}
    _need(isinstance(expiry, dict), "expiry must be {cycle: weekly|monthly, which: 0}")
    _known_keys(expiry, {"cycle", "which"}, "expiry")
    cycle, which = expiry.get("cycle"), expiry.get("which", 0)
    _need(cycle in ("weekly", "monthly"), "expiry.cycle must be weekly or monthly")
    _need(_is_whole(which) and which >= 0, "expiry.which must be 0 (nearest), 1, 2, ...")

    entry = doc.get("entry") or {}
    _need(isinstance(entry, dict), "entry must be a mapping")
    _known_keys(entry, _ENTRY_KEYS, "entry")
    window = None
    if mode == "intraday":
        w = entry.get("window") or {}
        _need(isinstance(w, dict) and _is_hhmm(w.get("from")) and _is_hhmm(w.get("to"))
              and w["from"] < w["to"], 'entry.window needs from and to as "HH:MM" in quotes')
        window = (w["from"], w["to"])
    else:
        _need("window" not in entry,
              "entry.window is for intraday strategies; positional ones decide at the close")
    entry_when = _expression(entry.get("when"), "entry.when")

    raw_legs = doc.get("legs")
    _need(isinstance(raw_legs, list) and 1 <= len(raw_legs) <= MAX_LEGS, f"legs: 1 to {MAX_LEGS} legs")
    legs = tuple(_leg(raw, i) for i, raw in enumerate(raw_legs))

    ex = doc.get("exit") or {}
    _need(isinstance(ex, dict), "exit must be a mapping")
    _known_keys(ex, _EXIT_KEYS, "exit")
    target, stop = ex.get("target_pct"), ex.get("stop_pct")
    for key, value in (("target_pct", target), ("stop_pct", stop)):
        _need(value is None or (_is_number(value) and value > 0), f"exit.{key} must be a positive number")
    days_before = ex.get("days_before_expiry", 0)
    _need(_is_whole(days_before) and days_before >= 0,
          "exit.days_before_expiry must be 0 (hold to expiry) or more")
    exit_time = ex.get("time")
    if mode == "intraday":
        _need(_is_hhmm(exit_time), 'exit.time is required for intraday, as "HH:MM" in quotes')
    else:
        _need(exit_time is None, "exit.time is for intraday strategies")
    exit_ = Exit(None if target is None else float(target), None if stop is None else float(stop),
                 days_before, exit_time, _expression(ex.get("when"), "exit.when"))

    reentry = doc.get("reentry") or {}
    _need(isinstance(reentry, dict), "reentry must be a mapping")
    _known_keys(reentry, {"max_per_day"}, "reentry")
    max_per_day = reentry.get("max_per_day", 1)
    _need(_is_whole(max_per_day) and max_per_day >= 1, "reentry.max_per_day must be 1 or more")

    sizing = doc.get("sizing") or {}
    _need(isinstance(sizing, dict), "sizing must be a mapping")
    _known_keys(sizing, {"max_margin_pct"}, "sizing")
    max_margin = sizing.get("max_margin_pct", 100)
    _need(_is_number(max_margin) and 0 < max_margin <= 100,
          "sizing.max_margin_pct must be above 0 and at most 100")

    return OptionsStrategy(name.strip(), underlying, mode, cycle, which, window, entry_when,
                           legs, exit_, max_per_day, float(max_margin))
