"""The intraday options backtester (design 2026-10-19): hand-worked cases on a 2-day minute market."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.costs import Fill, FnoCharges  # noqa: E402
from fno.engine import CAPITAL, NO_TRADE  # noqa: E402
from fno.intraday import NO_MINUTES, run  # noqa: E402
from fno.strategy import StrategyError, parse  # noqa: E402
from fno_intraday_synthetic import D, E1, LOT, P, STRIKES, facts, market  # noqa: E402

CH = FnoCharges.load()
ATM = 23500.0
# Staged round numbers: in at 09:21's open, out at 15:15's open.
FILL = {(D, "09:21", ATM, "CE"): {"open": 40.0, "high": 40.0},
        (D, "09:21", ATM, "PE"): {"open": 38.0, "high": 38.0},
        (D, "15:15", ATM, "CE"): {"open": 10.0},
        (D, "15:15", ATM, "PE"): {"open": 8.0}}
PREMIUM = 39.80 + 37.81


def straddle(window=("09:20", "09:21"), exit_="", when=None, per_day=1):
    condition = f'\n  when: "{when}"' if when else ""
    return parse(f"""
version: options/1
name: test-intraday-straddle
underlying: NIFTY
mode: intraday
expiry: {{cycle: weekly, which: 0}}
entry:
  window: {{from: "{window[0]}", to: "{window[1]}"}}{condition}
legs:
  - {{side: sell, type: CE, strike: {{atm: 0}}, lots: 1}}
  - {{side: sell, type: PE, strike: {{atm: 0}}, lots: 1}}
exit: {{time: "15:15"{exit_}}}
reentry: {{max_per_day: {per_day}}}
""")


def orb():
    return parse("""
