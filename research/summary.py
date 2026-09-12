"""What Opus is shown after a version is tested (design 5.2).

TRAINING ONLY. This object is the single channel between the sweep and the AI,
and it has no locked-year field - which is what makes design 2.4 structural
rather than a promise. A test asserts it, by field name and by serialised
content.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from costs import HoldingCostModel
from research.lakh import compound
from research.sweep import ComboResult

LIST_SIZE = 15


@dataclass(frozen=True)
class TrainingSummary:
    combos_tested: int
    combos_profitable: int
    total_trades: int
    win_rate_pct: float | None
    net_pnl: float
    gross_pnl: float
    fees_paid: float
    per_timeframe: list[dict[str, Any]] = field(default_factory=list)
    top: list[dict[str, Any]] = field(default_factory=list)
    bottom: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "combos_tested": self.combos_tested,
            "combos_profitable": self.combos_profitable,
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate_pct,
            "net_pnl": self.net_pnl,
            "gross_pnl": self.gross_pnl,
            "fees_paid": self.fees_paid,
            "per_timeframe": self.per_timeframe,
            "top": self.top,
            "bottom": self.bottom,
            "skipped": self.skipped,
        }


def _row(result: ComboResult, cost_model: HoldingCostModel, window_days: int) -> dict[str, Any]:
    scored = compound(result.trades, cost_model, window_days=window_days)
    wins = sum(1 for t in result.trades if t.net_pnl > 0)
    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "trades": len(result.trades),
        "win_rate_pct": round(100 * wins / len(result.trades), 2) if result.trades else None,
        "net_pnl": round(result.net_pnl, 2),
        "worst_dip_pct": scored.worst_dip_pct,
        "hold_return_pct": result.hold_return_pct,
    }


def build_summary(
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> TrainingSummary:
    """Aggregate one version's training results into what the AI is shown."""
    tested = [r for r in results if r.skipped_reason is None]
    skipped: dict[str, int] = {}
    for r in results:
        if r.skipped_reason is not None:
            skipped[r.skipped_reason] = skipped.get(r.skipped_reason, 0) + 1

    rows = [_row(r, cost_model, window_days_for(r)) for r in tested]
    trades = [t for r in tested for t in r.trades]
    gross = sum(t.gross_pnl for t in trades)
    fees = sum(t.costs for t in trades)
    wins = sum(1 for t in trades if t.net_pnl > 0)

    by_timeframe: dict[str, list[ComboResult]] = {}
    for r in tested:
        by_timeframe.setdefault(r.timeframe, []).append(r)

    per_timeframe = []
    for timeframe, group in sorted(by_timeframe.items()):
        profitable = sum(1 for r in group if r.net_pnl > 0)
        holds = [r.hold_return_pct for r in group if r.hold_return_pct is not None]
        per_timeframe.append({
            "timeframe": timeframe,
            "combos": len(group),
            "trades": sum(len(r.trades) for r in group),
            "net_pnl": round(sum(r.net_pnl for r in group), 2),
            "symbols_profitable_pct": round(100 * profitable / len(group), 2),
            "avg_hold_return_pct": round(sum(holds) / len(holds), 2) if holds else None,
        })

    ordered = sorted(rows, key=lambda row: row["net_pnl"], reverse=True)
    return TrainingSummary(
        combos_tested=len(tested),
        combos_profitable=sum(1 for r in tested if r.net_pnl > 0),
        total_trades=len(trades),
        win_rate_pct=round(100 * wins / len(trades), 2) if trades else None,
        net_pnl=round(sum(t.net_pnl for t in trades), 2),
        gross_pnl=round(gross, 2),
        fees_paid=round(fees, 2),
        per_timeframe=per_timeframe,
        top=ordered[:LIST_SIZE],
        bottom=list(reversed(ordered[-LIST_SIZE:])) if ordered else [],
        skipped=skipped,
    )
