"""Turn a finished run into the rows the database stores.

Pure: no client, no clock, no I/O. The evaluate command (and later the daily
loop) builds these and hands them to research.store, so what is written can be
tested without a database.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date, datetime
from typing import Any

from backtest_types import SimTrade
from costs import HoldingCostModel
from research.lakh import START_VALUE, LakhResult, compound, passed
from research.sweep import ComboResult

MONTHS_PER_YEAR = 12


def run_row(
    *,
    started_at: datetime,
    finished_at: datetime | None,
    status: str,
    data_end: date,
    locked_from: date,
    strategy_name: str | None,
    pick_symbol: str | None,
    pick_timeframe: str | None,
    locked: LakhResult | None,
    hold_end_value: float | None,
    combos_profitable: int,
    combos_tested: int,
    warnings: Sequence[str],
    trigger: str = "manual",
    final_version_id: int | None = None,
    versions_tried: int = 1,
    ideas_dropped: int = 0,
    ai_review: str | None = None,
) -> dict[str, Any]:
    """The grid row (design 5.5, 6.2).

    A run with no qualifying pick is a RESULT, not an error: every locked-year
    column is null and the verdict is False, so the grid can show it plainly.
    """
    win_rate = None
    trades_per_month = None
    if locked is not None and locked.trades:
        win_rate = round(100 * locked.winning_trades / locked.trades, 2)
    if locked is not None:
        trades_per_month = round(locked.trades / MONTHS_PER_YEAR, 2)

    return {
        "trigger": trigger,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "data_end": data_end,
        "locked_from": locked_from,
        "locked_to": data_end,
        "final_strategy_name": strategy_name,
        "final_version_id": final_version_id,
        "pick_symbol": pick_symbol,
        "pick_timeframe": pick_timeframe,
        "locked_trades": None if locked is None else locked.trades,
        "trades_per_month": trades_per_month,
        "win_rate_pct": win_rate,
        "lakh_end_value": None if locked is None else locked.end_value,
        "hold_end_value": hold_end_value,
        "worst_dip_pct": None if locked is None else locked.worst_dip_pct,
        "verdict_passed": False if locked is None else passed(locked),
        "beat_holding": (
            None if locked is None or hold_end_value is None
            else locked.end_value > hold_end_value
        ),
        "combos_profitable": combos_profitable,
        "combos_tested": combos_tested,
        "versions_tried": versions_tried,
        "ideas_dropped": ideas_dropped,
        "ai_review": ai_review,
        "warnings": list(warnings),
    }


def combo_rows(
    run_id: str,
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> list[dict[str, Any]]:
    """One row per combination the final version was tested on."""
    rows: list[dict[str, Any]] = []
    for r in results:
        scored = None
        if r.skipped_reason is None and r.trades:
            scored = compound(r.trades, cost_model, window_days=window_days_for(r))
        wins = sum(1 for t in r.trades if t.net_pnl > 0)
        rows.append({
            "run_id": run_id,
            "symbol": r.symbol,
            "timeframe": r.timeframe,
            "trades": len(r.trades),
            "win_rate_pct": round(100 * wins / len(r.trades), 2) if r.trades else None,
            "net_pnl": round(r.net_pnl, 2) if r.trades else None,
            "cagr_pct": None if scored is None else scored.cagr_pct,
            "worst_dip_pct": None if scored is None else scored.worst_dip_pct,
            "skipped_reason": r.skipped_reason,
        })
    return rows


def locked_trade_rows(
    run_id: str, trades: Sequence[SimTrade], cost_model: HoldingCostModel,
    *, start_value: float = START_VALUE,
) -> list[dict[str, Any]]:
    """The pick's locked-year trades, each with the balance it left behind."""
    rows: list[dict[str, Any]] = []
    balance = start_value
    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        before = balance
        balance = max(balance + balance * gross_return - fees, 0.0)
        rows.append({
            "run_id": run_id,
            "entry_at": t.entry_fill_ts,
            "exit_at": t.exit_fill_ts,
            "side": t.position_type,
            "entry_price": round(t.entry_price, 4),
            "exit_price": round(t.exit_price, 4),
            "fees": round(fees, 4),
            "net_return_pct": round(100 * (balance - before) / before, 4) if before else 0.0,
            "balance_after": round(balance, 2),
        })
    return rows


def equity_rows(run_id: str, series: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Daily balances, tagged with the run they belong to."""
    return [{"run_id": run_id, **point} for point in series]
