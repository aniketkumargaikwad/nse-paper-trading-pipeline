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

ONE MOMENT FOR ALL LEGS. Each leg's opening price is its own first trade, and
on a gap day a thin strike's first trade can come long after (or before) the
others': 23 Sep 2019 printed a 150-point call spread at 226. Legs filled
together must make a price the position could actually have, so when they
break its no-arbitrage limits (the least and most it can pay at expiry) the
least-traded leg is moved to the limit, and the leg says so.

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

LIMIT_NOTE = "opening prints broke the position's no-arbitrage limit: moved to it"
MOVED_EXPIRY_DAYS = 3      # weekly expiries are 7 days apart, a holiday move 1-2

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
    note: str = ""

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


def _payoff_limits(legs: list[tuple[int, str, float]]) -> tuple[float, float]:
    """The least and most a position (weight, type, strike per leg) can be worth at
    expiry: today's value must lie between them. Unbounded sides are infinite."""
    def payoff(level: float) -> float:
        return sum(w * (max(level - k, 0.0) if typ == "CE" else max(k - level, 0.0))
                   for w, typ, k in legs)

    points = [payoff(level) for level in (0.0, *sorted({k for _, _, k in legs}))]
    slope_above = sum(w for w, typ, _ in legs if typ == "CE")       # beyond the top strike
    low = min(points) if slope_above >= 0 else -np.inf
    high = max(points) if slope_above <= 0 else np.inf
    return low, high


def _within_limits(weights: list[int], types: list[str], strikes: list[float],
                   prices: list[float], volumes: list[int]) -> tuple[list[float], int | None]:
    """Prices moved, if they must, so the legs together respect the position's limits;
    returns the prices and which leg moved (None if none did)."""
    low, high = _payoff_limits(list(zip(weights, types, strikes)))
    value = sum(w * p for w, p in zip(weights, prices))
    gap = low - value if value < low else high - value if value > high else 0.0
    if gap == 0.0 or len(prices) < 2:
        return prices, None
    j = min(range(len(prices)), key=lambda i: volumes[i])
    moved = list(prices)
    moved[j] = max(prices[j] + gap / weights[j], 0.0)
    return moved, j


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
    # The traded expiry's own lot: while a revision rolls in, expiries differ.
    lot = market.lot_size(day, rows[rows["expiry"] == expiry])
    known_iv = snap.rows["iv"].dropna()
    fallback_iv = float(np.median(known_iv)) if len(known_iv) else 0.2
    legs = []
    for leg, strike in zip(strategy.legs, strikes):
        row = _contract(snap.rows, expiry, leg.type, strike)
        iv = float(row["iv"]) if np.isfinite(row["iv"]) else fallback_iv
        legs.append(margin.MarginLeg(leg.sign, leg.type == "CE", strike, leg.lots * lot, iv,
                                     float(row["settle"])))
    vix = float(market.fact(day, "vix"))
    per_set = margin.estimate(legs, forward=snap.forward, years=snap.dte / 365, rate=snap.rate, vix=vix,
                              fill_day=market.next_session(day), expiry=expiry)
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
    found = []
    for leg, strike in zip(strategy.legs, plan.strikes):
        row = _contract(rows, plan.expiry, leg.type, strike)
        if not _traded(row):
            return Skip(day, NO_TRADE, f"{leg.type} {strike:g} expiring {plan.expiry} "
                                       f"did not trade on {day}")
        found.append(row)
    units = [leg.lots * plan.lot_size * plan.sets for leg in strategy.legs]
    volumes = [int(row["volume_contracts"]) for row in found]
    opens, moved = _within_limits([leg.sign * u for leg, u in zip(strategy.legs, units)],
                                  [leg.type for leg in strategy.legs], list(plan.strikes),
                                  [float(row["open"]) for row in found], volumes)
    legs, premium, fees = [], 0.0, 0.0
    for i, (leg, strike) in enumerate(zip(strategy.legs, plan.strikes)):
        price = fills.fill_price(opens[i], leg.side, volumes[i])
        fee = charges.fill(Fill(day, "OPT", leg.side, price, units[i]))
        premium -= leg.sign * price * units[i]
        fees += fee
        note = LIMIT_NOTE if i == moved else ""
        legs.append(TradeLeg(leg.type, leg.side, strike, units[i], price, price, note=note))
    return Trade(underlying, plan.expiry, plan.decided, day, plan.sets, plan.lot_size,
                 plan.margin_per_set, plan.equity, plan.facts, legs, premium, premium - fees, fees)


