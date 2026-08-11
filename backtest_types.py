"""Trade records shared by the simulator and the metrics module.

Separate from backtest.py so metrics.py can read a trade without importing the
orchestration module - which pulls in Supabase, the data provider and argparse,
none of which a pure metric function should ever need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd


@dataclass(frozen=True)
class SimTrade:
    """One completed simulated round-trip inside a backtest."""

    entry_signal_ts: datetime
    entry_fill_ts: datetime
    exit_signal_ts: datetime      # for SL/target hits: the candle that hit them
    exit_fill_ts: datetime
    position_type: str
    quantity: int
    intended_entry_price: float
    entry_price: float            # after slippage
    intended_exit_price: float
    exit_price: float             # after slippage
    exit_reason: str              # 'signal' | 'stop_loss' | 'target' | 'end_of_data'
    gross_pnl: float
    costs: float
    net_pnl: float


@dataclass(frozen=True)
class SkippedEntry:
    """An entry signal that could not become a trade.

    Recorded rather than dropped: a signal that never became a position is a
    real fact about the strategy, and a silently-dropped one would make the
    strategy look more selective than it is.
    """

    signal_ts: pd.Timestamp
    price: float
    reason: str          # 'notional_below_price'


@dataclass(frozen=True)
class SimResult:
    trades: list[SimTrade]
    skipped: list[SkippedEntry]
