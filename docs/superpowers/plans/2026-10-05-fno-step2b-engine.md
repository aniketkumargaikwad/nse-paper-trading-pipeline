# F&O Step 2b — The Options Backtest Engine on Daily Data, and the Atlas

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest positional NIFTY / BANKNIFTY option strategies on NSE's daily files since 2016 — strategy format, strike choice, fills with spreads and dated charges, margin, a ₹5 lakh account, expiry settlement, a report split by market condition — and measure the options atlas on the training years.

**Architecture:** Small pure modules over the step-1 store and the step-2a facts. `strategy` parses the YAML format; `strikes` picks the expiry and strikes from one day's chain; `fills` adds the half-spread; `margin` is a SPAN-style estimate; `account` sizes in whole sets and turns a balance into months and days; `market` hands the engine one day at a time and cuts the exam year out at load; `engine` runs the day loop; `report` summarises; `atlas` holds the classic strategies; two scripts run them.

**Tech Stack:** Python 3.11, numpy, pandas, PyYAML, pytest. Reuses `strategy.expr` / `expr_eval` (the expression language) and `research.portfolio.summarise` (the month-by-month reading every stock result is judged by).

**Spec:** `docs/superpowers/specs/2026-10-02-fno-options-design.md` §3, §4, §5, §7 (atlas), §12 step 2, §13.

**Conventions (from steps 1 and 2a):** run with `.venv/Scripts/python.exe -m pytest ...`; LF line endings (edit with the editor or `write_bytes`); test modules import shared helpers from `tests/` by module name (`from fno_synthetic import ...`).

**Decisions made while planning (each is stated in the code too):**
- **Daily timing.** Decide at the close, fill at the next session's open, exits checked on closing marks (the day's settlement prices). Every report says stops are seen on the close only.
- **One position at a time per strategy.** An entry is decided only when flat with nothing pending, so a new position starts at least one session after the last one closed (or the session after expiry).
- **No phantom fills.** An entry leg whose contract did not trade in the fill session (volume 0 or no open) skips the whole entry, with the reason. An exit cannot be skipped: an untraded leg closes at its settlement price with the thin-market spread, and the trade says so.
- **Expiry settlement.** On its expiry day NSE's old file shows an option's settlement price as **0** (checked: NIFTY 28 Mar 2019). The final settlement price is therefore the expiring future's settlement (11,570.00 that day, the index close), else the index close from Yahoo (weekly expiries have no future), else NSE's `underlying_price`. In-the-money options pay intrinsic value; bought ones pay STT on exercise; sold ones pay nothing extra.
- **Margin** (uncalibrated, says so): bought-only → premium paid; every sold option covered by a bought one of the same type → max(scan loss, maximum loss at expiry); otherwise scan loss + 2% exposure on the sold notional. Scan = ±max(6%, 0.5 × VIX)% of the forward and ±4 vol points, 7 price points × 2 vol shifts, Black-76 repricing.
- **Sizing.** Whole "sets" (one set = every leg at its `lots`), as many as `max_margin_pct` (default 100) of the balance covers. A new optional key `sizing: {max_margin_pct: …}` lets a strategy keep margin in reserve — the worst-day rule may need it.
- **Two facts made by the engine**, added to the format's names: `expiry_dte` (calendar days to the strategy's OWN expiry — the facts table's `dte` is always the nearest weekly) and `sessions_to_event` (sessions until the next Budget / RBI / election-result day, 0 on the day, NaN beyond 10). Both use dates known in advance.
- **Indicators in positional strategies are written bare** (`close > sma(200)`): the bars are daily, so the `daily.` / `prev_day.` prefixes (built for intraday) are refused by the parser.
- **Monthly** = an expiry that has a future that day; **weekly** = every option expiry. `which: 0` = nearest with at least a day to run.
- **Leg numbers in `from_leg` count from 0**, like `expiry.which`.
- **Exam year** = the last 365 days before the newest stored session; `MarketData.load(..., until=)` drops everything after the training end, so neither the atlas nor a default backtest can read it.
- **Trend at entry** (for the report's split) = the index's 50-day average against itself 20 sessions earlier: > +2% rising, < −2% falling, else sideways.

---

## File structure

| File | Responsibility |
|---|---|
| `fno/strategy.py` | The `options/1` format: `parse()` → `OptionsStrategy`, refusals as `StrategyError` |
| `fno/fills.py` | Half-spread on a fill |
| `fno/strikes.py` | `pick_expiry`, `pick_strikes` on one day's chain |
| `fno/margin.py` | `MarginLeg`, `scan_pct`, `max_loss`, `estimate` |
| `fno/account.py` | `CAPITAL`, `sets_affordable`, month / day / holding returns |
| `fno/market.py` | `MarketData` (one day at a time, `until`), `training_window`, `stored_sessions` |
| `fno/engine.py` | `run()` → `BacktestResult` with `Trade`s, `Skip`s, daily equity |
| `fno/report.py` | `build()` → `Report`; `to_text()` |
| `fno/atlas.py` | The classic strategies as documents |
| `scripts/fno_backtest.py` | Backtest one YAML file, print the report |
| `scripts/fno_atlas.py` | Measure the atlas, write `data/fno/atlas/results.json`, print a table |
| `tests/fno_synthetic.py` | A small Black-76 market for the tests (not a test module) |
| `tests/test_fno_strategy.py`, `test_fno_fills.py`, `test_fno_strikes.py`, `test_fno_margin.py`, `test_fno_account.py`, `test_fno_market.py`, `test_fno_engine.py`, `test_fno_report.py`, `test_fno_atlas.py`, `test_fno_golden.py` | One per module, plus the frozen real-data backtest |

---

### Task 1: The strategy format

**Files:** Create `fno/strategy.py`, `tests/test_fno_strategy.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_strategy.py`:

```python
"""The options strategy format (design §3): what parses, and what is refused with a reason."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.strategy import StrategyError, parse  # noqa: E402

# The design's own example, verbatim apart from the comments.
SPEC_EXAMPLE = """
version: options/1
name: weekly-20-delta-strangle
underlying: NIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry:
  window: {from: "09:30", to: "10:00"}
  when: "vix > 11 and vix < 16 and not event_today and dte <= 4"
legs:
  - {side: sell, type: CE, strike: {delta: 0.20}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.20}, lots: 1}
exit:
  target_pct: 50
  stop_pct: 100
  time: "15:15"
  days_before_expiry: 0
  when: "vix_chg_pct > 15"
reentry: {max_per_day: 1}
"""

POSITIONAL = """
version: options/1
name: monthly-condor
underlying: BANKNIFTY
mode: positional
expiry: {cycle: monthly, which: 0}
entry:
  when: "expiry_dte >= 25 and iv_rank > 50"
legs:
  - {side: sell, type: CE, strike: {delta: 0.2}, lots: 1}
  - {side: sell, type: PE, strike: {delta: -0.2}, lots: 1}
  - {side: buy, type: CE, strike: {from_leg: 0, offset: 500}, lots: 1}
  - {side: buy, type: PE, strike: {from_leg: 1, offset: -500}, lots: 1}
exit: {target_pct: 50, stop_pct: 200, days_before_expiry: 2}
sizing: {max_margin_pct: 60}
"""


def test_the_design_example_parses():
    s = parse(SPEC_EXAMPLE)
    assert (s.name, s.underlying, s.mode, s.cycle, s.which) == (
        "weekly-20-delta-strangle", "NIFTY", "intraday", "weekly", 0)
    assert s.window == ("09:30", "10:00")
    assert (s.legs[0].rule, s.legs[0].value, s.legs[0].sign) == ("delta", 0.2, -1)
    assert s.legs[1].value == -0.2
    assert (s.exit.target_pct, s.exit.stop_pct, s.exit.time) == (50.0, 100.0, "15:15")
    assert s.exit.when is not None and s.entry_when is not None
    assert (s.max_per_day, s.max_margin_pct) == (1, 100.0)


def test_a_positional_condor_parses():
    s = parse(POSITIONAL)
    assert s.mode == "positional" and s.window is None and s.cycle == "monthly"
    assert (s.legs[2].rule, s.legs[2].value) == ("from_leg", (0, 500.0))
    assert (s.legs[3].rule, s.legs[3].value, s.legs[3].sign) == ("from_leg", (1, -500.0), 1)
    assert s.exit.days_before_expiry == 2
    assert s.max_margin_pct == 60.0


@pytest.mark.parametrize("old,new,match", [
    ("vix_chg_pct > 15", "vix_change > 15", "unknown name 'vix_change'"),
    ("{delta: -0.20}", "{delta: 0.20}", "a put's delta is negative"),
    ("version: options/1", "version: options/2", "version must be options/1"),
    ("{delta: 0.20}, lots: 1}", "{delta: 0.20, atm: 0}, lots: 1}", "exactly one of"),
    ("underlying: NIFTY", "underlying: FINNIFTY", "daily data only"),
    ('time: "15:15"', "time: 15:15", "HH:MM"),
    ("{delta: 0.20}, lots: 1}", "{delta: 0.20}, lots: 0}", "lots must be"),
    ("stop_pct: 100", "stop_pc: 100", "unknown key"),
    ("mode: intraday", "mode: scalping", "mode must be"),
])
def test_refusals_say_what_to_change(old, new, match):
    text = SPEC_EXAMPLE.replace(old, new, 1)
    assert text != SPEC_EXAMPLE
    with pytest.raises(StrategyError, match=match):
        parse(text)


def test_from_leg_must_point_to_an_earlier_leg():
    with pytest.raises(StrategyError, match="EARLIER leg"):
        parse(POSITIONAL.replace("{from_leg: 0, offset: 500}", "{from_leg: 2, offset: 500}"))


def test_a_window_is_refused_on_a_positional_strategy():
    text = POSITIONAL.replace("entry:\n", 'entry:\n  window: {from: "09:30", to: "10:00"}\n')
    with pytest.raises(StrategyError, match="window is for intraday"):
        parse(text)


def test_dotted_names_are_refused_on_daily_bars():
    with pytest.raises(StrategyError, match="unknown name 'prev_day.close'"):
        parse(POSITIONAL.replace("iv_rank > 50", "prev_day.close > 1"))


def test_indicators_on_the_index_are_allowed():
    s = parse(POSITIONAL.replace("iv_rank > 50", "close > sma(200) and rsi(14) < 70"))
    assert s.entry_when is not None


def test_not_yaml_is_refused():
    with pytest.raises(StrategyError, match="YAML"):
        parse("legs: [unclosed")
```

- [ ] **Step 2: Run to see them fail** — `.venv/Scripts/python.exe -m pytest tests/test_fno_strategy.py -q` → `ModuleNotFoundError: No module named 'fno.strategy'`

- [ ] **Step 3: Write `fno/strategy.py`**

```python
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
# sessions until the next scheduled event (0 = today).
FACT_NAMES = frozenset({
    "vix", "vix_chg_pct", "atm_iv", "iv_rank", "iv_pctile", "pcr_oi", "pcr_vol",
    "max_pain", "straddle", "expected_move_pct", "skew_25d", "dte", "weekday",
    "event_today", "basis_pct", "fii_index_fut_net", "spot", "forward",
    "atm_strike", "max_oi_call_strike", "max_oi_put_strike",
    "expiry_dte", "sessions_to_event",
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
    extra = sorted(set(raw) - allowed)
    _need(not extra, f"{where}: unknown key(s) {', '.join(map(str, extra))} "
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
        _need(_is_whole(j) and 0 <= j < i, f"{where}.strike.from_leg must name an EARLIER leg (counting from 0)")
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
```

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_strategy.py -q` → all pass.

- [ ] **Step 5: Commit** — `git add fno/strategy.py tests/test_fno_strategy.py && git commit -m "feat(fno): the options strategy format, parsed and checked with reasons"`

---

### Task 2: Fills pay half the spread

**Files:** Create `fno/fills.py`, `tests/test_fno_fills.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_fills.py`:

```python
"""Half the bid-ask spread on every fill (design §4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.fills import fill_price, half_spread  # noqa: E402


def test_half_a_percent_of_the_premium_or_one_tick():
    assert half_spread(100.0, 1000) == pytest.approx(0.5)
    assert half_spread(4.0, 1000) == pytest.approx(0.05)      # 0.5% would be 2 paise: one tick


def test_doubled_in_a_thin_market():
    assert half_spread(100.0, 99) == pytest.approx(1.0)
    assert half_spread(100.0, 100) == pytest.approx(0.5)


def test_buying_pays_up_selling_receives_less_never_below_zero():
    assert fill_price(100.0, "buy", 1000) == pytest.approx(100.5)
    assert fill_price(100.0, "sell", 1000) == pytest.approx(99.5)
    assert fill_price(0.05, "sell", 0) == 0.0
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.fills'`

- [ ] **Step 3: Write `fno/fills.py`**

```python
"""What a fill costs beyond the quoted price: half the bid-ask spread (design §4).

Starting values, kept here in one place: the larger of one tick and 0.5% of
the premium, doubled when the contract traded fewer than 100 contracts that
session. Paper trading (step 5) records the live bid and ask at every
simulated fill, and those samples replace these numbers.
"""

from __future__ import annotations

TICK = 0.05
HALF_SPREAD_PCT = 0.5
THIN_CONTRACTS = 100


def half_spread(price: float, volume_contracts: int) -> float:
    half = max(TICK, price * HALF_SPREAD_PCT / 100)
    return 2 * half if volume_contracts < THIN_CONTRACTS else half


def fill_price(price: float, side: str, volume_contracts: int) -> float:
    """The price actually paid (buy) or received (sell) per unit."""
    half = half_spread(price, volume_contracts)
    return price + half if side == "buy" else max(price - half, 0.0)
```

- [ ] **Step 4: Run** — passes.

- [ ] **Step 5: Commit** — `git add fno/fills.py tests/test_fno_fills.py && git commit -m "feat(fno): fills pay half the spread, doubled in a thin market"`

---

### Task 3: The synthetic market, and choosing expiry and strikes

**Files:** Create `tests/fno_synthetic.py`, `fno/strikes.py`, `tests/test_fno_strikes.py`

- [ ] **Step 1: Write the test market** — `tests/fno_synthetic.py` (a helper, not a test module; `market()` imports `MarketData` lazily because Task 6 creates it):

```python
"""A small, hand-checkable NIFTY option market for the F&O engine's tests.

Six sessions in January 2024; a weekly expiry on Thursday 4 Jan, the next on
11 Jan, the monthly (with its future) on 25 Jan; strikes 21,000-22,000 every
100. Every price is Black-76 at 15% volatility on a flat forward of 21,500,
so the chain behaves like a real one (deltas, put-call parity), and single
prices can be overridden to stage a fill, a stop or a gap. On its expiry day
an option's settlement price is 0, as in NSE's old file. NIFTY closes at
21,560 on 4 Jan: the weekly's final settlement.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from fno import pricing
from fno.contracts import LotSizes
from fno.events import Events
from fno.nse_files import COLUMNS, DTYPES

D1, D2, D3, D4, D5, D6 = (date(2024, 1, 1), date(2024, 1, 2), date(2024, 1, 3),
                          date(2024, 1, 4), date(2024, 1, 5), date(2024, 1, 8))
DAYS = (D1, D2, D3, D4, D5, D6)
E1, E2, MONTHLY = date(2024, 1, 4), date(2024, 1, 11), date(2024, 1, 25)
STRIKES = tuple(float(k) for k in range(21000, 22001, 100))
FORWARD = 21500.0
SIGMA = 0.15
RATE = 0.068            # fno/rates.yaml for 2024, so parity gives back FORWARD
LOT = 50
EXPIRY_SPOT = 21560.0


def model(day: date, expiry: date, strike: float, typ: str) -> float:
    T = (expiry - day).days / 365
    if T <= 0:
        return max(FORWARD - strike, 0.0) if typ == "CE" else max(strike - FORWARD, 0.0)
    return round(float(pricing.black76(FORWARD, strike, T, SIGMA, RATE, typ == "CE")), 2)


def _row(d: date, e: date, instrument: str, strike: float, typ: Any, price: float, settle: float) -> dict:
    return {"trade_date": d, "underlying": "NIFTY", "instrument": instrument, "expiry": e,
            "strike": strike, "option_type": typ, "open": price, "high": price, "low": price,
            "close": price, "settle": settle, "volume_contracts": 1000, "oi_units": 100_000,
            "oi_change_units": 0, "turnover_inr": 0.0, "lot_size": np.nan, "underlying_price": np.nan}


def daily(overrides: dict | None = None) -> pd.DataFrame:
    """Every contract on every day; `overrides` maps (day, expiry, strike, type) to column values."""
    rows = []
    for d in DAYS:
        for e in (E1, E2, MONTHLY):
            if e < d:
                continue
            for k in STRIKES:
                for typ in ("CE", "PE"):
                    p = model(d, e, k, typ)
                    rows.append(_row(d, e, "OPT", k, typ, p, 0.0 if d == e else p))
        rows.append(_row(d, MONTHLY, "FUT", np.nan, None, FORWARD, FORWARD))
    frame = pd.DataFrame(rows, columns=list(COLUMNS))
    for (d, e, k, typ), values in (overrides or {}).items():
        at = ((frame["trade_date"] == d) & (frame["expiry"] == e)
              & (frame["strike"] == k) & (frame["option_type"] == typ))
        assert at.sum() == 1, (d, e, k, typ)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame.astype(DTYPES)


def facts(**columns: list) -> pd.DataFrame:
    """One facts row per session: VIX 14, IV rank 50, no events - unless overridden."""
    base = {"trade_date": list(DAYS), "vix": [14.0] * 6, "iv_rank": [50.0] * 6,
            "event_today": [False] * 6,
            "dte": [float((E1 - d).days if d < E1 else (E2 - d).days) for d in DAYS]}
    base.update(columns)
    return pd.DataFrame(base)


def spot(expiry_close: float = EXPIRY_SPOT) -> pd.DataFrame:
    close = [expiry_close if d == E1 else FORWARD for d in DAYS]
    return pd.DataFrame({"trade_date": list(DAYS), "open": close, "high": close,
                         "low": close, "close": close})


def market(*, overrides: dict | None = None, facts_frame: pd.DataFrame | None = None,
           spot_frame: pd.DataFrame | None = None, events: tuple = (), until: date | None = None):
    from fno.market import MarketData

    return MarketData.from_frames(
        "NIFTY", daily=daily(overrides),
        facts=facts() if facts_frame is None else facts_frame,
        spot=spot() if spot_frame is None else spot_frame,
        lots=LotSizes({"NIFTY": ((date(2016, 1, 1), LOT),)}),
        events=Events(tuple({"date": d, "kind": k} for d, k in events)),
        until=until)
```

- [ ] **Step 2: Write the failing tests** — `tests/test_fno_strikes.py`:

```python
"""Which expiry and which strikes a strategy's legs land on, on one day's chain."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain  # noqa: E402
from fno.strategy import Leg  # noqa: E402
from fno.strikes import NoStrike, pick_expiry, pick_strikes  # noqa: E402
from fno_synthetic import D1, D4, E1, E2, FORWARD, MONTHLY, RATE, daily, model  # noqa: E402


def snap(rows=None):
    return chain.snapshot(daily() if rows is None else rows, "NIFTY", D1, E1, spot=FORWARD, rate=RATE)


def leg(typ, rule, value, side="sell"):
    return Leg(side, typ, rule, value, 1)


def test_weekly_counts_from_the_nearest_expiry_with_a_day_to_run():
    rows = daily()
    assert pick_expiry(rows, D1, "weekly", 0) == E1
    assert pick_expiry(rows, D1, "weekly", 1) == E2
    assert pick_expiry(rows, D4, "weekly", 0) == E2      # E1 expires on D4: no day left


def test_monthly_means_an_expiry_that_has_a_future():
    rows = daily()
    assert pick_expiry(rows, D1, "monthly", 0) == MONTHLY
    assert pick_expiry(rows, D1, "monthly", 1) is None


def test_atm_counts_strikes_out_of_the_money_on_each_side():
    got = pick_strikes(snap(), [leg("CE", "atm", 0), leg("PE", "atm", 0),
                                leg("CE", "atm", 2), leg("PE", "atm", 2), leg("CE", "atm", -1)])
    assert got == [21500, 21500, 21700, 21300, 21400]


def test_atm_beyond_the_listed_strikes_is_refused():
    with pytest.raises(NoStrike, match="beyond"):
        pick_strikes(snap(), [leg("CE", "atm", 9)])


def test_otm_pct_is_measured_from_the_forward():
    assert pick_strikes(snap(), [leg("CE", "otm_pct", 1.0), leg("PE", "otm_pct", 1.0)]) == [21700, 21300]


def test_delta_picks_the_strike_with_the_nearest_delta():
    s = snap()
    got = pick_strikes(s, [leg("CE", "delta", 0.2), leg("PE", "delta", -0.2)])
    for strike, typ, target in zip(got, ("CE", "PE"), (0.2, -0.2)):
        side = s.rows[s.rows["option_type"] == typ]
        assert strike == side.loc[(side["delta"] - target).abs().idxmin(), "strike"]
    assert got[0] > FORWARD > got[1]


def test_premium_picks_the_nearest_settlement_price():
    assert pick_strikes(snap(), [leg("CE", "premium", model(D1, E1, 21700.0, "CE") + 0.4)]) == [21700]


def test_max_oi_and_a_wing_from_another_leg():
    rows = daily({(D1, E1, 21800.0, "CE"): {"oi_units": 999_999}})
    got = pick_strikes(snap(rows), [leg("CE", "max_oi", True), leg("CE", "from_leg", (0, 200.0), "buy")])
    assert got == [21800, 22000]
```

- [ ] **Step 3: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.strikes'`

- [ ] **Step 4: Write `fno/strikes.py`**

```python
"""Which expiry and which strikes a strategy's legs land on, on a decision day.

Only the decision day's rows are read - a ChainSnapshot, or that day's file -
so the choice cannot see a later price.

MONTHLY is an expiry that has a future that day (index futures are monthly
only); WEEKLY is every option expiry, so before NIFTY's weeklies began (Feb
2019) and after BANKNIFTY's ended (Nov 2024) "weekly" lands on the monthly.
`which` counts from the nearest expiry with at least a day to run.

