"""What a tested options version reports to the AI (design §4).

Built from training-window reports only: the engine runs on data cut at the
training window's end, so nothing here can come from the exam year - a test
asserts every date in it falls inside the window.

`pick` is the day's pick rule: of the indexes where the version passes the
options rule (fno.rules), the one with the best average month. `score` stands
in for the AI's choice when the day is cut short (research.run_day.choose_final):
a passing version first, then the best average month.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

HEADLINE = ("avg_month_pct", "median_month_pct", "months_positive_pct", "worst_day_pct",
            "worst_day", "worst_fall_pct", "trades", "trades_per_month", "win_rate_pct",
            "avg_days_held", "total_charges", "end_value", "avg_holding_month_pct",
            "years_beating_holding_pct", "edge_t")
WORST_DAYS = 5
MOSTLY_SKIPPED_PCT = 50.0


def index_summary(report: Any, *, mode: str, why_not: str | None, sessions: int) -> dict[str, Any]:
    """One index's training reading."""
    h = report.headline
    skipped = dict(report.skipped)
    share = round(100 * sum(skipped.values()) / sessions, 1) if sessions else 0.0
    out: dict[str, Any] = {"underlying": report.underlying, "mode": mode,
                           "start": report.start, "end": report.end,
                           "passes": why_not is None, "why_not": why_not}
    out.update({k: h.get(k) for k in HEADLINE})
    out.update(years=list(report.basket.years), splits=dict(report.splits),
               worst_days=list(report.worst_days[:WORST_DAYS]), shock_days=list(report.shock_days),
               skipped=skipped, sessions=sessions, skipped_share_pct=share, warning="")
    if share > MOSTLY_SKIPPED_PCT:
        reasons = ", ".join(f"{n} {k}" for k, n in sorted(skipped.items(), key=lambda kv: -kv[1]))
        out["warning"] = (f"skipped on {share:.0f}% of sessions ({reasons}): most days the strategy "
                          "could not be entered as written, so these numbers describe very few days")
    return out


@dataclass
class OptionsSummary:
    mode: str
    indexes: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "indexes": list(self.indexes)}

    def pick(self) -> Mapping[str, Any] | None:
        passing = [s for s in self.indexes if s["passes"]]
        return max(passing, key=lambda s: s["avg_month_pct"]) if passing else None


def score(summary: OptionsSummary) -> tuple[bool, float]:
    months = [s["avg_month_pct"] for s in summary.indexes if s.get("avg_month_pct") is not None]
    return summary.pick() is not None, max(months, default=float("-inf"))


def one_line(summary: OptionsSummary | Mapping[str, Any] | None) -> str:
    """'NIFTY +0.72%/month, 18.4 trades/month, fails: ...; BANKNIFTY ...' for logs and the ledger."""
    if summary is None:
        return "not tested"
    rows: Sequence[Mapping[str, Any]] = (summary.indexes if isinstance(summary, OptionsSummary)
                                          else summary.get("indexes", []))
    parts = []
    for s in rows:
        verdict = "PASSES" if s["passes"] else f"fails: {s['why_not']}"
        if s.get("avg_month_pct") is None:
            parts.append(f"{s['underlying']} {verdict}")
            continue
        parts.append(f"{s['underlying']} {s['avg_month_pct']:+.2f}%/month, "
                     f"{s['trades_per_month']:.1f} trades/month, worst day {s['worst_day_pct']:+.1f}%, "
                     f"luck t {'n/a' if s['edge_t'] is None else format(s['edge_t'], '+.2f')} - {verdict}")
    return "; ".join(parts)