def _follow_moved_expiry(trade: Trade, rows: pd.DataFrame) -> None:
    """When an expiry falls on a holiday NSE moves it, and from some day on its file
    lists the same contracts under the new date (NIFTY 29 Jun 2023 became 28 Jun from
    28 Jun; ten such moves on NIFTY and BANKNIFTY since 2016). A held position follows:
    if its expiry is no longer listed but one expiry within MOVED_EXPIRY_DAYS is, and it
    lists the position's contracts, that is the same expiry under its new date."""
    options = rows[rows["instrument"] == "OPT"]
    listed = set(options["expiry"])
    if not listed or trade.expiry in listed:
        return
    near = [e for e in listed if 0 < abs((e - trade.expiry).days) <= MOVED_EXPIRY_DAYS]
    if len(near) != 1 or any(_contract(rows, near[0], leg.type, leg.strike) is None for leg in trade.legs):
        return
    trade.note = f"expiry moved by NSE from {trade.expiry} to {near[0]}"
    trade.expiry = near[0]


def _mark(trade: Trade, rows: pd.DataFrame) -> None:
    """The day's settlement prices - each a closing price, and a thin strike's can be
    hours old - held to the position's no-arbitrage limits like a fill."""
    volumes = []
    for leg in trade.legs:
        row = _contract(rows, trade.expiry, leg.type, leg.strike)
        if row is not None and row["settle"] > 0:
            leg.mark = float(row["settle"])
        volumes.append(int(row["volume_contracts"]) if row is not None else 0)
    marks, _ = _within_limits([leg.sign * leg.units for leg in trade.legs],
                              [leg.type for leg in trade.legs], [leg.strike for leg in trade.legs],
                              [leg.mark for leg in trade.legs], volumes)
    for leg, mark in zip(trade.legs, marks):
        leg.mark = mark


LOSS_CAP = "loss cap"


def stop_level(ex: Any, basis: float, equity_at_entry: float) -> tuple[float, str] | None:
    """The P&L (rupees, before charges) at which the position is closed, and why.

    The tighter of the stop (a share of the net premium) and the loss cap (a
    share of the account at entry). None if neither applies.
    """
    levels = []
    if ex.stop_pct is not None and basis > 0:
        levels.append((-ex.stop_pct / 100 * basis, "stop"))
    if ex.max_loss_pct is not None and equity_at_entry > 0:
        levels.append((-ex.max_loss_pct / 100 * equity_at_entry, LOSS_CAP))
    return max(levels, key=lambda lv: lv[0]) if levels else None


def _exit_reason(strategy: OptionsStrategy, trade: Trade, day: date, condition: bool) -> str | None:
    ex = strategy.exit
    basis = abs(trade.premium)
    pnl = trade.premium + trade.value()          # before charges, the usual convention
    stop = stop_level(ex, basis, trade.equity_at_entry)
    if stop is not None and pnl <= stop[0]:
        return stop[1]
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
    references, volumes = [], []
    for leg in trade.legs:
        row = _contract(rows, trade.expiry, leg.type, leg.strike)
        if _traded(row):
            references.append(float(row["open"]))
            volumes.append(int(row["volume_contracts"]))
        else:
            references.append(float(row["settle"]) if row is not None and row["settle"] > 0 else leg.mark)
            volumes.append(0)
            leg.note = "no trade at the open: closed at the settlement price, thin-market spread"
    references, moved = _within_limits([leg.sign * leg.units for leg in trade.legs],
                                       [leg.type for leg in trade.legs],
                                       [leg.strike for leg in trade.legs], references, volumes)
    flow = 0.0
    for i, leg in enumerate(trade.legs):
        side = "sell" if leg.side == "buy" else "buy"
        price = fills.fill_price(references[i], side, volumes[i])
        if i == moved:
            leg.note = (leg.note + "; " if leg.note else "") + LIMIT_NOTE
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
        if trade is not None:
            _follow_moved_expiry(trade, rows)
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
