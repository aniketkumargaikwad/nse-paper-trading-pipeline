"""What Rs 1 lakh would have become.

Trades are replayed in entry order. Each uses the whole balance at that
moment, and its fees are recomputed on that balance - so a strategy that has
doubled its money pays fees on double the turnover, exactly as it would in a
real account. Money earns nothing between trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

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
