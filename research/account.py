"""The account: a fixed number of slots, signals taken as they arrive.

WHY THIS EXISTS
---------------
The basket (research.portfolio) funds every one of 200 sleeves all the time.
That is the right statistical average, and the wrong figure for an account:
a rule that is in the market 5% of the time leaves 95% of that money idle,
so a selective strategy that would earn 5% a month on the capital actually
at work reads as 0.25% in the basket. The owner's goal is stated on the
money he has, not on 200 funded sleeves.

So the same trades are replayed the way an account would take them: SLOTS
positions at most, each Rs 1 lakh notional, an entry taken if a slot is free
at that moment and skipped otherwise. Skipping is what a real account does;
it is also what makes this a measurement rather than a wish.

WHAT IS FIXED, AND WHY
----------------------
* Capital is SLOTS x Rs 1 lakh; returns are stated on that capital, and the
  Rs 1 lakh headline is the same percentage applied to Rs 1 lakh. Fees are
  almost entirely proportional, so the percentage barely depends on scale.
* Signals are taken in time order, ties broken by symbol name. Deterministic,
  so a re-run gives the same answer; and no priority rule to fit.
* A position exiting at the same instant another wants to enter frees its
  slot first, because both are fills at the same bar's open.
* Holding is the equal-weight basket's holding, the same benchmark the
  basket uses: what doing nothing with the same universe would have made.

Pure: no I/O, no clock, no database.
"""

from __future__ import annotations

import heapq
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backtest_types import SimTrade
from research.portfolio import (
    NOTIONAL,
    Basket,
    holding_by_month,
    month_of,
    stock_sleeves,
    summarise,
)
from research.sweep import ComboResult

# How many positions the account holds at once. Ten Rs 1 lakh slots is a Rs 10
# lakh account; the percentage result applies to any size.
SLOTS = 10


@dataclass(frozen=True)
class Replay:
    taken: tuple[SimTrade, ...]
    skipped: int
    # Position-time actually used, as a share of slots x the replay's span.
    slot_use_pct: float


def replay(trades: Sequence[SimTrade], *, slots: int = SLOTS) -> Replay:
    """Take each entry if a slot is free at that moment; skip it otherwise."""
    ordered = sorted(trades, key=lambda t: (t.entry_fill_ts, t.entry_price, t.quantity))
    open_exits: list[Any] = []
    taken: list[SimTrade] = []
    skipped = 0
    held_seconds = 0.0
    for t in ordered:
        while open_exits and open_exits[0] <= t.entry_fill_ts:
            heapq.heappop(open_exits)
        if len(open_exits) >= slots:
            skipped += 1
            continue
        heapq.heappush(open_exits, t.exit_fill_ts)
        taken.append(t)
        held_seconds += (t.exit_fill_ts - t.entry_fill_ts).total_seconds()
    span = 0.0
    if ordered:
        span = (max(t.exit_fill_ts for t in ordered) - ordered[0].entry_fill_ts).total_seconds()
    use = 100 * held_seconds / (slots * span) if span > 0 else 0.0
    return Replay(tuple(taken), skipped, round(use, 2))


def build_account(
    results: Sequence[ComboResult], *, timeframe: str, slots: int = SLOTS,
    notional: float = NOTIONAL,
) -> Basket | None:
    """What an account with `slots` positions would have made on `timeframe`."""
    sleeves = stock_sleeves(results, timeframe=timeframe)
    if not sleeves:
        return None

    done = replay([t for r in sleeves for t in r.trades], slots=slots)
    holding = holding_by_month(sleeves)
    capital = slots * notional

    pnl: dict[str, float] = {}
    count: dict[str, int] = {}
    wins: dict[str, int] = {}
    for t in done.taken:
        key = month_of(t.exit_fill_ts)
        pnl[key] = pnl.get(key, 0.0) + t.net_pnl
        count[key] = count.get(key, 0) + 1
        if t.net_pnl > 0:
            wins[key] = wins.get(key, 0) + 1

    months = [
        {
            "month": month,
            "strategy_pct": round(100 * pnl.get(month, 0.0) / capital, 4),
            "holding_pct": round(hold, 4),
            "trades": count.get(month, 0),
            "wins": wins.get(month, 0),
            "stocks": stocks,
            "stocks_trading": 0,
        }
        for month, (hold, stocks) in sorted(holding.items())
    ]
    signals = len(done.taken) + done.skipped
    return summarise(
        timeframe, len(sleeves), months, notional=notional,
        stock_months=sum(m["stocks"] for m in months), trading_months=0,
        slots=slots, slot_use_pct=done.slot_use_pct,
        signals_skipped_pct=round(100 * done.skipped / signals, 2) if signals else 0.0,
    )


def account_weight(slots: int = SLOTS, notional: float = NOTIONAL):
    """How much one taken trade moves the account, as a fraction of capital."""
    capital = slots * notional

    def weight_of(t: SimTrade) -> float:
        return t.net_pnl / capital

    return weight_of
