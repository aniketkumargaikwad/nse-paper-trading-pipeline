"""Pick one stock x timeframe from a version's TRAINING results (design 5.3).

A fixed rule, not a judgement, so it cannot be tempted to cherry-pick.

THE RULE BEATS BUY-AND-HOLD OR PICKS NOTHING. The first version ranked by
compounded annual return alone, which in a training window where simply
holding averaged +418% meant the winner was usually whichever stock rose
most - beta wearing a strategy's clothes. Measured on the 2026-09-14 run:
version 7 beat holding on 131 of 1,177 combinations, the picker chose none of
them, and its choice went on to lose 10.3% of the locked year while the stock
itself gained 30.7%.

So a combination qualifies only if it beat holding the same stock over the
same window, and the best excess over holding wins. Days where nothing
qualifies now report no pick, which is a truthful empty answer rather than a
flattering wrong one.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from costs import HoldingCostModel
from research.lakh import LakhResult, compound
from research.segment import trades_fast_enough
from research.sweep import ComboResult

DAYS_PER_MONTH = 30.44

MIN_TRAINING_TRADES = 30
MAX_TRAINING_DIP_PCT = 30.0


@dataclass(frozen=True)
class Pick:
    result: ComboResult
    training: LakhResult
    # How far ahead of simply holding the same stock, in percentage points
    # over the whole training window. The number the rule now ranks on.
    excess_vs_hold_pct: float = 0.0


def pick_best(
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> Pick | None:
    candidates: list[tuple[int, Pick]] = []
    for order, result in enumerate(results):
        if result.skipped_reason is not None or len(result.trades) < MIN_TRAINING_TRADES:
            continue
        window_days = window_days_for(result)
        # An active system, by instruction - but judged against ITS OWN
        # segment. Ten trades a month is a trade every two days, which only
        # an intraday system does, so one flat floor would rule long-term out
        # of the research entirely.
        months = max(window_days / DAYS_PER_MONTH, 0.1)
        if not trades_fast_enough(result.trades, months):
            continue
        lakh = compound(result.trades, cost_model, window_days=window_days)
        # The sweep's dip counts money still in an open position; compound's
        # only sees closed trades. Prefer the honest one - it is the whole
        # point of this filter.
        dip = result.worst_dip_pct if result.worst_dip_pct is not None else lakh.worst_dip_pct
        if lakh.cagr_pct is None or dip > MAX_TRAINING_DIP_PCT:
            continue
        if result.hold_return_pct is None:
            # No benchmark, so "did it beat doing nothing" has no answer and
            # the combination cannot be judged at all.
            continue
        returned = 100 * (lakh.end_value - lakh.start_value) / lakh.start_value
        excess = returned - result.hold_return_pct
        if excess <= 0:
            continue
        candidates.append((order, Pick(result, lakh, round(excess, 4))))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].excess_vs_hold_pct,
                                      -item[1].training.trades, item[0]))
    return candidates[0][1]