STRIKES are always chosen among the strikes listed for that expiry and type,
nearest first; a tie goes to the lower strike.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import numpy as np
import pandas as pd

from fno.chain import ChainSnapshot, expiries


class NoStrike(LookupError):
    """A leg's rule has no strike on that day's chain."""


def expiries_of(day_rows: pd.DataFrame, day: date, cycle: str) -> list[date]:
    listed = [e for e in expiries(day_rows, day) if e > day]
    if cycle == "monthly":
        futures = set(day_rows.loc[(day_rows["trade_date"] == day)
                                   & (day_rows["instrument"] == "FUT"), "expiry"])
        listed = [e for e in listed if e in futures]
    return listed


def pick_expiry(day_rows: pd.DataFrame, day: date, cycle: str, which: int) -> date | None:
    listed = expiries_of(day_rows, day, cycle)
    return listed[which] if which < len(listed) else None


def _nearest(values: pd.Series, target: float):
    return (values - target).abs().idxmin()


def pick_strikes(snap: ChainSnapshot, legs: Sequence) -> list[float]:
    """One strike per leg, in order (a `from_leg` wing reads an earlier choice)."""
    rows = snap.rows[snap.rows["settle"] > 0]
    chosen: list[float] = []
    for i, leg in enumerate(legs):
        side = rows[rows["option_type"] == leg.type].sort_values("strike").reset_index(drop=True)
        if side.empty:
            raise NoStrike(f"leg {i}: no {leg.type} listed for {snap.expiry} on {snap.day}")
        strikes = side["strike"]
        if leg.rule in ("atm", "otm_pct") and not np.isfinite(snap.forward):
            raise NoStrike(f"leg {i}: no forward price for {snap.expiry} on {snap.day}")
        if leg.rule == "atm":
            at = int(_nearest(strikes, snap.forward))
            j = at + leg.value if leg.type == "CE" else at - leg.value
            if not 0 <= j < len(side):
                raise NoStrike(f"leg {i}: {leg.value} strikes from the money is beyond the listed strikes")
            strike = strikes[j]
        elif leg.rule == "otm_pct":
            move = 1 + leg.value / 100 if leg.type == "CE" else 1 - leg.value / 100
            strike = strikes[_nearest(strikes, snap.forward * move)]
        elif leg.rule == "delta":
            with_delta = side[side["delta"].notna()]
            if with_delta.empty:
                raise NoStrike(f"leg {i}: no {leg.type} has a delta (no IV) on {snap.day}")
            strike = with_delta.loc[_nearest(with_delta["delta"], leg.value), "strike"]
        elif leg.rule == "premium":
            strike = side.loc[_nearest(side["settle"], leg.value), "strike"]
        elif leg.rule == "max_oi":
            strike = side.loc[side["oi_units"].idxmax(), "strike"]
        else:                                   # from_leg
            j, offset = leg.value
            strike = strikes[_nearest(strikes, chosen[j] + offset)]
        chosen.append(float(strike))
    return chosen
