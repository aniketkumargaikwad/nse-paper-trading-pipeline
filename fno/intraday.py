"""The intraday options backtester (design 2026-10-19): minute by minute, out by the exit time.

ONE SESSION, on the index's own minute clock:
  1. Fill at this minute's open what was decided on the last minute - an entry,
     or the strategy's own exit condition.
  2. A position still open at the exit time closes at this minute's opens.
  3. Otherwise stop, then target, against this minute's prices, worst case
     first: sold legs at their high, bought legs at their low. A minute that
     OPENS past the stop pays the opens (the gap); otherwise the exit is at the
     stop level, each leg priced between its open and its worst in the
     proportion that puts the position exactly there. The target the same way
     with the best prices. A minute that touches both is a stop.
  4. When the minute has finished, and another minute comes before the exit
     time: the strategy's exit condition; or - flat, inside the entry window,
     under the day's entry limit - the entry condition: expiry and strikes from
     the chain at this minute, sized by margin, filled at the next open.

POINT IN TIME (design §2): the previous session's daily facts, never the day's
own; India VIX and the index from their minutes; calendar facts for the day.

NO PHANTOM FILLS. A leg that has not traded in the LOOKBACK_MINUTES up to the
decision, or has no candle in the fill minute, skips the entry with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd

from expr_eval import evaluate
from fno import account, fills, margin
from fno.costs import Fill, FnoCharges
from fno.engine import (CAPITAL, LIMIT_NOTE, NO_CAPITAL, NO_EXPIRY, NO_STRIKE, NO_TRADE,
                        BacktestResult, Skip, Trade, TradeLeg, _within_limits)
from fno.intraday_market import IntradayMarket
from fno.rates import risk_free
from fno.strategy import FACT_NAMES, OptionsStrategy, StrategyError
from fno.strikes import NoStrike, expiries_of, pick_strikes

NO_MINUTES = "no minute data"
LOOKBACK_MINUTES = 5
OPENING_RANGE = (time(9, 15), time(9, 45))
ONE_MINUTE = pd.Timedelta(minutes=1)

NOTES = (
    "Minute data: decisions on a finished minute, fills at the next minute's open. Stops and "
    "targets are checked on every minute's high and low, worst case first, a gap at the open paid.",
    "Decisions see the previous session's daily facts, and live India VIX and index minutes.",
    "Margin is a SPAN-style estimate, not yet calibrated against a broker's calculator.",
    "Real option minutes begin in Oct 2024 (Upstox): about 12 months of training, the minimum "
    "the owner set for intraday strategies on 19 Oct 2026.",
)


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def _candle(bars: pd.DataFrame, t: pd.Timestamp) -> pd.Series | None:
    try:
        row = bars.loc[t]
    except KeyError:
        return None
    return row if isinstance(row, pd.Series) else row.iloc[-1]


def _contracts(bars: pd.DataFrame, upto: pd.Timestamp, lot: int) -> int:
    """Contracts traded in the LOOKBACK_MINUTES minutes ending with `upto`."""
    start = upto - (LOOKBACK_MINUTES - 1) * ONE_MINUTE
    return int(bars.loc[start:upto, "volume"].sum()) // lot


@dataclass(frozen=True)
class _Plan:
    expiry: date
    strikes: tuple[float, ...]
    sets: int
    lot: int
    margin_per_set: float
    equity: float
    facts: dict[str, Any]


@dataclass
class _Held:
    trade: Trade
    bars: list[pd.DataFrame]
    last: list[float]

    def weights(self) -> list[int]:
        return [leg.sign * leg.units for leg in self.trade.legs]

    def types(self) -> list[str]:
        return [leg.type for leg in self.trade.legs]

    def strikes(self) -> list[float]:
        return [leg.strike for leg in self.trade.legs]

    def minute(self, t: pd.Timestamp) -> tuple[list[float], list[float], list[float], list[float], list[int]]:
        """Each leg's open, high, low, close and volume in minute t; no candle repeats the last price."""
        o, h, l, c, v = [], [], [], [], []
        for i, bars in enumerate(self.bars):
            row = _candle(bars, t)
            if row is None:
                p = self.last[i]
                o.append(p), h.append(p), l.append(p), c.append(p), v.append(0)
            else:
                o.append(float(row["open"])), h.append(float(row["high"])), l.append(float(row["low"]))
                c.append(float(row["close"])), v.append(int(row["volume"]))
        return o, h, l, c, v

    def held_to_limits(self, prices: list[float], volumes: list[int]) -> list[float]:
        moved, _ = _within_limits(self.weights(), self.types(), self.strikes(), prices, volumes)
        return moved

    def pnl(self, prices: list[float]) -> float:
        return self.trade.premium + sum(w * p for w, p in zip(self.weights(), prices))


