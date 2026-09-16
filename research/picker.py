"""Pick one TIMEFRAME from a version's TRAINING results (design 2026-09-16 2.3).

A fixed rule, not a judgement, so it cannot be tempted to cherry-pick.

THE RULE PICKS A BASKET, NOT A STOCK. The first rule chose the one stock in
~1,177 combinations with the largest lead over holding across eight years,
and the locked year then judged that stock alone - on one trade, twice out
of five runs. The best of 1,177 is nearly always a fluke, and one stock
cannot say anything about "per month" or "consistently".

So the rule now chooses the stock timeframe whose EQUAL-WEIGHT BASKET of
every stock did best per month in training, provided the basket:

* has at least MIN_MONTHS of history,
* traded at least MIN_TRADES_PER_MONTH times a month across the basket -
  an active system, by the owner's instruction,
* never fell more than MAX_DIP_PCT from a high,
* beat holding the same basket on average (excess > 0), and
* made money on average (avg month > 0).

Ranked by average month rather than by excess: the goal is stated as a
return, and the excess gate already refuses beta - a strategy in the market
a third of the time cannot beat full-time holding by beta alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from research.portfolio import Basket, build_basket
from research.sweep import ComboResult

MIN_MONTHS = 24
MIN_TRADES_PER_MONTH = 10.0
MAX_DIP_PCT = 30.0


@dataclass(frozen=True)
class TimeframePick:
    timeframe: str
    basket: Basket


def disqualified(basket: Basket) -> str | None:
    """Why a basket cannot be picked, in the owner's words - or None if it can."""
    if basket.month_count < MIN_MONTHS:
        return f"only {basket.month_count} months of history (needs {MIN_MONTHS})"
    if basket.trades_per_month < MIN_TRADES_PER_MONTH:
        return (f"too slow: {basket.trades_per_month:.1f} trades a month across the basket "
                f"(needs {MIN_TRADES_PER_MONTH:.0f})")
    if basket.worst_dip_pct > MAX_DIP_PCT:
        return f"fell {basket.worst_dip_pct:.1f}% from a high (limit {MAX_DIP_PCT:.0f}%)"
    if basket.avg_excess_pct <= 0:
        return (f"did not beat holding: {basket.avg_month_pct:+.2f}% a month against "
                f"{basket.avg_holding_month_pct:+.2f}% for holding")
    if basket.avg_month_pct <= 0:
        return f"lost money on average: {basket.avg_month_pct:+.2f}% a month"
    return None


def baskets_by_timeframe(results: Sequence[ComboResult]) -> dict[str, Basket]:
    """Every stock timeframe's basket, in the order the results name them."""
    seen: list[str] = []
    for r in results:
        if not r.is_index and r.timeframe not in seen:
            seen.append(r.timeframe)
    out: dict[str, Basket] = {}
    for timeframe in seen:
        basket = build_basket(results, timeframe=timeframe)
        if basket is not None:
            out[timeframe] = basket
    return out


def pick_timeframe(results: Sequence[ComboResult]) -> TimeframePick | None:
    candidates = [
        (order, TimeframePick(timeframe, basket))
        for order, (timeframe, basket) in enumerate(baskets_by_timeframe(results).items())
        if disqualified(basket) is None
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].basket.avg_month_pct,
                                      -item[1].basket.avg_excess_pct, item[0]))
    return candidates[0][1]


def best_score(results: Sequence[ComboResult]) -> float:
    """What a version scores under this rule: its pick's average month, or -inf."""
    pick = pick_timeframe(results)
    return float("-inf") if pick is None else pick.basket.avg_month_pct