```

- [ ] **Step 5: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_strikes.py -q` → all pass.

- [ ] **Step 6: Commit** — `git add fno/strikes.py tests/fno_synthetic.py tests/test_fno_strikes.py && git commit -m "feat(fno): choose the expiry and each leg's strike from one day's chain"`

---

### Task 4: Margin, estimated SPAN-style

**Files:** Create `fno/margin.py`, `tests/test_fno_margin.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_margin.py`:

```python
"""The margin a position blocks: a SPAN-style estimate (design §4)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import pricing  # noqa: E402
from fno.margin import EXPOSURE_PCT, MarginLeg, estimate, max_loss, scan_pct  # noqa: E402

F, T, R = 21500.0, 7 / 365, 0.068


def leg(sign, typ, strike, units=50, iv=0.15):
    price = float(pricing.black76(F, strike, T, iv, R, typ == "CE"))
    return MarginLeg(sign, typ == "CE", strike, units, iv, price)


def margin(legs, vix=14.0):
    return estimate(legs, forward=F, years=T, rate=R, vix=vix)


def test_the_scan_widens_with_vix_above_a_floor():
    assert scan_pct(10.0) == 6.0
    assert scan_pct(30.0) == 15.0
    assert scan_pct(float("nan")) == 6.0


def test_bought_options_block_the_premium_paid():
    legs = [leg(1, "CE", 21500), leg(1, "PE", 21500)]
    assert margin(legs) == pytest.approx(sum(x.price * x.units for x in legs))


def test_a_narrow_credit_spread_blocks_its_maximum_loss():
    legs = [leg(-1, "PE", 21400), leg(1, "PE", 21300)]
    credit = (legs[0].price - legs[1].price) * 50
    assert max_loss(legs) == pytest.approx(100 * 50 - credit)
    assert margin(legs) == pytest.approx(100 * 50 - credit)


def test_an_uncovered_call_can_lose_without_limit():
    assert max_loss([leg(-1, "CE", 21500)]) == float("inf")


def test_a_naked_straddle_blocks_scan_loss_plus_exposure_more_when_vix_is_high():
    legs = [leg(-1, "CE", 21500), leg(-1, "PE", 21500)]
    exposure = EXPOSURE_PCT / 100 * F * 100
    calm, fearful = margin(legs, vix=12.0), margin(legs, vix=30.0)
    premium = sum(x.price for x in legs)
    # +-6% is 1,290 points: one leg is that far in the money, both premiums were collected
    assert calm - exposure == pytest.approx((0.06 * F - premium) * 50, rel=0.05)
    assert fearful > calm
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.margin'`

- [ ] **Step 3: Write `fno/margin.py`**

```python
"""How much margin a position blocks: a SPAN-style estimate (design §4).

NOT the exchange's SPAN. The underlying is scanned +-SCAN (wider when India
VIX is high) at seven points, and volatility +-VOL_SCAN, repricing every leg
with Black-76; the worst loss is the "SPAN" part.

* Only bought options: the premium paid.
* Every sold option covered by a bought one of the same type (a spread, a
  condor): the larger of the scan loss and the maximum loss at expiry -
  brokers block about the maximum loss on a hedged position.
* Otherwise: the scan loss plus EXPOSURE_PCT of the sold notional.

The constants are starting values. Step 5 compares the estimate with the
broker's calculator on the live chain and states the error on every report;
until then every report says the margin is uncalibrated.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from fno.pricing import black76

SCAN_FLOOR_PCT = 6.0
SCAN_PER_VIX_POINT = 0.5          # scan, % of the underlying = max(floor, 0.5 x VIX)
VOL_SCAN = 0.04                   # absolute: 15% -> 11% and 19%
EXPOSURE_PCT = 2.0
MOVES = (-1.0, -2 / 3, -1 / 3, 0.0, 1 / 3, 2 / 3, 1.0)
MIN_VOL = 0.01


@dataclass(frozen=True)
class MarginLeg:
    sign: int          # +1 bought, -1 sold
    is_call: bool
    strike: float
    units: int         # lots x lot size
    iv: float
    price: float       # premium per unit at the decision


def scan_pct(vix: float) -> float:
    return SCAN_FLOOR_PCT if vix != vix else max(SCAN_FLOOR_PCT, SCAN_PER_VIX_POINT * vix)


def _payoff(legs: Sequence[MarginLeg], level: float) -> float:
    cost = sum(x.sign * x.price * x.units for x in legs)
    value = sum(x.sign * x.units * (max(level - x.strike, 0.0) if x.is_call else max(x.strike - level, 0.0))
                for x in legs)
    return value - cost


def max_loss(legs: Sequence[MarginLeg]) -> float:
    """The worst loss at expiry; infinite when sold calls outnumber bought ones."""
    if sum(x.sign * x.units for x in legs if x.is_call) < 0:
        return math.inf
    # The payoff is straight between strikes and does not fall above the top
    # one, so its lowest point is at 0 or at a strike.
    worst = min(_payoff(legs, level) for level in (0.0, *sorted({x.strike for x in legs})))
    return max(0.0, -worst)


def _covered(legs: Sequence[MarginLeg]) -> bool:
    calls = sum(x.sign * x.units for x in legs if x.is_call)
    puts = sum(x.sign * x.units for x in legs if not x.is_call)
    return calls >= 0 and puts >= 0


def estimate(legs: Sequence[MarginLeg], *, forward: float, years: float, rate: float, vix: float) -> float:
    if all(x.sign > 0 for x in legs):
        return sum(x.price * x.units for x in legs)
    T = max(years, 1 / 365)
    K = np.array([x.strike for x in legs])
    calls = np.array([x.is_call for x in legs])
    iv = np.array([x.iv for x in legs])
    weight = np.array([x.sign * x.units for x in legs], dtype=float)
    base = float(weight @ black76(forward, K, T, iv, rate, calls))
    scan = scan_pct(vix) / 100
    worst = 0.0
    for move in MOVES:
        for shift in (VOL_SCAN, -VOL_SCAN):
            vol = np.maximum(iv + shift, MIN_VOL)
            value = float(weight @ black76(forward * (1 + move * scan), K, T, vol, rate, calls))
            worst = max(worst, base - value)
    if _covered(legs):
        return max(worst, max_loss(legs))
    sold = sum(x.units for x in legs if x.sign < 0)
    return worst + EXPOSURE_PCT / 100 * forward * sold
```

- [ ] **Step 4: Run** — passes. (If `pricing.black76` does not broadcast a scalar forward against arrays, pass `np.full(len(legs), forward)`; check `fno/pricing.py`.)

- [ ] **Step 5: Commit** — `git add fno/margin.py tests/test_fno_margin.py && git commit -m "feat(fno): SPAN-style margin estimate, uncalibrated and saying so"`

---

### Task 5: The account

**Files:** Create `fno/account.py`, `tests/test_fno_account.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_account.py`:

```python
"""Rs 5 lakh, sized in whole sets by margin, judged by month and by day (design §4, §5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.account import day_returns, holding_month_returns, month_returns, sets_affordable  # noqa: E402


def test_whole_sets_never_more_margin_than_allowed():
    assert sets_affordable(500_000, 150_000) == 3
    assert sets_affordable(450_000, 150_000) == 3          # exactly fits
    assert sets_affordable(500_000, 150_000, 50) == 1
    assert sets_affordable(100_000, 150_000) == 0
    assert sets_affordable(500_000, 0) == 0


def test_months_compound_on_the_balance():
    equity = pd.Series([510_000.0, 520_000.0, 494_000.0, 500_000.0],
                       index=[date(2024, 1, 30), date(2024, 1, 31), date(2024, 2, 29), date(2024, 3, 28)])
    assert month_returns(equity, 500_000) == {
        "2024-01": 4.0, "2024-02": -5.0, "2024-03": pytest.approx(1.2146, abs=1e-4)}


def test_the_first_day_is_measured_from_the_capital():
    equity = pd.Series([450_000.0, 495_000.0], index=[date(2024, 1, 1), date(2024, 1, 2)])
    assert list(day_returns(equity, 500_000).round(4)) == [-10.0, 10.0]


def test_holding_starts_from_the_close_before_the_run():
    closes = {date(2023, 12, 29): 100.0, date(2024, 1, 31): 110.0, date(2024, 2, 29): 99.0}
    assert holding_month_returns(closes, date(2024, 1, 1), date(2024, 2, 29)) == {
        "2024-01": 10.0, "2024-02": -10.0}
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.account'`

- [ ] **Step 3: Write `fno/account.py`**

```python
"""The options account: Rs 5 lakh, whole sets, months and days (design §4, §5).

A SET is one of every leg at its `lots`. Sizing takes as many whole sets as
the allowed share of the balance covers in margin, never more - a balance
that has grown buys more sets, one that has shrunk buys fewer, so monthly
returns compound on the balance (the 22 Sep 2026 fix, research.portfolio).

The balance is cash plus open positions at the day's settlement prices.
Margin is not spent - it is blocked - so it changes the size, not the cash.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

import pandas as pd

CAPITAL = 500_000.0


def sets_affordable(equity: float, margin_per_set: float, max_margin_pct: float = 100.0) -> int:
    if margin_per_set <= 0 or equity <= 0:
        return 0
    return int(math.floor(equity * max_margin_pct / 100 / margin_per_set + 1e-9))


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _chain(month_ends: pd.Series, previous: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for month, value in month_ends.items():
        out[month] = round(100 * (float(value) / previous - 1), 4) if previous > 0 else 0.0
        previous = float(value)
    return out


def month_returns(equity: pd.Series, capital: float) -> dict[str, float]:
    """Each month-end balance over the previous one (the first over the capital), in %."""
    s = equity.sort_index()
    return _chain(s.groupby([_month(d) for d in s.index]).last(), capital)


def day_returns(equity: pd.Series, capital: float) -> pd.Series:
    """Each day's balance over the day before (the first over the capital), in %."""
    s = equity.sort_index().astype(float)
    previous = s.shift(1)
    previous.iloc[0] = capital
    return 100 * (s / previous - 1)


def holding_month_returns(closes: Mapping[date, float], start: date, end: date) -> dict[str, float]:
    """Holding the index over the same months, from the last close before `start`."""
    s = pd.Series(dict(closes), dtype=float).sort_index()
    before = s[[d < start for d in s.index]]
    inside = s[[start <= d <= end for d in s.index]]
    if inside.empty:
        return {}
    first = float(before.iloc[-1]) if len(before) else float(inside.iloc[0])
    return _chain(inside.groupby([_month(d) for d in inside.index]).last(), first)
```

- [ ] **Step 4: Run** — passes.

- [ ] **Step 5: Commit** — `git add fno/account.py tests/test_fno_account.py && git commit -m "feat(fno): the options account - whole sets by margin, months, days, holding"`

---

### Task 6: The market, one day at a time

**Files:** Create `fno/market.py`, `tests/test_fno_market.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_market.py`:

```python
"""What the engine may read about one underlying, and when (design §2.5, §6)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.market import training_window  # noqa: E402
from fno_synthetic import (  # noqa: E402
    D1, D2, D3, D4, D5, D6, DAYS, EXPIRY_SPOT, FORWARD, LOT, MONTHLY, facts, market, spot,
)
from strategy.expr import parse_expression  # noqa: E402


def test_sessions_and_the_next_one():
    m = market()
    assert m.sessions(D2, D4) == [D2, D3, D4]
    assert m.next_session(D4) == D5 and m.next_session(D6) is None


def test_a_day_s_rows_are_that_day_only():
    assert set(market().rows(D3)["trade_date"]) == {D3}


def test_until_cuts_everything_later_out():
    m = market(until=D3)
    assert m.sessions_all[-1] == D3
    assert m.rows(D4).empty
    assert D4 not in m.closes()


def test_final_settlement_the_expiring_future_then_the_index_close():
    m = market()
    assert m.final_settlement(MONTHLY, m.rows(D1)) == FORWARD       # a future expiring then
    assert m.final_settlement(D4, m.rows(D4)) == EXPIRY_SPOT         # a weekly: the index close
    bare = market(spot_frame=spot().iloc[:0])
    with pytest.raises(LookupError, match="no final settlement"):
        bare.final_settlement(D4, bare.rows(D4))


def test_lot_size_from_the_file_when_it_says_else_the_table():
    m = market()
    assert m.lot_size(D1, m.rows(D1)) == LOT
    assert m.lot_size(D1, m.rows(D1).assign(lot_size=75.0)) == 75


def test_days_to_the_strategy_s_own_expiry():
    m = market()
    weekly = m.expiry_dte("weekly", 0)
    assert (weekly[D1], weekly[D4]) == (3.0, 7.0)
    assert m.expiry_dte("monthly", 0)[D1] == 24.0


def test_sessions_to_event_counts_sessions_not_days():
    s = market(events=((D5, "rbi"),)).sessions_to_event()
    assert (s[D3], s[D4], s[D5]) == (2.0, 1.0, 0.0)
    assert np.isnan(s[D6])


def test_a_signal_reads_facts_candles_and_extra_facts():
    m = market(facts_frame=facts(vix=[12.0, 14.0, 16.0, 18.0, 20.0, 22.0]))
    on = m.signal(parse_expression("vix > 15 and close > 0 and expiry_dte < 5"),
                  {"expiry_dte": {d: 3.0 for d in DAYS}})
    assert [on[d] for d in DAYS] == [False, False, True, True, True, True]
    assert all(m.signal(None, {}).values())


def test_event_today_is_true_or_false_never_missing():
    m = market(facts_frame=facts(event_today=[False, True, False, False, False, False]))
    on = m.signal(parse_expression("not event_today"), {})
    assert [on[d] for d in DAYS] == [True, False, True, True, True, True]


def test_trend_from_the_fifty_day_average():
    assert market().trend(D1) == "unknown"
    days = pd.bdate_range(end=pd.Timestamp(D6), periods=90)
    rising = pd.DataFrame({"trade_date": [d.date() for d in days],
                           **{c: [100.0 * 1.01 ** i for i in range(90)] for c in ("open", "high", "low", "close")}})
    assert market(spot_frame=rising).trend(D6) == "rising"


def test_the_training_window_seals_the_last_365_days():
    sessions = [date(2016, 1, 1) + timedelta(days=i) for i in range(4000)]
    start, end = training_window(sessions)
    assert start == date(2016, 1, 1)
    assert end == sessions[-1] - timedelta(days=365)
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.market'`

- [ ] **Step 3: Write `fno/market.py`**

```python
"""Everything the options engine reads about one underlying (design §2.5, §4).

A decision on a day is handed that day's rows only; the fill at the next
open is handed the next session's rows. `until` cuts everything after a date
out at load - the atlas and a default backtest pass the training end, so the
exam year cannot be read even by mistake.

Two facts are made here rather than in fno.features, because they depend on
the strategy or on the calendar ahead:

* expiry_dte - calendar days to the strategy's OWN expiry (its cycle and
  `which`); the facts table's `dte` is always the nearest weekly.
* sessions_to_event - sessions until the next Budget / RBI / election-result
  day, 0 on the day, NaN beyond EVENT_HORIZON. Those dates are announced in
  advance and NSE publishes its holidays a year ahead, so counting forward
  to them is not hindsight.

TREND (for the report): the index's 50-day average against itself 20
sessions earlier - up more than 2% rising, down more than 2% falling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from expr_eval import evaluate
from fno import spot as spot_store
from fno import store
from fno.contracts import LotSizes
from fno.events import Events
from fno.nse_files import COLUMNS
from fno.strikes import pick_expiry
from fno.yahoo import read_whole
from strategy.expr import Expr

TREND_SMA = 50
TREND_LOOKBACK = 20
TREND_BAND_PCT = 2.0
EVENT_HORIZON = 10
EXAM_DAYS = 365
TRAINING_START = date(2016, 1, 1)
BOOL_FACTS = ("event_today",)


def training_window(sessions: list[date]) -> tuple[date, date]:
    """The training years: from 2016 to the last session before the exam year."""
    if not sessions:
        raise ValueError("no sessions stored")
    cutoff = sessions[-1] - timedelta(days=EXAM_DAYS)
    inside = [d for d in sessions if TRAINING_START <= d <= cutoff]
    if not inside:
        raise ValueError("no training sessions before the exam year")
    return inside[0], inside[-1]


def features_path(root: Path, underlying: str) -> Path:
    return Path(root) / "features" / f"{underlying}.parquet"


def stored_sessions(root: Path, underlying: str) -> list[date]:
    """Every session the facts table has, without loading the rest."""
    return sorted(pd.read_parquet(features_path(root, underlying), columns=["trade_date"])["trade_date"])


class MarketData:
    def __init__(self, underlying: str, *, facts: pd.DataFrame, spot: pd.DataFrame, lots: LotSizes,
                 read_year: Callable[[int], pd.DataFrame], calendar: pd.DataFrame, events: Events,
                 until: date | None = None) -> None:
        if until is not None:
            facts = facts[facts["trade_date"] <= until]
            spot = spot[spot["trade_date"] <= until]
            calendar = calendar[calendar["trade_date"] <= until]
        self.underlying = underlying
        self.until = until
        self.facts = facts.sort_values("trade_date").reset_index(drop=True)
        self.sessions_all: list[date] = list(self.facts["trade_date"])
        self._position = {d: i for i, d in enumerate(self.sessions_all)}
        self._facts_by_day = self.facts.set_index("trade_date")
        self.spot = spot.sort_values("trade_date").reset_index(drop=True)
        self._closes = dict(zip(self.spot["trade_date"], self.spot["close"].astype(float)))
        self.lots = lots
        self.calendar = calendar
        self.events = events
        self._read_year = read_year
        self._years: dict[int, dict[date, pd.DataFrame]] = {}
        self._trend = self._trend_labels()

    @classmethod
    def from_frames(cls, underlying: str, *, daily: pd.DataFrame, facts: pd.DataFrame,
                    spot: pd.DataFrame, lots: LotSizes, events: Events,
                    until: date | None = None) -> "MarketData":
        def read_year(year: int) -> pd.DataFrame:
            return daily[[d.year == year for d in daily["trade_date"]]].reset_index(drop=True)

        calendar = daily[["trade_date", "instrument", "expiry"]].drop_duplicates()
        return cls(underlying, facts=facts, spot=spot, lots=lots, read_year=read_year,
                   calendar=calendar, events=events, until=until)

    @classmethod
    def load(cls, root: Path, underlying: str, *, until: date | None = None,
             lots: LotSizes | None = None, events: Events | None = None) -> "MarketData":
        root = Path(root)
        facts = pd.read_parquet(features_path(root, underlying))
        spot = read_whole(spot_store.spot_path(root, underlying))
        years = sorted(int(p.stem) for p in (root / "daily" / underlying).glob("*.parquet"))
        calendar = pd.concat(
            [pd.read_parquet(store.daily_path(root, underlying, y),
                             columns=["trade_date", "instrument", "expiry"]).drop_duplicates()
             for y in years], ignore_index=True)

        def read_year(year: int) -> pd.DataFrame:
            return store.read_daily(root, underlying, date(year, 1, 1), date(year, 12, 31))

        return cls(underlying, facts=facts, spot=spot, lots=lots or LotSizes.load(),
                   read_year=read_year, calendar=calendar, events=events or Events.load(), until=until)

    # -- the calendar -------------------------------------------------------

    def sessions(self, start: date, end: date) -> list[date]:
        return [d for d in self.sessions_all if start <= d <= end]

    def next_session(self, day: date) -> date | None:
        i = self._position.get(day)
        return self.sessions_all[i + 1] if i is not None and i + 1 < len(self.sessions_all) else None

    # -- one day ------------------------------------------------------------

    def rows(self, day: date) -> pd.DataFrame:
        """That day's F&O rows and nothing else (empty after `until`)."""
        if self.until is not None and day > self.until:
            return pd.DataFrame(columns=list(COLUMNS))
        if day.year not in self._years:
            frame = self._read_year(day.year)
            self._years = {y: v for y, v in self._years.items() if y >= day.year - 1}
            self._years[day.year] = {d: g.reset_index(drop=True) for d, g in frame.groupby("trade_date")}
        return self._years[day.year].get(day, pd.DataFrame(columns=list(COLUMNS)))

    def fact(self, day: date, name: str) -> Any:
        try:
            return self._facts_by_day.at[day, name]
        except KeyError:
            return float("nan")

    def spot_close(self, day: date) -> float:
        return self._closes.get(day, float("nan"))

    def closes(self) -> dict[date, float]:
        return dict(self._closes)

    def lot_size(self, day: date, rows: pd.DataFrame) -> int:
        """NSE's own lot where the file has one (from 8 Jul 2024), else lot_sizes.yaml."""
        published = rows.loc[rows["instrument"] == "OPT", "lot_size"].dropna()
        if not published.empty:
            return int(published.mode().iloc[0])
        return self.lots.on(self.underlying, day)

    def final_settlement(self, expiry: date, rows: pd.DataFrame) -> float:
        """The index level an expiring option pays out on. On the expiry day NSE's old
        file gives the OPTION a settlement price of 0; the expiring future's settles at
        the index close, so that is read first, then the close itself."""
        fut = rows[(rows["instrument"] == "FUT") & (rows["expiry"] == expiry) & (rows["settle"] > 0)]
        if not fut.empty:
            return float(fut["settle"].iloc[0])
        close = self.spot_close(expiry)
        if close == close:
            return close
        published = rows["underlying_price"].dropna()
        if not published.empty:
            return float(published.iloc[0])
        raise LookupError(f"{self.underlying} {expiry}: no final settlement price "
                          "(no expiring future, no index close)")

    # -- facts the engine makes ---------------------------------------------

    def expiry_dte(self, cycle: str, which: int) -> dict[date, float]:
        out: dict[date, float] = {}
        for day, rows in self.calendar.groupby("trade_date"):
            expiry = pick_expiry(rows, day, cycle, which)
            out[day] = float((expiry - day).days) if expiry is not None else float("nan")
        return out

    def sessions_to_event(self) -> dict[date, float]:
        s = self.sessions_all
        if not s:
            return {}
        events = set(self.events.between(s[0], s[-1]))
        return {day: next((float(k) for k in range(EVENT_HORIZON + 1)
                           if i + k < len(s) and s[i + k] in events), float("nan"))
                for i, day in enumerate(s)}

    def _trend_labels(self) -> dict[date, str]:
        close = self.spot.set_index("trade_date")["close"].astype(float)
        sma = close.rolling(TREND_SMA).mean()
        change = 100 * (sma / sma.shift(TREND_LOOKBACK) - 1)
        return {day: ("unknown" if c != c else "rising" if c > TREND_BAND_PCT
                      else "falling" if c < -TREND_BAND_PCT else "sideways")
                for day, c in change.items()}

    def trend(self, day: date) -> str:
        return self._trend.get(day, "unknown")

    def signal(self, node: Expr | None, extra: dict[str, dict[date, float]]) -> dict[date, bool]:
        """Whether `node` holds at each session's close. The index's own daily candles
        are the bars (all of Yahoo's history, so sma(200) is ready in 2016); the facts
        and `extra` are variables, missing on days the F&O market has none."""
        if node is None:
            return {d: True for d in self.sessions_all}
        days = sorted(set(self.spot["trade_date"]) | set(self.sessions_all))
        index = pd.DatetimeIndex(pd.to_datetime(days))
        bars = self.spot.assign(trade_date=pd.to_datetime(self.spot["trade_date"])).set_index("trade_date")
        df = bars.reindex(index)[["open", "high", "low", "close"]].astype(float)
        facts = (self.facts.assign(trade_date=pd.to_datetime(self.facts["trade_date"]))
                 .set_index("trade_date").reindex(index))
        variables: dict[str, pd.Series] = {}
        for name in facts.columns:
            if name in BOOL_FACTS:
                variables[name] = facts[name].astype("boolean").fillna(False).astype(bool)
            elif pd.api.types.is_numeric_dtype(facts[name]):
                variables[name] = facts[name].astype(float)
        for name, values in extra.items():
            variables[name] = pd.Series({pd.Timestamp(d): v for d, v in values.items()},
                                        dtype=float).reindex(index)
        out = evaluate(node, df, variables)
        return {d: (lambda v: bool(v) if v == v else False)(out.get(pd.Timestamp(d), False))
                for d in self.sessions_all}
```

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_market.py tests/test_fno_strikes.py -q` → all pass.

- [ ] **Step 5: Commit** — `git add fno/market.py tests/test_fno_market.py && git commit -m "feat(fno): the market one day at a time, the exam year cut out at load"`

---

### Task 7: The engine

**Files:** Create `fno/engine.py`, `tests/test_fno_engine.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_engine.py`:

```python
"""The options backtester on daily data (design §4): hand-worked cases on a synthetic market."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.costs import Fill, FnoCharges  # noqa: E402
from fno.engine import CAPITAL, NO_CAPITAL, NO_TRADE, run  # noqa: E402
from fno.strategy import StrategyError, parse  # noqa: E402
from fno_synthetic import D1, D2, D3, D4, D5, D6, E1, E2, LOT, facts, market, model  # noqa: E402

CH = FnoCharges.load()
ATM = 21500.0
# The fill on D2 is staged at round numbers so the arithmetic can be read.
FILL = {(D2, E1, ATM, "CE"): {"open": 100.0}, (D2, E1, ATM, "PE"): {"open": 90.0}}


def straddle(side="sell", exit_="{}", sizing=""):
    return parse(f"""
