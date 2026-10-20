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


def test_the_lot_is_the_traded_expiry_s_own_when_expiries_differ():
    """Dec 2024: new weeklies carried NIFTY's new lot of 75 while older contracts kept 25.
    The lot must come from the expiry traded, not from whichever lot most rows carry."""
    from datetime import date

    from fno.contracts import LotSizes
    from fno.events import Events
    from fno.market import MarketData
    from fno_synthetic import daily, spot

    rows = daily(FILL)
    rows.loc[rows["expiry"] == E1, "lot_size"] = 75.0
    rows.loc[rows["expiry"] != E1, "lot_size"] = 25.0        # E2 and the monthly: the majority
    m = MarketData.from_frames("NIFTY", daily=rows, facts=facts(), spot=spot(),
                               lots=LotSizes({"NIFTY": ((date(2016, 1, 1), 50),)}), events=Events(()))
    [t] = run(straddle(), m, start=D1, end=D4).trades
    assert t.lot_size == 75
    assert all(leg.units == t.sets * 75 for leg in t.legs)


def test_a_held_contract_follows_nse_when_a_holiday_moves_its_expiry():
    """29 Jun 2023 was a holiday: NSE relabelled every contract expiring then as 28 Jun
    from 28 Jun on. Here the 4 Jan weekly is relabelled 3 Jan from D3 - the position
    must follow it and settle on D3 at that day's index close, 21,560."""
    from datetime import date

    from fno.contracts import LotSizes
    from fno.events import Events
    from fno.market import MarketData
    from fno_synthetic import daily, spot

    rows = daily(FILL)
    moved = (rows["trade_date"] >= D3) & (rows["expiry"] == E1)
    rows.loc[moved, "expiry"] = D3
    rows.loc[moved & (rows["trade_date"] == D3), "settle"] = 0.0
    rows = rows[~((rows["trade_date"] == D4) & (rows["expiry"] == D3))]
    closes = spot().assign(close=[21500.0, 21500.0, 21560.0, 21500.0, 21500.0, 21500.0])
    m = MarketData.from_frames("NIFTY", daily=rows, facts=facts(), spot=closes,
                               lots=LotSizes({"NIFTY": ((date(2016, 1, 1), LOT),)}), events=Events(()))
    [t] = run(straddle(), m, start=D1, end=D3).trades
    assert (t.expiry, t.exited, t.exit_reason) == (D3, D3, "expiry")
    units = t.sets * LOT
    charges = CH.fill(Fill(D2, "OPT", "sell", 99.50, units)) + CH.fill(Fill(D2, "OPT", "sell", 89.55, units))
    assert t.pnl == pytest.approx((99.50 + 89.55 - 60.0) * units - charges)
    assert "moved" in t.note


def test_opening_prints_that_break_a_spread_s_limits_are_pulled_back_to_them():
    """23 Sep 2019: a 150-point bear call spread was closed at the legs' first prints,
    676 and 450 - a spread worth 226, more than its width can ever be. The least-traded
    leg is moved to the limit: here the thin sold leg to 21,700 - 21,500 = 200 above
    the other leg's 100, i.e. 300, then pays its (thin) spread."""
    spread = parse("""
version: options/1
name: test-bear-call
underlying: NIFTY
mode: positional
expiry: {cycle: weekly, which: 0}
legs:
  - {side: sell, type: CE, strike: {atm: 0}, lots: 1}
  - {side: buy, type: CE, strike: {atm: 2}, lots: 1}
exit: {stop_pct: 100}
""")
    gap = {(D2, E1, ATM, "CE"): {"open": 100.0, "settle": 260.0},
           (D2, E1, 21700.0, "CE"): {"open": 30.0, "settle": 90.0},
           (D3, E1, ATM, "CE"): {"open": 450.0, "volume_contracts": 50},
           (D3, E1, 21700.0, "CE"): {"open": 100.0}}
    [t] = run(spread, market(overrides=gap), start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "stop")
    sold, bought = t.legs
    assert sold.exit_price == pytest.approx(300.0 + 2 * 1.5)
    assert bought.exit_price == pytest.approx(100.0 - 0.5)
    assert "limit" in sold.note
    units = t.sets * LOT
    assert -t.premium + (sold.exit_price - bought.exit_price) * units <= 200 * units + 4 * units


def test_a_stale_close_cannot_mark_a_spread_beyond_its_width():
    """Marks are closing prices, and a thin strike's last trade can be hours old: a 200-point
    call spread marked at 500 against 50 would show a loss no such spread can have."""
    spread = parse("""
version: options/1
name: test-bear-call
underlying: NIFTY
mode: positional
expiry: {cycle: weekly, which: 0}
legs:
  - {side: sell, type: CE, strike: {atm: 0}, lots: 1}
  - {side: buy, type: CE, strike: {atm: 2}, lots: 1}
""")
    stale = {(D2, E1, ATM, "CE"): {"open": 100.0, "settle": 500.0, "volume_contracts": 40},
             (D2, E1, 21700.0, "CE"): {"open": 30.0, "settle": 50.0}}
    result = run(spread, market(overrides=stale), start=D1, end=D3)
    [t] = result.trades
    units = t.sets * LOT
    assert result.equity[D2] >= CAPITAL + t.premium - t.charges - 200 * units - 1e-6


def test_a_loss_cap_is_seen_at_the_close_and_filled_at_the_next_open():
    stop = {(D2, E1, ATM, "CE"): {"settle": 400.0}, (D3, E1, ATM, "CE"): {"open": 450.0}}
    [t] = run(straddle(exit_="{max_loss_pct: 2}"), market(overrides=staged(FILL, stop)),
              start=D1, end=D4).trades
    assert (t.exited, t.exit_reason) == (D3, "loss cap")
    assert t.pnl < -0.02 * CAPITAL      # the gap to the next open is paid, whatever the cap says
