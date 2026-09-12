"""Pick one stock x timeframe from a version's TRAINING results (design 5.3).

A fixed rule, not a judgement: among combinations with enough trades and a
survivable worst dip, the highest compounded annual return after fees. Ties go
to more trades, then to the earlier combination, so the same results always
produce the same pick.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from costs import HoldingCostModel
from research.lakh import LakhResult, compound
from research.sweep import ComboResult

MIN_TRAINING_TRADES = 30
MAX_TRAINING_DIP_PCT = 30.0


@dataclass(frozen=True)
class Pick:
    result: ComboResult
    training: LakhResult


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
        lakh = compound(result.trades, cost_model, window_days=window_days_for(result))
        if lakh.cagr_pct is None or lakh.worst_dip_pct > MAX_TRAINING_DIP_PCT:
            continue
        candidates.append((order, Pick(result, lakh)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].training.cagr_pct, -item[1].training.trades, item[0]))
    return candidates[0][1]