version: options/1
name: test-straddle
underlying: NIFTY
mode: positional
expiry: {{cycle: weekly, which: 0}}
legs:
  - {{side: {side}, type: CE, strike: {{atm: 0}}, lots: 1}}
  - {{side: {side}, type: PE, strike: {{atm: 0}}, lots: 1}}
exit: {exit_}
{sizing}""")


def staged(*parts):
    out: dict = {}
    for part in parts:
        for key, values in part.items():
            out[key] = {**out.get(key, {}), **values}
    return out


def test_a_short_straddle_held_to_expiry_worked_by_hand():
    """Decided at D1's close on the 4 Jan weekly: ATM 21,500 (the forward is 21,500).
    Filled at D2's open: CE 100 - half-spread 0.50 = 99.50; PE 90 - 0.45 = 89.55.
    Settled on 4 Jan at NIFTY's close, 21,560: the CE pays 60 a unit, the PE nothing,
    and a SOLD option pays no exercise STT. Profit = (99.50 + 89.55 - 60) x units - charges."""
    result = run(straddle(), market(overrides=FILL), start=D1, end=D4)
    [t] = result.trades
    assert (t.decided, t.entered, t.exited, t.exit_reason, t.expiry) == (D1, D2, D4, "expiry", E1)
    assert [leg.strike for leg in t.legs] == [ATM, ATM]
    assert t.sets == int(CAPITAL // t.margin_per_set) > 0
    units = t.sets * LOT
    assert [leg.entry_price for leg in t.legs] == pytest.approx([99.50, 89.55])
    charges = CH.fill(Fill(D2, "OPT", "sell", 99.50, units)) + CH.fill(Fill(D2, "OPT", "sell", 89.55, units))
    assert t.charges == pytest.approx(charges)
    assert t.pnl == pytest.approx((99.50 + 89.55 - 60.0) * units - charges)
    assert result.equity[D4] == pytest.approx(CAPITAL + t.pnl)
    assert result.skips == []


def test_a_decision_never_sees_a_later_price():
    planted = {(d, e, k, t): {"settle": 5 * model(d, e, k, t) + 1}
               for d in (D2, D3) for e in (E1, E2) for k in (21300.0, 21500.0, 21700.0) for t in ("CE", "PE")}
    base = run(straddle(), market(overrides=FILL), start=D1, end=D4).trades[0]
    seen = run(straddle(), market(overrides=staged(planted, FILL)), start=D1, end=D4).trades[0]
    assert (seen.decided, [x.strike for x in seen.legs], seen.margin_per_set, seen.sets) == (
        base.decided, [x.strike for x in base.legs], base.margin_per_set, base.sets)


def test_a_leg_that_did_not_trade_skips_the_whole_entry():
    dead = {(D2, E1, ATM, "CE"): {"open": 0.0, "volume_contracts": 0}}
    result = run(straddle(), market(overrides=dead), start=D1, end=D4)
    assert [(s.day, s.kind) for s in result.skips] == [(D2, NO_TRADE)]
    assert "21500" in result.skips[0].detail
    [t] = result.trades
    assert (t.decided, t.entered) == (D2, D3)


def test_a_stop_is_seen_at_the_close_and_filled_at_the_next_open_gap_included():
    stop = {(D2, E1, ATM, "CE"): {"settle": 400.0}, (D3, E1, ATM, "CE"): {"open": 450.0}}
    [t] = run(straddle(exit_="{stop_pct: 100}"), market(overrides=staged(FILL, stop)),
              start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "stop")
    ce, pe = t.legs
    assert ce.exit_price == pytest.approx(450 + 2.25)
    pe_open = model(D3, E1, ATM, "PE")
    assert pe.exit_price == pytest.approx(pe_open + max(0.05, pe_open * 0.005))


def test_a_target_takes_half_the_premium():
    cheap = {(D2, E1, ATM, "CE"): {"settle": 40.0}, (D2, E1, ATM, "PE"): {"settle": 40.0}}
    [t] = run(straddle(exit_="{target_pct: 50}"), market(overrides=staged(FILL, cheap)),
              start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "target")


def test_days_before_expiry_closes_ahead_of_it():
    [t] = run(straddle(exit_="{days_before_expiry: 2}"), market(overrides=FILL), start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "days before expiry")


def test_the_strategy_s_own_exit_condition():
    m = market(overrides=FILL, facts_frame=facts(vix=[14.0, 25.0, 14.0, 14.0, 14.0, 14.0]))
    [t] = run(straddle(exit_='{when: "vix > 20"}'), m, start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "exit condition")


def test_a_bought_option_in_the_money_at_expiry_pays_stt_on_exercise():
    [t] = run(straddle(side="buy"), market(overrides=FILL), start=D1, end=D4).trades
    units = t.sets * LOT
    assert [x.entry_price for x in t.legs] == pytest.approx([100.5, 90.45])
    fees = (CH.fill(Fill(D2, "OPT", "buy", 100.5, units)) + CH.fill(Fill(D2, "OPT", "buy", 90.45, units))
            + CH.exercise(D4, 60.0 * units))
    assert t.charges == pytest.approx(fees)
    assert t.pnl == pytest.approx((60.0 - 100.5 - 90.45) * units - fees)


def test_an_exit_cannot_be_skipped_an_untraded_leg_closes_at_settlement():
    stop = {(D2, E1, ATM, "CE"): {"settle": 400.0}, (D3, E1, ATM, "CE"): {"open": 450.0},
            (D3, E1, ATM, "PE"): {"open": 0.0, "volume_contracts": 0, "settle": 30.0}}
    [t] = run(straddle(exit_="{stop_pct: 100}"), market(overrides=staged(FILL, stop)),
              start=D1, end=D4).trades
    pe = t.legs[1]
    assert pe.exit_price == pytest.approx(30.0 + 2 * max(0.05, 30 * 0.005))
    assert "settlement" in pe.note


def test_too_little_capital_skips_and_says_what_one_set_needs():
    result = run(straddle(), market(overrides=FILL), start=D1, end=D4, capital=1_000)
    assert result.trades == []
    assert {s.kind for s in result.skips} == {NO_CAPITAL}
    assert "margin" in result.skips[0].detail


def test_sizing_uses_only_the_allowed_share_of_the_balance():
    full = run(straddle(), market(overrides=FILL), start=D1, end=D4).trades[0]
    half = run(straddle(sizing="sizing: {max_margin_pct: 50}"), market(overrides=FILL),
               start=D1, end=D4).trades[0]
    assert half.sets == int(CAPITAL * 0.5 // half.margin_per_set)
    assert half.sets < full.sets


def test_after_expiry_the_next_position_opens_the_session_after():
    result = run(straddle(), market(overrides=FILL), start=D1, end=D6)
    first, second = result.trades
    assert (first.exited, second.decided, second.entered, second.expiry) == (D4, D4, D5, E2)
    assert second.exit_reason == "open at end" and second.exited is None


def test_a_position_open_at_the_end_is_marked_not_closed():
    result = run(straddle(), market(overrides=FILL), start=D1, end=D3)
    [t] = result.trades
    assert t.exited is None and t.exit_reason == "open at end"
    assert result.equity[D3] == pytest.approx(CAPITAL + t.pnl)


def test_intraday_strategies_wait_for_minute_data():
    s = parse("""
version: options/1
name: intraday
underlying: NIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry: {window: {from: "09:30", to: "10:00"}}
legs: [{side: sell, type: CE, strike: {atm: 0}, lots: 1}]
exit: {time: "15:15"}
""")
    with pytest.raises(StrategyError, match="minute data"):
        run(s, market(), start=D1, end=D4)
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError: No module named 'fno.engine'`

- [ ] **Step 3: Write `fno/engine.py`**

```python
"""The options backtester on daily data (design §4): positional strategies.

ONE SESSION, IN THIS ORDER
  1. At the open: fill what was decided at the last close - each leg at its
     contract's opening price, paying half the spread (fno.fills) and the
     charges in force that day (fno.costs).
  2. A position whose expiry is today settles at the close: in-the-money
     options pay intrinsic value at the final settlement price
     (MarketData.final_settlement), bought ones pay STT on exercise, the rest
     expire worthless.
  3. Whatever is still open is marked at the day's settlement prices.
  4. Exits are checked on those marks: stop, then target, then days before
     expiry, then the strategy's own condition. A hit is filled at the next
     open - a gap past the stop costs the gap, as it would.
  5. Flat with nothing pending, and the entry condition true: choose expiry
     and strikes from today's chain, size by margin, fill at the next open.

NO PHANTOM FILLS. An entry leg whose contract did not trade in the fill
session skips the whole entry, recorded with the reason. An EXIT cannot be
skipped - the position has to be closed - so an untraded leg closes at its
settlement price with the thin-market spread, and the leg says so.

Daily data cannot see inside a day: stops are checked on the close only, and
every result carries that note.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from fno import account, chain, fills, margin
from fno.costs import Fill, FnoCharges
from fno.market import MarketData
from fno.rates import risk_free
from fno.strategy import OptionsStrategy, StrategyError
from fno.strikes import NoStrike, pick_expiry, pick_strikes

CAPITAL = account.CAPITAL

NO_EXPIRY = "no expiry"
NO_STRIKE = "no strike"
NO_CAPITAL = "not enough capital"
NO_TRADE = "leg did not trade"

NOTES = (
    "Daily data: decisions at the close, fills at the next session's open. Stops and "
    "targets are checked on closing prices only - a move through a stop inside a day "
    "is not seen, and the exit fills at the next open, gap included.",
    "Margin is a SPAN-style estimate, not yet calibrated against a broker's calculator "
    "(design §4; calibration comes with paper trading).",
    "Lot sizes before 8 Jul 2024 come from fno/lot_sizes.yaml (derived from futures "
    "turnover, checked against NSE circulars).",
)