version: options/1
name: test-orb
underlying: NIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry: {window: {from: "09:15", to: "13:00"}, when: "close > or_high"}
legs: [{side: buy, type: CE, strike: {atm: 0}, lots: 1}]
exit: {time: "15:15", stop_pct: 30, target_pct: 60}
""")


def test_the_0920_straddle_worked_by_hand():
    """Decided on the finished 09:20 minute: the nearest weekly expires today (0 DTE),
    ATM 23,500. Filled at 09:21's open: CE 40 - 0.20 = 39.80, PE 38 - 0.19 = 37.81.
    Closed at 15:15's open: CE 10 + 0.05 = 10.05, PE 8 + 0.05 = 8.05."""
    r = run(straddle(), market(option_overrides=FILL), start=P, end=D)
    [t] = r.trades
    assert (t.expiry, t.facts["decided_time"], t.facts["entry_time"], t.facts["exit_time"], t.exit_reason) == (
        E1, "09:20", "09:21", "15:15", "exit time")
    assert [leg.strike for leg in t.legs] == [ATM, ATM]
    assert t.sets == int(CAPITAL // t.margin_per_set) > 0
    units = t.sets * LOT
    assert [leg.entry_price for leg in t.legs] == pytest.approx([39.80, 37.81])
    assert [leg.exit_price for leg in t.legs] == pytest.approx([10.05, 8.05])
    fees = sum(CH.fill(Fill(D, "OPT", side, price, units))
               for side, price in (("sell", 39.80), ("sell", 37.81), ("buy", 10.05), ("buy", 8.05)))
    assert t.charges == pytest.approx(fees)
    assert t.pnl == pytest.approx((39.80 + 37.81 - 10.05 - 8.05) * units - fees)
    assert r.equity[D] == pytest.approx(CAPITAL + t.pnl)


def test_a_stop_inside_a_minute_exits_at_the_stop_level():
    jump = {(D, "10:00", ATM, "CE"): {"high": 120.0}}
    [t] = run(straddle(exit_=", stop_pct: 50"), market(option_overrides={**FILL, **jump}), start=P, end=D).trades
    assert (t.exit_reason, t.facts["exit_time"]) == ("stop", "10:00")
    before_costs = (t.pnl + t.charges) / (t.sets * LOT)
    assert -0.5 * PREMIUM - 1.0 < before_costs < -0.5 * PREMIUM       # only the exit's half-spreads below it


def test_a_minute_that_opens_past_the_stop_pays_the_gap():
    gap = {(D, "10:00", ATM, "CE"): {"open": 150.0, "high": 150.0}}
    [t] = run(straddle(exit_=", stop_pct: 50"), market(option_overrides={**FILL, **gap}), start=P, end=D).trades
    assert t.exit_reason == "stop"
    assert t.legs[0].exit_price == pytest.approx(150.0 + 0.75)


def test_a_minute_that_touches_both_is_a_stop():
    both = {(D, "10:00", ATM, "CE"): {"high": 120.0, "low": 0.05}, (D, "10:00", ATM, "PE"): {"low": 0.05}}
    [t] = run(straddle(exit_=", stop_pct: 50, target_pct: 50"),
              market(option_overrides={**FILL, **both}), start=P, end=D).trades
    assert t.exit_reason == "stop"


def test_a_target_inside_a_minute_exits_at_the_target_level():
    cheap = {(D, "11:00", ATM, "CE"): {"low": 5.0}, (D, "11:00", ATM, "PE"): {"low": 5.0}}
    [t] = run(straddle(exit_=", target_pct: 50"), market(option_overrides={**FILL, **cheap}), start=P, end=D).trades
    assert (t.exit_reason, t.facts["exit_time"]) == ("target", "11:00")
    before_costs = (t.pnl + t.charges) / (t.sets * LOT)
    assert 0.5 * PREMIUM - 1.0 < before_costs < 0.5 * PREMIUM


def test_a_leg_that_has_not_traded_in_five_minutes_skips_the_entry():
    quiet = {(D, f"09:{m}", ATM, "CE"): {"volume": 0} for m in range(16, 21)}
    r = run(straddle(window=("09:20", "09:25")), market(option_overrides=quiet), start=P, end=D)
    assert [(s.day, s.kind) for s in r.skips] == [(D, NO_TRADE)]
    [t] = r.trades
    assert (t.facts["decided_time"], t.facts["entry_time"]) == ("09:21", "09:22")


def test_decisions_read_the_previous_session_s_facts_not_the_day_s_own():
    s = straddle(when="iv_rank > 60")
    assert len(run(s, market(facts_frame=facts(iv_rank=[70.0, 10.0])), start=P, end=D).trades) == 1
    assert run(s, market(facts_frame=facts(iv_rank=[10.0, 70.0])), start=P, end=D).trades == []


def test_the_opening_range_is_known_only_from_09_45():
    moves = {(D, "09:30"): {"high": 23560.0}, (D, "10:00"): {"close": 23600.0, "high": 23600.0}}
    [t] = run(orb(), market(index_overrides=moves), start=P, end=D).trades
    assert t.facts["decided_time"] == "10:00"


def test_a_decision_never_sees_a_later_price():
    later = {(D, hhmm, k, typ): {"open": 999.0, "high": 999.0, "low": 999.0, "close": 999.0}
             for hhmm in ("09:25", "09:40", "10:00") for k in STRIKES for typ in ("CE", "PE")}
    base = run(straddle(), market(option_overrides=FILL), start=P, end=D).trades[0]
    seen = run(straddle(), market(option_overrides={**FILL, **later}), start=P, end=D).trades[0]
    assert ([x.strike for x in seen.legs], seen.margin_per_set, seen.sets, seen.facts["decided_time"]) == (
        [x.strike for x in base.legs], base.margin_per_set, base.sets, base.facts["decided_time"])


def test_re_entry_up_to_the_day_s_limit():
    r = run(straddle(window=("09:20", "12:00"), exit_=", target_pct: 10", per_day=2), market(), start=P, end=D)
    assert len(r.trades) == 2 and all(t.exit_reason == "target" for t in r.trades)


def test_a_day_without_option_minutes_is_skipped_and_said():
    r = run(straddle(), market(with_options=False), start=P, end=D)
    assert r.trades == [] and [(s.day, s.kind) for s in r.skips] == [(D, NO_MINUTES)]


def test_positional_strategies_run_on_the_daily_engine():
    s = parse("""
version: options/1
name: positional
underlying: NIFTY
mode: positional
expiry: {cycle: weekly, which: 0}
legs: [{side: sell, type: CE, strike: {atm: 0}, lots: 1}]
""")
    with pytest.raises(StrategyError, match="fno.engine"):
        run(s, market(), start=P, end=D)


def test_a_loss_cap_tighter_than_the_stop_exits_at_the_cap():
    jump = {(D, "10:00", ATM, "CE"): {"high": 120.0}}
    [t] = run(straddle(exit_=", stop_pct: 500, max_loss_pct: 1"),
              market(option_overrides={**FILL, **jump}), start=P, end=D).trades
    assert (t.exit_reason, t.facts["exit_time"]) == ("loss cap", "10:00")
    units = t.sets * LOT
    before_costs = t.pnl + t.charges
    assert -0.01 * CAPITAL - units * 1.0 < before_costs < -0.01 * CAPITAL   # the exit's half-spreads below it


def test_the_stop_stays_the_reason_when_it_is_tighter_than_the_cap():
    jump = {(D, "10:00", ATM, "CE"): {"high": 120.0}}
    [t] = run(straddle(exit_=", stop_pct: 50, max_loss_pct: 50"),
              market(option_overrides={**FILL, **jump}), start=P, end=D).trades
    assert t.exit_reason == "stop"


def test_a_loss_cap_alone_is_enough():
    [t] = run(straddle(exit_=", max_loss_pct: 1"),
              market(option_overrides={**FILL, (D, "10:00", ATM, "CE"): {"high": 120.0}}), start=P, end=D).trades
    assert t.exit_reason == "loss cap"