def _between(a: list[float], b: list[float], frac: float) -> list[float]:
    return [x + frac * (y - x) for x, y in zip(a, b)]


def _variables(market: IntradayMarket, day: date, prev: date, expiry: date, bars: pd.DataFrame,
               stev: dict[date, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    facts = market.daily.facts
    row = facts[facts["trade_date"] == prev]
    if len(row):
        r = row.iloc[0]
        for name in FACT_NAMES:
            value = r.get(name)
            if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
                out[name] = float(value)
    out["event_today"] = bool(market.daily.events.on(day))
    out["weekday"] = float(day.weekday())
    out["dte"] = out["expiry_dte"] = float((expiry - day).days)
    out["sessions_to_event"] = float(stev.get(day, np.nan))
    vix = market.vix_on(bars.index)
    out["vix"] = vix
    prev_vix = float(row.iloc[0]["vix"]) if len(row) and "vix" in row.columns else np.nan
    out["vix_chg_pct"] = 100 * (vix / prev_vix - 1)
    today = np.array([ts.date() == day for ts in bars.index], dtype=bool)
    clock = np.array([ts.time() for ts in bars.index])
    first = bars.loc[today]
    out["day_open"] = float(first["open"].iloc[0]) if len(first) else np.nan
    in_range = today & (clock >= OPENING_RANGE[0]) & (clock < OPENING_RANGE[1])
    after = today & (clock >= OPENING_RANGE[1])
    hi = float(bars.loc[in_range, "high"].max()) if in_range.any() else np.nan
    lo = float(bars.loc[in_range, "low"].min()) if in_range.any() else np.nan
    out["or_high"] = pd.Series(np.where(after, hi, np.nan), index=bars.index)
    out["or_low"] = pd.Series(np.where(after, lo, np.nan), index=bars.index)
    return out


def _signal(node: Any, bars: pd.DataFrame, variables: dict[str, Any],
            minutes: list[pd.Timestamp]) -> dict[int, bool]:
    if node is None:
        return {t.value: True for t in minutes}
    out = evaluate(node, bars, variables).reindex(pd.DatetimeIndex(minutes))
    return {t.value: (bool(v) if v == v else False) for t, v in out.items()}


def _plan(strategy: OptionsStrategy, market: IntradayMarket, day: date, prev: date, t: pd.Timestamp,
          expiry: date, rate: float, lot: int, equity: float, variables: dict[str, Any]) -> _Plan | Skip:
    snap = market.chain(day, expiry, t, rate=rate, lot=lot)
    try:
        strikes = pick_strikes(snap, strategy.legs)
    except NoStrike as exc:
        return Skip(day, NO_STRIKE, f"{t:%H:%M} {exc}")
    book = market.day_options(expiry, day)
    for leg, k in zip(strategy.legs, strikes):
        if _contracts(book[(k, leg.type)], t, lot) <= 0:
            return Skip(day, NO_TRADE, f"{leg.type} {k:g} expiring {expiry} has not traded in the "
                                       f"{LOOKBACK_MINUTES} minutes to {t:%H:%M}")
    known = snap.rows["iv"].dropna()
    fallback = float(np.median(known)) if len(known) else 0.2
    legs = []
    for leg, k in zip(strategy.legs, strikes):
        row = snap.rows[(snap.rows["option_type"] == leg.type) & (snap.rows["strike"] == k)].iloc[0]
        iv = float(row["iv"]) if np.isfinite(row["iv"]) else fallback
        legs.append(margin.MarginLeg(leg.sign, leg.type == "CE", k, leg.lots * lot, iv, float(row["settle"])))
    vix_now = float(variables["vix"].get(t, np.nan))
    per_set = margin.estimate(legs, forward=snap.forward, years=snap.dte / 365, rate=rate, vix=vix_now,
                              fill_day=day, expiry=expiry)
    sets = account.sets_affordable(equity, per_set, strategy.max_margin_pct)
    if sets == 0:
        return Skip(day, NO_CAPITAL, f"{t:%H:%M} one set needs Rs {per_set:,.0f} of margin; Rs {equity:,.0f} "
                                     f"at {strategy.max_margin_pct:g}% allows none")
    facts = {"vix": vix_now, "iv_rank": variables.get("iv_rank", np.nan),
             "event_today": variables["event_today"], "dte": (expiry - day).days,
             "trend": market.daily.trend(prev), "decided_time": f"{t:%H:%M}"}
    return _Plan(expiry, tuple(strikes), sets, lot, per_set, equity, facts)


def _open(strategy: OptionsStrategy, plan: _Plan, market: IntradayMarket, day: date, t: pd.Timestamp,
          charges: FnoCharges) -> _Held | Skip:
    book = market.day_options(plan.expiry, day)
    bars = [book[(k, leg.type)] for leg, k in zip(strategy.legs, plan.strikes)]
    candles = [_candle(b, t) for b in bars]
    for leg, k, c in zip(strategy.legs, plan.strikes, candles):
        if c is None:
            return Skip(day, NO_TRADE, f"{leg.type} {k:g} has no candle at {t:%H:%M}: entry skipped")
    units = [leg.lots * plan.lot * plan.sets for leg in strategy.legs]
    opens, moved = _within_limits([leg.sign * u for leg, u in zip(strategy.legs, units)],
                                  [leg.type for leg in strategy.legs], list(plan.strikes),
                                  [float(c["open"]) for c in candles], [int(c["volume"]) for c in candles])
    legs, premium, fees = [], 0.0, 0.0
    for i, (leg, k) in enumerate(zip(strategy.legs, plan.strikes)):
        price = fills.fill_price(opens[i], leg.side, _contracts(bars[i], t - ONE_MINUTE, plan.lot))
        fee = charges.fill(Fill(day, "OPT", leg.side, price, units[i]))
        premium -= leg.sign * price * units[i]
        fees += fee
        legs.append(TradeLeg(leg.type, leg.side, k, units[i], price, price,
                             note=LIMIT_NOTE if i == moved else ""))
    trade = Trade(market.underlying, plan.expiry, day, day, plan.sets, plan.lot, plan.margin_per_set,
                  plan.equity, {**plan.facts, "entry_time": f"{t:%H:%M}"}, legs, premium, premium - fees, fees)
    return _Held(trade, bars, [float(c["close"]) for c in candles])


def _exit(held: _Held, raw: list[float], t: pd.Timestamp, reason: str, charges: FnoCharges, day: date) -> float:
    trade = held.trade
    flow = 0.0
    for i, leg in enumerate(trade.legs):
        side = "sell" if leg.side == "buy" else "buy"
        price = fills.fill_price(raw[i], side, _contracts(held.bars[i], t - ONE_MINUTE, trade.lot_size))
        fee = charges.fill(Fill(day, "OPT", side, price, leg.units))
        flow += leg.sign * price * leg.units - fee
        trade.charges += fee
        leg.exit_price = leg.mark = price
    trade.cash += flow
    trade.exited, trade.exit_reason, trade.pnl = day, reason, trade.cash
    trade.facts["exit_time"] = f"{t:%H:%M}"
    return flow


def _opens(held: _Held, t: pd.Timestamp) -> list[float]:
    o, _, _, _, v = held.minute(t)
    return held.held_to_limits(o, v)


def _breach(strategy: OptionsStrategy, held: _Held, t: pd.Timestamp) -> tuple[str, list[float]] | None:
    o, h, l, c, v = held.minute(t)
    legs = held.trade.legs
    o = held.held_to_limits(o, v)
    worst = held.held_to_limits([hi if leg.sign < 0 else lo for leg, hi, lo in zip(legs, h, l)], v)
    best = held.held_to_limits([lo if leg.sign < 0 else hi for leg, hi, lo in zip(legs, h, l)], v)
    held.last = c
    basis = abs(held.trade.premium)
    if basis <= 0:
        return None
    at_open, at_worst, at_best = held.pnl(o), held.pnl(worst), held.pnl(best)
    ex = strategy.exit
    if ex.stop_pct is not None:
        level = -ex.stop_pct / 100 * basis
        if at_open <= level:
            return "stop", o
        if at_worst <= level:
            return "stop", _between(o, worst, (level - at_open) / (at_worst - at_open))
    if ex.target_pct is not None:
        level = ex.target_pct / 100 * basis
        if at_open >= level:
            return "target", o
        if at_best >= level:
            return "target", _between(o, best, (level - at_open) / (at_best - at_open))
    return None


def _session(strategy: OptionsStrategy, market: IntradayMarket, day: date, cash: float, charges: FnoCharges,
             trades: list[Trade], skips: list[Skip], stev: dict[date, float]) -> float:
    prev = market.previous_session(day)
    if prev is None:
        return cash
    prev_rows = market.daily.rows(prev)
    listed = expiries_of(prev_rows, prev, strategy.cycle)
    if strategy.which >= len(listed):
        skips.append(Skip(day, NO_EXPIRY, f"no {strategy.cycle} expiry number {strategy.which} listed on {prev}"))
        return cash
    expiry = listed[strategy.which]
    if not market.day_options(expiry, day):
        skips.append(Skip(day, NO_MINUTES, f"no option minutes for the {expiry} expiry on {day}"))
        return cash
    bars = market.index_bars(day)
    minutes = [ts for ts in bars.index if ts.date() == day]
    if not minutes:
        skips.append(Skip(day, NO_MINUTES, f"no index minutes on {day}"))
        return cash
    variables = _variables(market, day, prev, expiry, bars, stev)
    enter_on = _signal(strategy.entry_when, bars, variables, minutes)
    exit_on = _signal(strategy.exit.when, bars, variables, minutes) if strategy.exit.when is not None else {}
    lot = market.daily.lot_size(prev, prev_rows[prev_rows["expiry"] == expiry])
    rate = risk_free(day)
    window = (_clock(strategy.window[0]), _clock(strategy.window[1]))
    exit_at = _clock(strategy.exit.time)

    held: _Held | None = None
    pending: tuple[str, Any] | None = None
    entries = 0
    for i, t in enumerate(minutes):
        if pending is not None:
            kind, payload = pending
            pending = None
            if kind == "enter":
                opened = _open(strategy, payload, market, day, t, charges)
                if isinstance(opened, Skip):
                    skips.append(opened)
                else:
                    held = opened
                    cash += held.trade.cash
                    entries += 1
            elif held is not None:
                cash += _exit(held, _opens(held, t), t, payload, charges, day)
                trades.append(held.trade)
                held = None
        if held is not None and t.time() >= exit_at:
            cash += _exit(held, _opens(held, t), t, "exit time", charges, day)
            trades.append(held.trade)
            held = None
        if held is not None:
            hit = _breach(strategy, held, t)
            if hit is not None:
                cash += _exit(held, hit[1], t, hit[0], charges, day)
                trades.append(held.trade)
                held = None
        if i + 1 == len(minutes) or minutes[i + 1].time() >= exit_at:
            continue
        if held is not None:
            if exit_on.get(t.value, False):
                pending = ("exit", "exit condition")
        elif (window[0] <= t.time() < window[1] and entries < strategy.max_per_day
              and enter_on.get(t.value, False)):
            plan = _plan(strategy, market, day, prev, t, expiry, rate, lot, cash, variables)
            if isinstance(plan, Skip):
                skips.append(plan)
            else:
                pending = ("enter", plan)
    if held is not None:
        cash += _exit(held, held.last, minutes[-1], "end of day", charges, day)
        trades.append(held.trade)
    return cash


def run(strategy: OptionsStrategy, market: IntradayMarket, *, start: date, end: date,
        capital: float = CAPITAL, charges: FnoCharges | None = None) -> BacktestResult:
    if strategy.mode != "intraday":
        raise StrategyError(f"{strategy.name}: positional strategies run on fno.engine")
    if strategy.underlying != market.underlying:
        raise ValueError(f"{strategy.name} trades {strategy.underlying}; the data is {market.underlying}")
    charges = charges or FnoCharges.load()
    stev = market.daily.sessions_to_event()
    cash = float(capital)
    trades: list[Trade] = []
    skips: list[Skip] = []
    equity: dict[date, float] = {}
    for day in market.sessions(start, end):
        cash = _session(strategy, market, day, cash, charges, trades, skips, stev)
        equity[day] = cash
    return BacktestResult(strategy.name, strategy.underlying, start, end, float(capital), trades, skips,
                          pd.Series(equity, dtype=float), NOTES)