@dataclass
class TradeLeg:
    type: str
    side: str
    strike: float
    units: int
    entry_price: float
    mark: float
    exit_price: float | None = None
    note: str = ""

    @property
    def sign(self) -> int:
        return 1 if self.side == "buy" else -1


@dataclass
class Trade:
    underlying: str
    expiry: date
    decided: date
    entered: date
    sets: int
    lot_size: int
    margin_per_set: float
    equity_at_entry: float
    facts: dict[str, Any]
    legs: list[TradeLeg]
    premium: float          # net premium at entry, rupees: + received, - paid
    cash: float             # every rupee in and out so far, charges included
    charges: float
    exited: date | None = None
    exit_reason: str = "open at end"
    pnl: float = 0.0

    def value(self) -> float:
        """The open legs at their marks: + what bought legs are worth, - what sold ones cost back."""
        return sum(leg.sign * leg.mark * leg.units for leg in self.legs)

    def days_held(self, end: date) -> int:
        return ((self.exited or end) - self.entered).days


@dataclass(frozen=True)
class Skip:
    day: date
    kind: str
    detail: str


@dataclass(frozen=True)
class _Plan:
    expiry: date
    strikes: tuple[float, ...]
    sets: int
    lot_size: int
    margin_per_set: float
    decided: date
    equity: float
    facts: dict[str, Any]


@dataclass
class BacktestResult:
    strategy: str
    underlying: str
    start: date
    end: date
    capital: float
    trades: list[Trade]
    skips: list[Skip]
    equity: pd.Series
    notes: tuple[str, ...] = NOTES


def _contract(rows: pd.DataFrame, expiry: date, typ: str, strike: float) -> pd.Series | None:
    hit = rows[(rows["instrument"] == "OPT") & (rows["expiry"] == expiry)
               & (rows["option_type"] == typ) & (rows["strike"] == strike)]
    return None if hit.empty else hit.iloc[0]


def _traded(row: pd.Series | None) -> bool:
    return row is not None and row["volume_contracts"] > 0 and row["open"] > 0


def _flag(value: Any) -> bool:
    return bool(value) if value == value else False


def _plan(strategy: OptionsStrategy, market: MarketData, rows: pd.DataFrame,
          day: date, equity: float) -> _Plan | Skip:
    expiry = pick_expiry(rows, day, strategy.cycle, strategy.which)
    if expiry is None:
        return Skip(day, NO_EXPIRY, f"no {strategy.cycle} expiry number {strategy.which} listed on {day}")
    snap = chain.snapshot(rows, market.underlying, day, expiry,
                          spot=market.spot_close(day), rate=risk_free(day))
    try:
        strikes = pick_strikes(snap, strategy.legs)
    except NoStrike as exc:
        return Skip(day, NO_STRIKE, str(exc))
    lot = market.lot_size(day, rows)
    known_iv = snap.rows["iv"].dropna()
    fallback_iv = float(np.median(known_iv)) if len(known_iv) else 0.2
    legs = []
    for leg, strike in zip(strategy.legs, strikes):
        row = _contract(snap.rows, expiry, leg.type, strike)
        iv = float(row["iv"]) if np.isfinite(row["iv"]) else fallback_iv
        legs.append(margin.MarginLeg(leg.sign, leg.type == "CE", strike, leg.lots * lot, iv,
                                     float(row["settle"])))
    vix = float(market.fact(day, "vix"))
    per_set = margin.estimate(legs, forward=snap.forward, years=snap.dte / 365, rate=snap.rate, vix=vix)
    sets = account.sets_affordable(equity, per_set, strategy.max_margin_pct)
    if sets == 0:
        return Skip(day, NO_CAPITAL, f"one set needs Rs {per_set:,.0f} of margin; Rs {equity:,.0f} "
                                     f"at {strategy.max_margin_pct:g}% allows none")
    facts = {"vix": vix, "iv_rank": float(market.fact(day, "iv_rank")),
             "event_today": _flag(market.fact(day, "event_today")),
             "dte": (expiry - day).days, "trend": market.trend(day)}
    return _Plan(expiry, tuple(strikes), sets, lot, per_set, day, equity, facts)


def _open(strategy: OptionsStrategy, plan: _Plan, underlying: str, rows: pd.DataFrame,
          day: date, charges: FnoCharges) -> Trade | Skip:
    legs, premium, fees = [], 0.0, 0.0
    for leg, strike in zip(strategy.legs, plan.strikes):
        row = _contract(rows, plan.expiry, leg.type, strike)
        if not _traded(row):
            return Skip(day, NO_TRADE, f"{leg.type} {strike:g} expiring {plan.expiry} "
                                       f"did not trade on {day}")
        units = leg.lots * plan.lot_size * plan.sets
        price = fills.fill_price(float(row["open"]), leg.side, int(row["volume_contracts"]))
        fee = charges.fill(Fill(day, "OPT", leg.side, price, units))
        premium -= leg.sign * price * units
        fees += fee
        legs.append(TradeLeg(leg.type, leg.side, strike, units, price, price))
    return Trade(underlying, plan.expiry, plan.decided, day, plan.sets, plan.lot_size,
                 plan.margin_per_set, plan.equity, plan.facts, legs, premium, premium - fees, fees)


def _mark(trade: Trade, rows: pd.DataFrame) -> None:
    for leg in trade.legs:
        row = _contract(rows, trade.expiry, leg.type, leg.strike)
        if row is not None and row["settle"] > 0:
            leg.mark = float(row["settle"])


def _exit_reason(strategy: OptionsStrategy, trade: Trade, day: date, condition: bool) -> str | None:
    ex = strategy.exit
    basis = abs(trade.premium)
    pnl = trade.premium + trade.value()          # before charges, the usual convention
    if ex.stop_pct is not None and basis > 0 and pnl <= -ex.stop_pct / 100 * basis:
        return "stop"
    if ex.target_pct is not None and basis > 0 and pnl >= ex.target_pct / 100 * basis:
        return "target"
    if ex.days_before_expiry > 0 and (trade.expiry - day).days <= ex.days_before_expiry:
        return "days before expiry"
    if condition:
        return "exit condition"
    return None


def _book(trade: Trade, flow: float, day: date, reason: str) -> float:
    trade.cash += flow
    trade.exited, trade.exit_reason, trade.pnl = day, reason, trade.cash
    return flow


def _close(trade: Trade, rows: pd.DataFrame, day: date, reason: str, charges: FnoCharges) -> float:
    flow = 0.0
    for leg in trade.legs:
        row = _contract(rows, trade.expiry, leg.type, leg.strike)
        side = "sell" if leg.side == "buy" else "buy"
        if _traded(row):
            price = fills.fill_price(float(row["open"]), side, int(row["volume_contracts"]))
        else:
            reference = float(row["settle"]) if row is not None and row["settle"] > 0 else leg.mark
            price = fills.fill_price(reference, side, 0)
            leg.note = "no trade at the open: closed at the settlement price, thin-market spread"
        fee = charges.fill(Fill(day, "OPT", side, price, leg.units))
        flow += leg.sign * price * leg.units - fee
        trade.charges += fee
        leg.exit_price = leg.mark = price
    return _book(trade, flow, day, reason)


def _settle(trade: Trade, level: float, day: date, charges: FnoCharges) -> float:
    flow = 0.0
    for leg in trade.legs:
        intrinsic = max(level - leg.strike, 0.0) if leg.type == "CE" else max(leg.strike - level, 0.0)
        flow += leg.sign * intrinsic * leg.units
        if leg.sign > 0 and intrinsic > 0:
            fee = charges.exercise(day, intrinsic * leg.units)
            flow -= fee
            trade.charges += fee
        leg.exit_price = leg.mark = intrinsic
    return _book(trade, flow, day, "expiry")


def run(strategy: OptionsStrategy, market: MarketData, *, start: date, end: date,
        capital: float = CAPITAL, charges: FnoCharges | None = None) -> BacktestResult:
    if strategy.mode != "positional":
        raise StrategyError(f"{strategy.name}: intraday strategies need minute data (step 3)")
    if strategy.underlying != market.underlying:
        raise ValueError(f"{strategy.name} trades {strategy.underlying}; the data is {market.underlying}")
    charges = charges or FnoCharges.load()
    sessions = market.sessions(start, end)
    extra = {"expiry_dte": market.expiry_dte(strategy.cycle, strategy.which),
             "sessions_to_event": market.sessions_to_event()}
    enter_on = market.signal(strategy.entry_when, extra)
    exit_on = market.signal(strategy.exit.when, extra) if strategy.exit.when is not None else {}

    cash = float(capital)
    trade: Trade | None = None
    pending: tuple[str, Any] | None = None
    trades: list[Trade] = []
    skips: list[Skip] = []
    equity: dict[date, float] = {}
    for i, day in enumerate(sessions):
        rows = market.rows(day)
        if pending is not None:
            kind, payload = pending
            pending = None
            if kind == "enter":
                opened = _open(strategy, payload, market.underlying, rows, day, charges)
                if isinstance(opened, Skip):
                    skips.append(opened)
                else:
                    trade = opened
                    cash += trade.cash
            else:
                cash += _close(trade, rows, day, payload, charges)
                trades.append(trade)
                trade = None
        if trade is not None and trade.expiry < day:
            raise RuntimeError(f"{trade.expiry}: a position's expiry passed with no session to settle it")
        if trade is not None and trade.expiry == day:
            cash += _settle(trade, market.final_settlement(day, rows), day, charges)
            trades.append(trade)
            trade = None
        if trade is not None:
            _mark(trade, rows)
        equity[day] = cash + (trade.value() if trade is not None else 0.0)
        if i + 1 == len(sessions):
            break
        if trade is not None:
            reason = _exit_reason(strategy, trade, day, exit_on.get(day, False))
            if reason:
                pending = ("exit", reason)
        elif enter_on.get(day, False):
            plan = _plan(strategy, market, rows, day, equity[day])
            if isinstance(plan, Skip):
                skips.append(plan)
            else:
                pending = ("enter", plan)
    if trade is not None:
        trade.pnl = trade.cash + trade.value()
        trades.append(trade)
    return BacktestResult(strategy.name, strategy.underlying, start, end, float(capital),
                          trades, skips, pd.Series(equity, dtype=float))
```

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_engine.py -q` → all pass. If the hand-worked test's `sets` is 0, the margin is above ₹5 lakh for one 50-unit straddle — wrong by an order of magnitude; investigate `fno/margin.py` rather than the test.

- [ ] **Step 5: Commit** — `git add fno/engine.py tests/test_fno_engine.py && git commit -m "feat(fno): the daily options engine - fills, stops, expiry settlement, margin sizing"`

---

### Task 8: The report

**Files:** Create `fno/report.py`, `tests/test_fno_report.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_report.py`:

```python
"""What each options result shows (design §5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import report  # noqa: E402
from fno.engine import run  # noqa: E402
from fno_synthetic import D1, D2, D6, DAYS, E1, FORWARD, market  # noqa: E402
from test_fno_engine import FILL, straddle  # noqa: E402

HOLDING = {date(2023, 12, 29): FORWARD, **{d: FORWARD for d in DAYS}}


def result():
    return run(straddle(), market(overrides=FILL), start=D1, end=D6)


def test_the_headline_and_the_split_by_market_condition():
    r = report.build(result(), HOLDING)
    assert r.headline["trades"] == 2
    vix = {row["label"]: row for row in r.splits["vix at entry"]}
    assert vix["normal (13-18)"]["trades"] == 2
    assert [row["label"] for row in r.splits["event day"]] == ["other days"]
    assert {row["label"] for row in r.splits["days to expiry at entry"]} == {"0-3 days", "4-7 days"}
    assert any("closing prices" in note for note in r.notes)


def test_worst_days_and_named_shock_days():
    res = result()
    days = [date(2024, 6, 3), date(2024, 6, 4), date(2024, 6, 5)]
    res.equity = pd.Series([500_000.0, 460_000.0, 470_000.0], index=days)
    res.start, res.end = days[0], days[-1]
    r = report.build(res, {date(2024, 5, 31): 22_000.0, **{d: 22_000.0 for d in days}})
    assert r.headline["worst_day_pct"] == pytest.approx(-8.0)
    assert r.headline["worst_day"] == date(2024, 6, 4)
    assert r.worst_days[0] == {"day": date(2024, 6, 4), "pct": pytest.approx(-8.0), "rupees": -40_000.0}
    assert [s["day"] for s in r.shock_days] == [date(2024, 6, 4)]


def test_the_text_names_the_strategy_and_the_caveats():
    text = report.to_text(report.build(result(), HOLDING))
    assert "test-straddle" in text and "Ten worst days" in text and "closing prices" in text
```

