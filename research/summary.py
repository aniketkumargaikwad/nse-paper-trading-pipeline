"""What Opus is shown after a version is tested (design 5.2).

TRAINING ONLY. This object is the single channel between the sweep and the AI,
and it has no locked-year field - which is what makes design 2.4 structural
rather than a promise. A test asserts it, by field name and by serialised
content.

The top and bottom tables are ranked by EXCESS OVER HOLDING, not by profit.
The training years averaged a 418% buy-and-hold return, so a long-only idea
always has winners; ranked by rupees, the tables were showing Opus the most
beta-heavy combinations and calling them the best. It said so itself after
the first full day, which is where this ranking came from.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from costs import HoldingCostModel
from research.lakh import START_VALUE, compound
from research.picker import baskets_by_timeframe, disqualified
from research.segment import classify, holding_days
from research.sweep import ComboResult

LIST_SIZE = 15
DAYS_PER_MONTH = 30.44
DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class TrainingSummary:
    combos_tested: int
    combos_profitable: int
    # How many beat simply holding the same symbol over the same window. In a
    # bull market this is the number that means something: every long-only
    # idea has winners, and combos_profitable counts the market's rise as
    # though the strategy had earned it.
    combos_beating_hold: int
    total_trades: int
    win_rate_pct: float | None
    net_pnl: float
    gross_pnl: float
    fees_paid: float
    per_timeframe: list[dict[str, Any]] = field(default_factory=list)
    # THE figures that decide a version (design 2026-09-16): every stock on
    # one timeframe at once, Rs 1 lakh each, month by month. One entry per
    # stock timeframe, each saying why it would or would not be picked.
    baskets: list[dict[str, Any]] = field(default_factory=list)
    top: list[dict[str, Any]] = field(default_factory=list)
    bottom: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def best_basket(self) -> dict[str, Any] | None:
        """The basket that would be picked, or failing that the best average month."""
        if not self.baskets:
            return None
        qualified = [b for b in self.baskets if b.get("qualifies")]
        pool = qualified or self.baskets
        return max(pool, key=lambda b: b.get("avg_month_pct") or float("-inf"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "combos_tested": self.combos_tested,
            "combos_profitable": self.combos_profitable,
            "combos_beating_hold": self.combos_beating_hold,
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate_pct,
            "net_pnl": self.net_pnl,
            "gross_pnl": self.gross_pnl,
            "fees_paid": self.fees_paid,
            "per_timeframe": self.per_timeframe,
            "baskets": self.baskets,
            "top": self.top,
            "bottom": self.bottom,
            "skipped": self.skipped,
        }


def _row(result: ComboResult, cost_model: HoldingCostModel, window_days: int) -> dict[str, Any]:
    scored = compound(result.trades, cost_model, window_days=window_days)
    wins = sum(1 for t in result.trades if t.net_pnl > 0)
    # Both sides as a percentage of the same starting money over the same
    # window, so the subtraction below means something.
    start = scored.start_value or START_VALUE
    returned = 100 * (scored.end_value - start) / start
    hold = result.hold_return_pct
    months = max(window_days / DAYS_PER_MONTH, 0.1)
    # A percentage means nothing without the years it took. These are what let
    # the message say "+16% a year" instead of "+345% at some point".
    years = max(window_days / DAYS_PER_YEAR, 0.01)
    return {
        "symbol": result.symbol,
        "timeframe": result.timeframe,
        "trades": len(result.trades),
        "trades_per_month": round(len(result.trades) / months, 2),
        # Measured from the trades, not from what the strategy calls itself:
        # the holding period decides the fees, the overnight risk and whether
        # the owner can run it at all.
        "segment": classify(result.trades),
        "held_days": (round(held, 2) if (held := holding_days(result.trades)) else None),
        "win_rate_pct": round(100 * wins / len(result.trades), 2) if result.trades else None,
        "net_pnl": round(result.net_pnl, 2),
        "return_pct": round(returned, 2),
        "cagr_pct": scored.cagr_pct,
        "end_value": round(scored.end_value, 2),
        "window_days": window_days,
        "window_years": round(years, 2),
        # The sweep's figure counts an open position; compound's does not.
        "worst_dip_pct": (result.worst_dip_pct if result.worst_dip_pct is not None
                          else scored.worst_dip_pct),
        "hold_return_pct": hold,
        # Named "holding_value", not "hold_end_value": that second name is a
        # LOCKED-YEAR column on research_runs, and the guard test rightly
        # refuses it here. This is the training window's own benchmark.
        "holding_value": None if hold is None else round(start * (1 + hold / 100), 2),
        "excess_vs_hold_pct": None if hold is None else round(returned - hold, 2),
    }


def _excess(row: dict[str, Any]) -> float:
    """A row with no benchmark cannot be judged, so it ranks below every row
    that can be."""
    excess = row["excess_vs_hold_pct"]
    return float("-inf") if excess is None else excess


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

    # Grouped from the rows rather than the results, so a timeframe is
    # summarised by the same numbers the top and bottom tables are ranked on.
    by_timeframe: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_timeframe.setdefault(row["timeframe"], []).append(row)

    per_timeframe = []
    for timeframe, group in sorted(by_timeframe.items()):
        profitable = sum(1 for row in group if row["net_pnl"] > 0)
        beating = sum(1 for row in group if (row["excess_vs_hold_pct"] or 0) > 0)
        holds = [row["hold_return_pct"] for row in group if row["hold_return_pct"] is not None]
        per_timeframe.append({
            "timeframe": timeframe,
            "combos": len(group),
            "trades": sum(row["trades"] for row in group),
            "net_pnl": round(sum(row["net_pnl"] for row in group), 2),
            "symbols_profitable_pct": round(100 * profitable / len(group), 2),
            "symbols_beating_hold_pct": round(100 * beating / len(group), 2),
            "avg_hold_return_pct": round(sum(holds) / len(holds), 2) if holds else None,
        })

    baskets = []
    for timeframe, basket in baskets_by_timeframe(tested).items():
        row = basket.as_dict()
        reason = disqualified(basket)
        row["qualifies"] = reason is None
        row["why_not"] = reason
        baskets.append(row)

    ordered = sorted(rows, key=_excess, reverse=True)
    return TrainingSummary(
        combos_tested=len(tested),
        combos_profitable=sum(1 for r in tested if r.net_pnl > 0),
        combos_beating_hold=sum(1 for row in rows if (row["excess_vs_hold_pct"] or 0) > 0),
        total_trades=len(trades),
        win_rate_pct=round(100 * wins / len(trades), 2) if trades else None,
        net_pnl=round(sum(t.net_pnl for t in trades), 2),
        gross_pnl=round(gross, 2),
        fees_paid=round(fees, 2),
        per_timeframe=per_timeframe,
        baskets=baskets,
        top=ordered[:LIST_SIZE],
        bottom=list(reversed(ordered[-LIST_SIZE:])) if ordered else [],
        skipped=skipped,
    )
