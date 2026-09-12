"""Indian intraday equity trading costs, itemised.

WHY THIS EXISTS
---------------
The engine charged a flat Rs 30 per round trip described as "brokerage+taxes".
That is right at exactly one trade size and wrong at every other, because most
real charges scale with turnover:

    Rs 20,000 notional  -> flat Rs 30 OVERSTATES the cost
    Rs 500,000 notional -> flat Rs 30 UNDERSTATES it badly

Since notional sizing was introduced the trade value varies per symbol, so a
flat number now misstates P&L differently on every stock in a universe - which
quietly distorts the per-symbol comparison the dispersion metrics exist to
make.

WHAT IS MODELLED (NSE cash equity, INTRADAY / MIS)

    brokerage        per order, per the broker's plan
    STT              sell side only, on turnover
    exchange txn     both sides, on turnover
    SEBI             both sides, on turnover
    stamp duty       BUY side only, on turnover
    GST              on (brokerage + exchange + SEBI)

RATES CHANGE. The defaults below carry the date they were last checked. They
are a configuration input, not a law of nature - verify against your broker's
current charge list before trusting a P&L figure to the rupee. Being
approximately right and knowing it beats being precisely wrong.

Delivery (CNC) trades are NOT modelled: this platform trades intraday, and
delivery has a different STT rate and no stamp-duty distinction worth guessing
at until it is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from config import IST

# Rates as understood on 2026-08-21, expressed as fractions of turnover.
# Sources are the standard published NSE/SEBI schedules; RE-VERIFY before
# relying on exact figures.
STT_SELL = 0.00025            # 0.025% on the sell side (intraday equity)
EXCHANGE_TXN = 0.0000297      # ~0.00297% per side (NSE)
SEBI_FEES = 0.000001          # Rs 10 per crore, per side
STAMP_DUTY_BUY = 0.00003      # 0.003% on the buy side
GST_RATE = 0.18               # on brokerage + exchange + SEBI

# Dhan's intraday plan: whichever is lower, per order.
DEFAULT_BROKERAGE_PER_ORDER = 20.0
DEFAULT_BROKERAGE_PCT = 0.0003          # 0.03%

# Delivery (CNC): a position still held after the day it was opened.
# As understood on 2026-09-11; RE-VERIFY before trusting a rupee figure.
STT_DELIVERY = 0.001                 # 0.1% on BOTH legs (intraday: sell only)
STAMP_DUTY_DELIVERY_BUY = 0.00015    # 0.015% on the buy side
DEFAULT_DELIVERY_BROKERAGE_PER_ORDER = 0.0   # Dhan charges no delivery brokerage
DEFAULT_DP_CHARGE_PER_SELL = 15.0            # depository charge per sell incl. GST (approx.)


@dataclass(frozen=True)
class CostModel:
    """Charges for one completed round trip (one buy and one sell).

    Every rate is explicit so a result can state what it assumed. Two
    backtests with different cost models are not comparable, and a stored
    result that cannot say which it used cannot be trusted later.
    """

    brokerage_per_order: float = DEFAULT_BROKERAGE_PER_ORDER
    brokerage_pct: float = DEFAULT_BROKERAGE_PCT
    stt_sell: float = STT_SELL
    exchange_txn: float = EXCHANGE_TXN
    sebi_fees: float = SEBI_FEES
    stamp_duty_buy: float = STAMP_DUTY_BUY
    gst_rate: float = GST_RATE

    def brokerage(self, turnover: float) -> float:
        """Per order: the lower of a flat fee and a percentage."""
        return min(self.brokerage_per_order, turnover * self.brokerage_pct)

    def round_trip(self, entry_price: float, exit_price: float, quantity: int) -> float:
        """Total charges for a completed trade, in rupees.

        Both legs are priced on their OWN turnover rather than on one average,
        because the two differ whenever the trade made or lost money - and
        charging the loss-making leg as though it were the winning one would
        flatter every losing trade slightly.
        """
        if quantity <= 0:
            return 0.0

        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        turnover = buy_turnover + sell_turnover

        brokerage = self.brokerage(buy_turnover) + self.brokerage(sell_turnover)
        exchange = turnover * self.exchange_txn
        sebi = turnover * self.sebi_fees

        stt = sell_turnover * self.stt_sell            # sell side only
        stamp = buy_turnover * self.stamp_duty_buy     # buy side only
        gst = (brokerage + exchange + sebi) * self.gst_rate

        return round(brokerage + exchange + sebi + stt + stamp + gst, 4)

    def describe(self) -> str:
        """One line for a result row, so a stored number says what it assumed."""
        return (
            f"brokerage min(Rs{self.brokerage_per_order:g}, "
            f"{self.brokerage_pct * 100:g}%)/order, "
            f"STT {self.stt_sell * 100:g}% sell, "
            f"stamp {self.stamp_duty_buy * 100:g}% buy, "
            f"GST {self.gst_rate * 100:g}%"
        )


@dataclass(frozen=True)
class DeliveryCostModel:
    """Charges for a round trip held overnight.

    Differs from intraday in three places that matter: STT on BOTH legs at a
    much higher rate, a higher stamp duty, and a depository charge on the sale.
    Charging an overnight position intraday rates flattered every multi-day
    backtest by roughly 0.15% per trade (docs/research/2026-08-30 §4).
    """

    brokerage_per_order: float = DEFAULT_DELIVERY_BROKERAGE_PER_ORDER
    stt: float = STT_DELIVERY
    exchange_txn: float = EXCHANGE_TXN
    sebi_fees: float = SEBI_FEES
    stamp_duty_buy: float = STAMP_DUTY_DELIVERY_BUY
    gst_rate: float = GST_RATE
    dp_charge_per_sell: float = DEFAULT_DP_CHARGE_PER_SELL

    def round_trip(self, entry_price: float, exit_price: float, quantity: float) -> float:
        """Total charges for a completed overnight trade, in rupees."""
        if quantity <= 0:
            return 0.0

        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        turnover = buy_turnover + sell_turnover

        brokerage = 2 * self.brokerage_per_order
        exchange = turnover * self.exchange_txn
        sebi = turnover * self.sebi_fees
        stt = turnover * self.stt                      # both legs
        stamp = buy_turnover * self.stamp_duty_buy     # buy side only
        gst = (brokerage + exchange + sebi) * self.gst_rate

        return round(
            brokerage + exchange + sebi + stt + stamp + gst + self.dp_charge_per_sell, 4
        )

    def describe(self) -> str:
        return (
            f"delivery: STT {self.stt * 100:g}% both legs, "
            f"stamp {self.stamp_duty_buy * 100:g}% buy, "
            f"DP Rs{self.dp_charge_per_sell:g}/sell, "
            f"brokerage Rs{self.brokerage_per_order:g}/order"
        )


@dataclass(frozen=True)
class HoldingCostModel:
    """Intraday charges for a same-day trade, delivery charges otherwise.

    Decided per TRADE, not per strategy: an intraday strategy that happens to
    hold a position overnight pays delivery charges for that trade, which is
    what a broker would actually charge. The day boundary is India midnight.
    """

    intraday: CostModel = field(default_factory=CostModel)
    delivery: DeliveryCostModel = field(default_factory=DeliveryCostModel)

    # Read by backtest._make_trade: this model needs the trade's timestamps.
    prices_by_holding: ClassVar[bool] = True

    def is_delivery(self, entry_ts: datetime, exit_ts: datetime) -> bool:
        return entry_ts.astimezone(IST).date() != exit_ts.astimezone(IST).date()

    def round_trip(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        *,
        entry_ts: datetime | None = None,
        exit_ts: datetime | None = None,
    ) -> float:
        if entry_ts is None or exit_ts is None:
            raise ValueError(
                "HoldingCostModel needs entry_ts and exit_ts: without them it "
                "cannot tell a same-day trade from an overnight one."
            )
        model = self.delivery if self.is_delivery(entry_ts, exit_ts) else self.intraday
        return model.round_trip(entry_price, exit_price, quantity)

    def describe(self) -> str:
        return f"same day: {self.intraday.describe()}; overnight: {self.delivery.describe()}"


@dataclass(frozen=True)
class FlatCostModel:
    """The original flat rupee charge per round trip.

    Kept so results produced before itemised costs can still be reproduced.
    A comparison against an old result is only meaningful if the old cost
    assumption can be reconstructed exactly.
    """

    cost_per_trade_inr: float = 30.0

    def round_trip(self, entry_price: float, exit_price: float, quantity: int) -> float:
        return self.cost_per_trade_inr

    def describe(self) -> str:
        return f"flat Rs{self.cost_per_trade_inr:g} per round trip"
