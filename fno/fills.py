"""What a fill costs beyond the quoted price: half the bid-ask spread (design §4).

Starting values, kept here in one place: the larger of one tick and 0.5% of
the premium, doubled when the contract traded fewer than 100 contracts that
session. Paper trading (step 5) records the live bid and ask at every
simulated fill, and those samples replace these numbers.
"""

from __future__ import annotations

TICK = 0.05
HALF_SPREAD_PCT = 0.5
THIN_CONTRACTS = 100


def half_spread(price: float, volume_contracts: int) -> float:
    half = max(TICK, price * HALF_SPREAD_PCT / 100)
    return 2 * half if volume_contracts < THIN_CONTRACTS else half


def fill_price(price: float, side: str, volume_contracts: int) -> float:
    """The price actually paid (buy) or received (sell) per unit."""
    half = half_spread(price, volume_contracts)
    return price + half if side == "buy" else max(price - half, 0.0)