- [ ] **Step 2: Run to see them fail** — `ModuleNotFoundError` (or `AttributeError: module 'fno' has no attribute 'report'`).

- [ ] **Step 3: Write `fno/report.py`**

```python
"""What each options result shows (design §5).

The month-by-month reading is the one every stock result is judged by
(research.portfolio.summarise) - the same average and median month, worst
fall, years beating holding and luck test - with holding the INDEX on the
same capital as the comparison. On top of it: the worst single day, every
trade split by the market it was opened in, the ten worst days and the named
shock days, and the notes that say what daily data cannot see.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from fno import account
from fno.engine import BacktestResult, Trade
from research.portfolio import Basket, luck_label, summarise

SHOCK_DAYS = {
    date(2020, 3, 12): "Covid crash",
    date(2020, 3, 23): "Covid low",
    date(2022, 2, 24): "Russia invades Ukraine",
    date(2024, 6, 4): "General-election result",
}


def _vix_band(v: float) -> str:
    if v != v:
        return "unknown"
    return "calm (<13)" if v < 13 else "normal (13-18)" if v <= 18 else "fearful (>18)"


def _dte_band(d: int) -> str:
    return "0-3 days" if d <= 3 else "4-7 days" if d <= 7 else "8-15 days" if d <= 15 else "16+ days"


def _iv_band(r: float) -> str:
    if r != r:
        return "unknown"
    return "low third" if r < 100 / 3 else "middle third" if r < 200 / 3 else "top third"


SPLITS: tuple[tuple[str, Callable[[dict[str, Any]], str]], ...] = (
    ("vix at entry", lambda f: _vix_band(f["vix"])),
    ("trend at entry", lambda f: f["trend"]),
    ("days to expiry at entry", lambda f: _dte_band(f["dte"])),
    ("iv rank at entry", lambda f: _iv_band(f["iv_rank"])),
    ("event day", lambda f: "event days" if f["event_today"] else "other days"),
)


@dataclass(frozen=True)
class Report:
    name: str
    underlying: str
    start: date
    end: date
    capital: float
    headline: dict[str, Any]
    basket: Basket
    splits: dict[str, list[dict[str, Any]]]
    worst_days: list[dict[str, Any]]
    shock_days: list[dict[str, Any]]
    skipped: dict[str, int]
    notes: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "underlying": self.underlying, "start": self.start,
                "end": self.end, "capital": self.capital, "headline": self.headline,
                "basket": self.basket.as_dict(), "splits": self.splits,
                "worst_days": self.worst_days, "shock_days": self.shock_days,
                "skipped": self.skipped, "notes": self.notes}


def _month(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


def _months_open(trade: Trade, end: date) -> set[str]:
    out, day, last = set(), trade.entered.replace(day=1), trade.exited or end
    while day <= last:
        out.add(_month(day))
        day = (day + timedelta(days=32)).replace(day=1)
    return out


def _split(trades: list[Trade], label: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    groups: dict[str, list[Trade]] = {}
    for t in trades:
        groups.setdefault(label(t.facts), []).append(t)
    rows = []
    for name, group in sorted(groups.items()):
        pnl = [t.pnl for t in group]
        rows.append({"label": name, "trades": len(group),
                     "win_rate_pct": round(100 * sum(p > 0 for p in pnl) / len(group), 2),
                     "avg_pnl": round(statistics.mean(pnl), 2), "total_pnl": round(sum(pnl), 2),
                     "avg_return_pct": round(statistics.mean(100 * t.pnl / t.equity_at_entry
                                                             for t in group), 4)})
    return rows


def build(result: BacktestResult, holding_closes: Mapping[date, float]) -> Report:
    if result.equity.empty:
        raise ValueError(f"{result.strategy}: no sessions between {result.start} and {result.end}")
    equity = result.equity.sort_index()
    months = account.month_returns(equity, result.capital)
    holding = account.holding_month_returns(holding_closes, result.start, result.end)
    opened = Counter(_month(t.entered) for t in result.trades)
    won = Counter(_month(t.entered) for t in result.trades if t.pnl > 0)
    rows = [{"month": m, "strategy_pct": pct, "holding_pct": holding.get(m, 0.0),
             "trades": opened.get(m, 0), "wins": won.get(m, 0), "stocks": 1}
            for m, pct in months.items()]
    active = set().union(*(_months_open(t, result.end) for t in result.trades)) & set(months)
    basket = summarise("day", 1, rows, stock_months=len(rows), trading_months=len(active))

    days = account.day_returns(equity, result.capital)
    previous = equity.shift(1)
    previous.iloc[0] = result.capital
    n = len(result.trades)
    headline = {
        "end_value": round(float(equity.iloc[-1]), 2),
        "avg_month_pct": basket.avg_month_pct,
        "median_month_pct": basket.median_month_pct,
        "months_positive_pct": basket.months_positive_pct,
        "worst_fall_pct": basket.worst_dip_pct,
        "worst_day_pct": round(float(days.min()), 4),
        "worst_day": days.idxmin(),
        "trades": n,
        "trades_per_month": basket.trades_per_month,
        "win_rate_pct": round(100 * sum(t.pnl > 0 for t in result.trades) / n, 2) if n else 0.0,
        "avg_days_held": round(statistics.mean(t.days_held(result.end) for t in result.trades), 2) if n else 0.0,
        "total_charges": round(sum(t.charges for t in result.trades), 2),
        "avg_holding_month_pct": basket.avg_holding_month_pct,
        "years_beating_holding_pct": basket.years_beating_holding_pct,
        "edge_t": basket.edge_t,
    }
    worst = [{"day": d, "pct": round(float(p), 4), "rupees": round(float(equity[d] - previous[d]), 2)}
             for d, p in days.nsmallest(10).items()]
    shocks = [{"day": d, "name": name, "pct": round(float(days[d]), 4)}
              for d, name in SHOCK_DAYS.items() if d in days.index]
    return Report(result.strategy, result.underlying, result.start, result.end, result.capital,
                  headline, basket, {title: _split(result.trades, label) for title, label in SPLITS},
                  worst, shocks, dict(Counter(s.kind for s in result.skips)), list(result.notes))


def _rs(x: float) -> str:
    return "n/a" if x is None or (isinstance(x, float) and math.isnan(x)) else f"Rs {x:,.0f}"


def to_text(r: Report) -> str:
    h = r.headline
    lines = [
        f"# {r.name} - {r.underlying}, {r.start} to {r.end}", "",
        f"{_rs(r.capital)} became {_rs(h['end_value'])}.",
        f"Average month {h['avg_month_pct']:+.2f}% (median {h['median_month_pct']:+.2f}%), "
        f"{h['months_positive_pct']:.0f}% of months up, worst fall {h['worst_fall_pct']:.1f}%, "
        f"worst day {h['worst_day_pct']:+.2f}% on {h['worst_day']}.",
        f"{h['trades']} trades ({h['trades_per_month']:.1f} a month), {h['win_rate_pct']:.0f}% won, "
        f"held {h['avg_days_held']:.1f} days on average, {_rs(h['total_charges'])} in charges.",
        f"Holding the index: {h['avg_holding_month_pct']:+.2f}% a month. Beat it in "
        f"{h['years_beating_holding_pct']:.0f}% of years; luck test: {luck_label(h['edge_t'])}.",
    ]
    for title, rows in r.splits.items():
        lines += ["", f"## By {title}", "", "| | trades | won | avg P&L | total P&L |",
                  "|---|---:|---:|---:|---:|"]
        lines += [f"| {row['label']} | {row['trades']} | {row['win_rate_pct']:.0f}% | "
                  f"{_rs(row['avg_pnl'])} | {_rs(row['total_pnl'])} |" for row in rows]
    lines += ["", "## Ten worst days", ""]
    lines += [f"- {d['day']}: {d['pct']:+.2f}% ({_rs(d['rupees'])})" for d in r.worst_days]
    if r.shock_days:
        lines += ["", "## Named shock days", ""]
        lines += [f"- {s['day']} {s['name']}: {s['pct']:+.2f}%" for s in r.shock_days]
    if r.skipped:
        lines += ["", "## Entries skipped", ""] + [f"- {k}: {n}" for k, n in sorted(r.skipped.items())]
    lines += ["", "## Read with care", ""] + [f"- {note}" for note in r.notes]
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_report.py -q` → all pass.

- [ ] **Step 5: Commit** — `git add fno/report.py tests/test_fno_report.py && git commit -m "feat(fno): options report - months, worst day, splits by market condition, shock days"`

---

### Task 9: The atlas and the two scripts

**Files:** Create `fno/atlas.py`, `tests/test_fno_atlas.py`, `scripts/fno_backtest.py`, `scripts/fno_atlas.py`

- [ ] **Step 1: Write the failing tests** — `tests/test_fno_atlas.py`:

```python
"""The options atlas: classic strategies, every one a valid document (design §7)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import atlas  # noqa: E402
from fno.strategy import parse  # noqa: E402


def test_every_atlas_strategy_parses_for_both_underlyings():
    parsed = [parse(doc) for doc in atlas.documents()]
    assert {s.underlying for s in parsed} == {"NIFTY", "BANKNIFTY"}
    assert len({s.name for s in parsed}) == len(parsed) == 2 * len(atlas.ATLAS)
    assert all(s.mode == "positional" for s in parsed)


def test_the_design_s_list_is_all_there():
    assert set(atlas.ATLAS) == {
        "short-straddle", "short-strangle-15d", "short-strangle-20d", "short-strangle-30d",
        "iron-condor", "bull-put-spread", "bear-call-spread", "long-straddle-before-events"}


def test_the_event_straddle_buys_ahead_and_sells_after():
    s = parse(atlas.document("long-straddle-before-events", "NIFTY"))
    assert all(leg.side == "buy" for leg in s.legs)
    assert s.which == 1 and s.entry_when is not None and s.exit.when is not None
```

- [ ] **Step 2: Run to see them fail** — `ImportError: cannot import name 'atlas'`

- [ ] **Step 3: Write `fno/atlas.py`**

```python
"""The options atlas: classic positional strategies, measured before the AI runs (design §7).

Each is the textbook version - no filters - so the AI starts from what the
plain building blocks earned on this data. "Weekly" lands on the nearest
listed expiry, which is the monthly before NIFTY's weeklies began (Feb 2019)
and after BANKNIFTY's ended (Nov 2024).

Measured on the TRAINING years only (fno.market.training_window): the exam
year is cut out at load and never read.
"""

from __future__ import annotations

from typing import Any

import yaml

from fno.strategy import VERSION

UNDERLYINGS = ("NIFTY", "BANKNIFTY")
HOLD = {"target_pct": 50, "stop_pct": 100}


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
    return yaml.safe_dump(doc, sort_keys=False)


def documents() -> list[str]:
    return [document(name, u) for u in UNDERLYINGS for name in ATLAS]
```

- [ ] **Step 4: Run** — passes.

- [ ] **Step 5: Write `scripts/fno_backtest.py`**

