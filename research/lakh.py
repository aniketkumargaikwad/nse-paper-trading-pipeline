"""What Rs 1 lakh would have become.

Trades are replayed in entry order. Each uses the whole balance at that
moment, and its fees are recomputed on that balance - so a strategy that has
doubled its money pays fees on double the turnover, exactly as it would in a
real account. Money earns nothing between trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import pandas as pd

from config import IST

from backtest_types import SimTrade
from costs import HoldingCostModel

START_VALUE = 100_000.0
MIN_LOCKED_TRADES = 10
MAX_LOCKED_DIP_PCT = 20.0


@dataclass(frozen=True)
class LakhResult:
    start_value: float
    end_value: float
    trades: int
    winning_trades: int
    worst_dip_pct: float        # 8.0 means the balance once fell 8% from a high
    cagr_pct: float | None


def compound(
    trades: Sequence[SimTrade],
    cost_model: HoldingCostModel,
    *,
    window_days: int,
    start_value: float = START_VALUE,
) -> LakhResult:
    balance = peak = start_value
    worst_dip = 0.0
    wins = 0

    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        change = balance * gross_return - fees
        if change > 0:
            wins += 1
        balance = max(balance + change, 0.0)
        peak = max(peak, balance)
        worst_dip = max(worst_dip, (peak - balance) / peak * 100 if peak > 0 else 0.0)
        if balance == 0.0:
            break

    years = window_days / 365.25
    cagr = None
    if years > 0 and balance > 0:
        cagr = round(((balance / start_value) ** (1 / years) - 1) * 100, 4)

    return LakhResult(
        start_value=start_value,
        end_value=round(balance, 2),
        trades=len(trades),
        winning_trades=wins,
        worst_dip_pct=round(worst_dip, 2),
        cagr_pct=cagr,
    )


def just_holding(
    day_candles: pd.DataFrame, cost_model: HoldingCostModel, *, start_value: float = START_VALUE
) -> float | None:
    """Buy at the first daily close, sell at the last, delivery fees once."""
    if len(day_candles) < 2:
        return None
    first = float(day_candles["close"].iloc[0])
    last = float(day_candles["close"].iloc[-1])
    units = start_value / first
    fees = cost_model.delivery.round_trip(first, last, units)
    return round(start_value + units * (last - first) - fees, 2)


def passed(locked: LakhResult) -> bool:
    """The locked-year verdict (design 5.5)."""
    return (
        locked.end_value > locked.start_value
        and locked.trades >= MIN_LOCKED_TRADES
        and locked.worst_dip_pct <= MAX_LOCKED_DIP_PCT
    )


def equity_series(
    trades: Sequence[SimTrade],
    cost_model: HoldingCostModel,
    *,
    day_index: pd.DatetimeIndex,
    closes: pd.Series | None = None,
    start_value: float = START_VALUE,
) -> list[dict[str, Any]]:
    """Daily Rs 1 lakh balance, and just-holding beside it when closes are given.

    The balance changes only when a trade closes and is carried flat between
    trades, which is what the account would really show: money earns nothing
    while it waits. The final point equals `compound(...).end_value` by
    construction - the same walk, sampled daily.

    Just-holding subtracts its one round trip of fees at every point, so the
    last point matches the headline figure. The Rs 1 lakh path instead pays
    each trade's fee as that trade closes.
    """
    if len(day_index) == 0:
        return []

    days = sorted({ts.astimezone(IST).date() for ts in day_index})

    balance_on: dict[Any, float] = {}
    balance = start_value
    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        balance = max(balance + balance * gross_return - fees, 0.0)
        balance_on[t.exit_fill_ts.astimezone(IST).date()] = balance

    hold_by_day: dict[Any, float] = {}
    if closes is not None and len(closes) >= 1:
        first_close = float(closes.iloc[0])
        last_close = float(closes.iloc[-1])
        units = start_value / first_close
        fees = cost_model.delivery.round_trip(first_close, last_close, units)
        for ts, close in closes.items():
            hold_by_day[ts.astimezone(IST).date()] = round(
                start_value + units * (float(close) - first_close) - fees, 2
            )

    out: list[dict[str, Any]] = []
    running = start_value
    for day in days:
        running = balance_on.get(day, running)
        row: dict[str, Any] = {"day": day, "lakh_balance": round(running, 2)}
        if hold_by_day:
            row["hold_balance"] = hold_by_day.get(day)
        out.append(row)
    return out
