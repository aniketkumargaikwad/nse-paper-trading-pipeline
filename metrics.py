"""Metrics for backtest results. PURE: no I/O, no database, no clock.

Every number here feeds a decision about whether to risk real money, so the
module is deliberately isolated: each function takes trades (and where needed a
capital base) and returns numbers, which means every one can be checked against
a value worked out by hand.

That isolation is the point. A subtly wrong Sharpe ratio is worse than no
Sharpe ratio, because it looks authoritative - and the difference between right
and wrong here is invisible unless the test can state the expected number
independently.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from backtest_types import SimTrade

# --- Kill-rule thresholds ---------------------------------------------------
MIN_TRADES = 30
MAX_DRAWDOWN_PCT = 20.0
MIN_PROFITABLE_SYMBOLS = 3  # capped at the strategy's instrument count

@dataclass(frozen=True)
class ComboMetrics:
    """Per strategy-x-instrument result, shaped for the backtest_results table."""

    total_trades: int
    winning_trades: int
    net_pnl: float
    win_rate_pct: float
    profit_factor: float | None   # None when there are no losing trades
    max_drawdown_pct: float
    longest_losing_streak: int


def compute_metrics(trades: list[SimTrade]) -> ComboMetrics:
    if not trades:
        return ComboMetrics(0, 0, 0.0, 0.0, None, 0.0, 0)

    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    profit_factor = round(sum(wins) / abs(sum(losses)), 4) if losses else None

    # Equity curve of cumulative net P&L, walked trade by trade. The % base
    # is the LARGEST entry notional — roughly the capital you would need to
    # run this combination — which keeps the number transparent rather than
    # depending on an arbitrary "starting capital" input.
    capital_base = max(t.entry_price * t.quantity for t in trades)
    equity = peak = 0.0
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    streak = longest = 0
    for p in pnls:
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)

    return ComboMetrics(
        total_trades=len(trades),
        winning_trades=len(wins),
        net_pnl=round(sum(pnls), 4),
        win_rate_pct=round(100.0 * len(wins) / len(trades), 2),
        profit_factor=profit_factor,
        max_drawdown_pct=round(100.0 * max_dd / capital_base, 2) if capital_base else 0.0,
        longest_losing_streak=longest,
    )


def evaluate_kill_rules(
    metrics: ComboMetrics, profitable_symbols: int, total_symbols: int
) -> tuple[bool, dict[str, Any]]:
    """Return (passed_all, flags). Flags carry required-vs-actual for display."""
    required_symbols = min(MIN_PROFITABLE_SYMBOLS, total_symbols)
    flags = {
        "min_trades": {
            "required": MIN_TRADES,
            "actual": metrics.total_trades,
            "passed": metrics.total_trades >= MIN_TRADES,
        },
        "net_positive_after_costs": {
            "required": "> 0",
            "actual": metrics.net_pnl,
            "passed": metrics.net_pnl > 0,
        },
        "drawdown_within_cap": {
            "required_max_pct": MAX_DRAWDOWN_PCT,
            "actual_pct": metrics.max_drawdown_pct,
            "passed": metrics.max_drawdown_pct <= MAX_DRAWDOWN_PCT,
        },
        "symbol_robustness": {
            "required_profitable_symbols": required_symbols,
            "actual_profitable_symbols": profitable_symbols,
            "passed": profitable_symbols >= required_symbols,
        },
    }
    return all(f["passed"] for f in flags.values()), flags
