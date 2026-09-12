"""Trade and fee-model builders shared by the research tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backtest_types import SimTrade
from costs import CostModel, DeliveryCostModel, HoldingCostModel

IST = ZoneInfo("Asia/Kolkata")


def ist(y: int, mo: int, d: int, h: int = 10, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def trade(
    *, entry: datetime, exit_: datetime, entry_price: float = 100.0,
    exit_price: float = 110.0, quantity: int = 1000, position_type: str = "long",
) -> SimTrade:
    if position_type == "long":
        gross = (exit_price - entry_price) * quantity
    else:
        gross = (entry_price - exit_price) * quantity
    return SimTrade(
        entry_signal_ts=entry, entry_fill_ts=entry, exit_signal_ts=exit_, exit_fill_ts=exit_,
        position_type=position_type, quantity=quantity,
        intended_entry_price=entry_price, entry_price=entry_price,
        intended_exit_price=exit_price, exit_price=exit_price,
        exit_reason="signal", gross_pnl=gross, costs=0.0, net_pnl=gross,
    )


def same_day(day: int, pct: float) -> SimTrade:
    """A trade on 2025-01-{day} returning `pct` percent gross."""
    return trade(entry=ist(2025, 1, day, 10), exit_=ist(2025, 1, day, 14),
                 entry_price=100.0, exit_price=100.0 * (1 + pct / 100))


# Every charge zeroed, so a test can isolate the compounding arithmetic.
FREE = HoldingCostModel(
    intraday=CostModel(brokerage_per_order=0.0, brokerage_pct=0.0, stt_sell=0.0,
                       exchange_txn=0.0, sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0),
    delivery=DeliveryCostModel(stt=0.0, exchange_txn=0.0, sebi_fees=0.0,
                               stamp_duty_buy=0.0, gst_rate=0.0, dp_charge_per_sell=0.0),
)
