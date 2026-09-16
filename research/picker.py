"""Pick one TIMEFRAME from a version's TRAINING results (design 2026-09-16 2.3).

A fixed rule, not a judgement, so it cannot be tempted to cherry-pick.

THE RULE PICKS AN ACCOUNT, NOT A STOCK. The first rule chose the one stock in
~1,177 combinations with the largest lead over holding across eight years,
and the locked year then judged that stock alone - on one trade, twice out
of five runs. The best of 1,177 is nearly always a fluke, and one stock
cannot say anything about "per month" or "consistently".

So the rule now chooses the stock timeframe whose ACCOUNT - ten Rs 1 lakh
slots, signals taken as they arrive across every stock (research.account) -
did best per month in training, provided the account:

* has at least MIN_MONTHS of history,
* never fell more than MAX_DIP_PCT from a high,
* beat holding the equal-weight basket over the time it was invested
  (avg_excess_active_pct > 0): a rule flat 85% of the time is compared with
  holding for the 15% it was in the market, not for the whole month,
* made money on average (avg month > 0), and
* either traded at least MIN_TRADES_PER_MONTH times a month or averaged the
  owner's target month. The floor is SOFT by his instruction: "if we are
  meeting the monthly minimum, I am good with any number of trades".

Ranked by average month rather than by excess: the goal is stated as a
return, and the excess gate already refuses beta - a strategy in the market
a third of the time cannot beat full-time holding by beta alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from research.account import build_account
from research.portfolio import Basket, build_basket
from research.segment import TARGET_MONTHLY_MIN
from research.sweep import ComboResult

MIN_MONTHS = 24
MIN_TRADES_PER_MONTH = 10.0
MAX_DIP_PCT = 30.0


@dataclass(frozen=True)
class TimeframePick:
    timeframe: str
    account: Basket
    basket: Basket


def disqualified(reading: Basket) -> str | None:
    """Why a reading cannot be picked, in the owner's words - or None if it can."""
    if reading.month_count < MIN_MONTHS:
        return f"only {reading.month_count} months of history (needs {MIN_MONTHS})"
    if reading.worst_dip_pct > MAX_DIP_PCT:
        return f"fell {reading.worst_dip_pct:.1f}% from a high (limit {MAX_DIP_PCT:.0f}%)"
    if reading.avg_excess_active_pct <= 0:
        at_work = reading.slot_use_pct if reading.slot_use_pct is not None else reading.sleeve_use_pct
        return (f"did not beat holding while invested: {reading.avg_month_pct:+.2f}% a month "
                f"against {reading.avg_holding_month_pct:+.2f}% for holding, with the money at "
                f"work {at_work:.0f}% of the time")
    if reading.avg_month_pct <= 0:
        return f"lost money on average: {reading.avg_month_pct:+.2f}% a month"
    if (reading.trades_per_month < MIN_TRADES_PER_MONTH
            and reading.avg_month_pct < TARGET_MONTHLY_MIN):
        return (f"too slow: {reading.trades_per_month:.1f} trades a month (needs "
                f"{MIN_TRADES_PER_MONTH:.0f}, or {TARGET_MONTHLY_MIN:.0f}% a month to excuse it)")
    return None


def _timeframes(results: Sequence[ComboResult]) -> list[str]:
    seen: list[str] = []
    for r in results:
        if not r.is_index and r.timeframe not in seen:
            seen.append(r.timeframe)
    return seen


def baskets_by_timeframe(results: Sequence[ComboResult]) -> dict[str, Basket]:
    """Every stock timeframe's fully-funded basket, in the order the results name them."""
    out: dict[str, Basket] = {}
    for timeframe in _timeframes(results):
        basket = build_basket(results, timeframe=timeframe)
        if basket is not None:
            out[timeframe] = basket
    return out


def accounts_by_timeframe(results: Sequence[ComboResult]) -> dict[str, Basket]:
    """Every stock timeframe's account replay, in the same order."""
    out: dict[str, Basket] = {}
    for timeframe in _timeframes(results):
        account = build_account(results, timeframe=timeframe)
        if account is not None:
            out[timeframe] = account
    return out


def pick_timeframe(results: Sequence[ComboResult]) -> TimeframePick | None:
    accounts = accounts_by_timeframe(results)
    candidates = []
    for order, (timeframe, account) in enumerate(accounts.items()):
        if disqualified(account) is not None:
            continue
        basket = build_basket(results, timeframe=timeframe)
        if basket is None:
            continue
        candidates.append((order, TimeframePick(timeframe, account, basket)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].account.avg_month_pct,
                                      -item[1].account.avg_excess_active_pct, item[0]))
    return candidates[0][1]


def best_score(results: Sequence[ComboResult]) -> float:
    """What a version scores under this rule: its pick's average month, or -inf."""
    pick = pick_timeframe(results)
    return float("-inf") if pick is None else pick.account.avg_month_pct