```python
"""Backtest one options strategy on daily data and print its report.

    python scripts/fno_backtest.py strategy.yaml
    python scripts/fno_backtest.py strategy.yaml --from 2019-01-01 --to 2022-12-31
    python scripts/fno_backtest.py strategy.yaml --json report.json

Runs the training years unless told otherwise: the exam year (the last 365
days) stays sealed unless --to reaches into it, and then the run says so.
Needs the F&O store, data/fno/features/ (scripts/fno_features.py) and
fno/lot_sizes.yaml (scripts/fno_lot_sizes.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import report, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("path", type=Path)
    parser.add_argument("--from", dest="start", type=date.fromisoformat)
    parser.add_argument("--to", dest="end", type=date.fromisoformat)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    strategy = parse(args.path.read_text(encoding="utf-8"))
    root = store.root_dir()
    start, end = training_window(stored_sessions(root, strategy.underlying))
    if args.end and args.end > end:
        print(f"NOTE: --to {args.end} reaches into the exam year (it starts after {end}).")
    start, end = args.start or start, args.end or end
    market = MarketData.load(root, strategy.underlying, until=end)
    result = run(strategy, market, start=start, end=end)
    built = report.build(result, market.closes())
    print(report.to_text(built))
    if args.json:
        args.json.write_text(json.dumps(built.as_dict(), indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Write `scripts/fno_atlas.py`**

```python
"""Measure the options atlas on the training years, NIFTY and BANKNIFTY.

    python scripts/fno_atlas.py                    # every atlas strategy
    python scripts/fno_atlas.py --only iron-condor

Writes data/fno/atlas/results.json (every report in full) and prints a
markdown table, one row per strategy, for docs/research/. The exam year is
cut out at load (fno.market) and never read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import atlas, report, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402
from research.portfolio import luck_label  # noqa: E402

HEADER = ("| strategy | index | avg month | months up | worst day | worst fall | trades/month "
          "| years beating index | luck test | Rs 5 lakh became |\n|---|---|---:|---:|---:|---:|---:|---:|---|---:|")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(atlas.ATLAS))
    args = parser.parse_args(argv)
    root = store.root_dir()
    names = [args.only] if args.only else list(atlas.ATLAS)
    reports, lines = [], [HEADER]
    for underlying in atlas.UNDERLYINGS:
        start, end = training_window(stored_sessions(root, underlying))
        print(f"{underlying}: training {start} -> {end}", flush=True)
        market = MarketData.load(root, underlying, until=end)
        for name in names:
            result = run(parse(atlas.document(name, underlying)), market, start=start, end=end)
            built = report.build(result, market.closes())
            reports.append(built.as_dict())
            h = built.headline
            lines.append(f"| {name} | {underlying} | {h['avg_month_pct']:+.2f}% | {h['months_positive_pct']:.0f}% "
                         f"| {h['worst_day_pct']:+.2f}% | {h['worst_fall_pct']:.1f}% | {h['trades_per_month']:.1f} "
                         f"| {h['years_beating_holding_pct']:.0f}% | {luck_label(h['edge_t'])} "
                         f"| Rs {h['end_value']:,.0f} |")
            print(lines[-1], flush=True)
    path = root / "atlas" / "results.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 7: Full suite** — `.venv/Scripts/python.exe -m pytest tests/ -q` → all pass.

- [ ] **Step 8: Commit** — `git add fno/atlas.py tests/test_fno_atlas.py scripts/fno_backtest.py scripts/fno_atlas.py && git commit -m "feat(fno): the options atlas, and scripts to backtest a strategy or the atlas"`

---

### Task 10: Finish the data the engine reads (after the backfill)

Waits for `scripts/fno_daily.py` to reach today (`tail` its log; the last line reads `done`). Nothing here is new code; it is the unfinished end of steps 1 and 2a, in their own words.

- [ ] **Step 1:** Step-1 plan (`docs/superpowers/plans/2026-10-03-fno-step1-daily-data.md`) Task 12 Steps 2–7 — check the store's shape, look at the quality report, run `scripts/fno_lot_sizes.py`, confirm every NIFTY / BANKNIFTY lot change against a public source, prove the table loads, commit `fno/lot_sizes.yaml`. Then its Task 13 (document it).
- [ ] **Step 2:** Step-2a plan (`docs/superpowers/plans/2026-10-04-fno-step2a-market-facts.md`) Task 8 Steps 1–5 — `scripts/fno_features.py`, run it, check ATM IV against India VIX (correlation above 0.85) and the other sanity numbers, document, commit.
- [ ] **Step 3: Check expiry settlement on real data** — for five NIFTY expiries (one per year 2017–2025, mixing monthly and weekly):

```bash
.venv/Scripts/python.exe -c "
from datetime import date
from fno.market import MarketData
from fno import store
m = MarketData.load(store.root_dir(), 'NIFTY')
for e in [date(2017,6,29), date(2019,3,28), date(2021,8,26), date(2023,5,11), date(2025,2,27)]:
    rows = m.rows(e)
    print(e, 'future/close:', m.final_settlement(e, rows), 'yahoo close:', m.spot_close(e))
"
```

Expected: the two numbers within a few points every time (NSE settles index derivatives at the index close). A weekly with no Yahoo close must raise, not guess. If any date is not an expiry, pick the nearest that is — the command prints whatever it finds.

---

### Task 11: One golden backtest, frozen

**Files:** Create `tests/fixtures/fno_golden/` (three Parquet files), `tests/test_fno_golden.py`

- [ ] **Step 1: Cut the fixture** — NIFTY, the weekly short straddle (atlas, target 50 / stop 100), February–March 2023:

```bash
.venv/Scripts/python.exe -c "
from datetime import date
from pathlib import Path
import pandas as pd
from fno import store
from fno.market import features_path
root, out = store.root_dir(), Path('tests/fixtures/fno_golden')
out.mkdir(parents=True, exist_ok=True)
start, end = date(2023, 2, 1), date(2023, 3, 31)
d = store.read_daily(root, 'NIFTY', start, end)
d = d[(d['expiry'] <= date(2023, 4, 13)) & ((d['instrument'] == 'FUT') | d['strike'].between(16000, 19000))]
d.to_parquet(out / 'daily.parquet', index=False)
f = pd.read_parquet(features_path(root, 'NIFTY'))
f[(f['trade_date'] >= start) & (f['trade_date'] <= end)].to_parquet(out / 'facts.parquet', index=False)
s = pd.read_parquet(root / 'spot' / 'NIFTY.parquet')
s[(s['trade_date'] >= date(2022, 12, 1)) & (s['trade_date'] <= end)].to_parquet(out / 'spot.parquet', index=False)
print(len(d), 'rows', sum(p.stat().st_size for p in out.iterdir()) // 1024, 'KB')
"
```

Expected: a few tens of thousands of rows, under ~1.5 MB. If larger, narrow the strike band to 16,500–18,500.

- [ ] **Step 2: Run it and print every trade**

```bash
.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'tests')
from test_fno_golden import golden
r = golden()
for t in r.trades:
    print(t.decided, t.entered, t.exited, t.exit_reason, t.expiry, t.sets, [(l.type, l.strike, l.entry_price, l.exit_price) for l in t.legs], round(t.charges, 2), round(t.pnl, 2))
print('end', round(float(r.equity.iloc[-1]), 2))
"
```

(Write the test file of Step 3 first, with the `golden()` helper and the frozen numbers left out, then run this.)

- [ ] **Step 3: Work the first trade by hand, then freeze** — `tests/test_fno_golden.py`:

```python
"""One real backtest, worked by hand and frozen (design §13): NIFTY weekly short straddle,
Feb-Mar 2023, from the real NSE rows in tests/fixtures/fno_golden/.

The first trade, by hand (fill in from Step 2 and the fixture rows - every number below
must be checked against `daily.parquet`, not copied from the engine's output):
  decided <date> at the close: nearest weekly <expiry>; forward <F> -> ATM <K>.
  filled <date> at the open: CE <open> - <half-spread> = <price>; PE likewise.
  lot 50 (lot_sizes.yaml, 2023); sets <n> = floor(500,000 / margin <m>).
  exit <reason> on <date>: ...
  P&L = (...) x units - charges <c> = <pnl>.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import atlas  # noqa: E402
from fno.contracts import LotSizes  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.events import Events  # noqa: E402
from fno.market import MarketData  # noqa: E402
from fno.strategy import parse  # noqa: E402

HERE = Path(__file__).resolve().parent / "fixtures" / "fno_golden"


def golden():
    market = MarketData.from_frames(
        "NIFTY", daily=pd.read_parquet(HERE / "daily.parquet"),
        facts=pd.read_parquet(HERE / "facts.parquet"), spot=pd.read_parquet(HERE / "spot.parquet"),
        lots=LotSizes({"NIFTY": ((date(2016, 1, 1), 50),)}), events=Events(()))
    strategy = parse(atlas.document("short-straddle", "NIFTY"))
    return run(strategy, market, start=date(2023, 2, 1), end=date(2023, 3, 31))


def test_the_golden_run_is_unchanged():
    r = golden()
    assert [(t.decided, t.entered, t.exited, t.exit_reason) for t in r.trades] == FROZEN_TRADES
    assert [round(t.pnl, 2) for t in r.trades] == pytest.approx(FROZEN_PNL, abs=0.01)
    assert round(float(r.equity.iloc[-1]), 2) == pytest.approx(FROZEN_END, abs=0.01)
```

with `FROZEN_TRADES`, `FROZEN_PNL`, `FROZEN_END` set from Step 2's output **after** the hand check of the first trade agrees to the paisa (fill = open ∓ half-spread from `daily.parquet`; settlement from the expiring future or the index close in `spot.parquet`; charges from `FnoCharges` at the fill date). If the hand check disagrees, the engine is wrong: fix it (and add a synthetic test for what was wrong) before freezing. Replace the docstring's angle-bracket slots with the worked numbers.

- [ ] **Step 4: Run** — `.venv/Scripts/python.exe -m pytest tests/test_fno_golden.py -q` → passes.

- [ ] **Step 5: Commit** — `git add tests/fixtures/fno_golden tests/test_fno_golden.py && git commit -m "test(fno): a golden NIFTY short-straddle backtest, worked by hand and frozen"`

---

### Task 12: Measure the atlas and write it down

- [ ] **Step 1: Sanity-check the margin on one real day** — a NIFTY ATM short straddle on a 2023 day should block roughly ₹1.2–2.5 lakh per lot of 50 (brokers' figures for that year); a 20-delta iron condor well under ₹1 lakh:

```bash
.venv/Scripts/python.exe -c "
from datetime import date
from fno import atlas, store
from fno.engine import run
from fno.market import MarketData
from fno.strategy import parse
m = MarketData.load(store.root_dir(), 'NIFTY', until=date(2023, 3, 31))
for name in ('short-straddle', 'iron-condor'):
    r = run(parse(atlas.document(name, 'NIFTY')), m, start=date(2023, 3, 1), end=date(2023, 3, 31))
    print(name, [(t.decided, t.lot_size, round(t.margin_per_set)) for t in r.trades])
"
```

Outside the band → revisit `fno/margin.py` constants before measuring anything.

- [ ] **Step 2: Run the atlas** — `.venv/Scripts/python.exe scripts/fno_atlas.py` (both underlyings, all eight). Expected: sixteen table rows and `written: data/fno/atlas/results.json`.

- [ ] **Step 3: Check the results against what is known** — short-option strategies should show many small winning months and their worst days around 12 / 23 Mar 2020 and 4 Jun 2024 (where in range); the long event straddle should lose most of the time. A short straddle with no losing day in March 2020 means the engine is missing something — investigate before writing anything down.

- [ ] **Step 4: Write `docs/research/2026-10-05-fno-atlas.md`** — the table from Step 2; for each strategy one or two plain-language lines on what it earned, its worst day against the 10% rule, and which market conditions it made and lost money in (from `results.json` splits); the training window; and the notes every report carries (closing prices only, uncalibrated margin, lot-size source).

- [ ] **Step 5: Document** — append to `docs/DATA.md` section 9 an "Options backtests" paragraph: `scripts/fno_backtest.py` and `scripts/fno_atlas.py`, where results go, the daily-timing rule, the margin model, and that the exam year is cut out at load.

- [ ] **Step 6: Full suite and commit** — `.venv/Scripts/python.exe -m pytest tests/ -q` (all pass), then `git add docs/research/2026-10-05-fno-atlas.md docs/DATA.md && git commit -m "docs(fno): the options atlas on the training years, and how to run a backtest"`
