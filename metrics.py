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
# Profitable on at least this share of the symbols actually traded.
#
# Replaces a fixed count of 3, which was written when strategies named five
# symbols by hand - a 60% bar. Applied unchanged to NIFTY50 it becomes 3 of 50:
# a 6% bar that would pass a strategy losing money on 47 stocks, which is
# precisely the curve-fitting the rule exists to catch.
#
# 40% is a judgement call, recorded as one. It preserves roughly what the old
# rule meant on a small universe while scaling honestly to 50 or 500.
MIN_PROFITABLE_SYMBOL_PCT = 40.0

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
    actual_pct = 100.0 * profitable_symbols / total_symbols if total_symbols else 0.0
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
            "required_pct": MIN_PROFITABLE_SYMBOL_PCT,
            "actual_pct": round(actual_pct, 2),
            "actual_profitable_symbols": profitable_symbols,
            "total_symbols": total_symbols,
            "passed": actual_pct >= MIN_PROFITABLE_SYMBOL_PCT,
        },
    }
    return all(f["passed"] for f in flags.values()), flags


# ---------------------------------------------------------------------------
# Pooled: every symbol's trades as one stream
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PooledMetrics:
    """Every trade across the universe treated as one stream.

    Answers "what would this have made". Says nothing about whether that came
    from a broad edge or one lucky symbol - that is what DispersionMetrics is
    for, and the two are reported together for exactly that reason.
    """

    total_trades: int
    winning_trades: int
    net_pnl: float
    win_rate_pct: float
    profit_factor: float | None
    max_drawdown_pct: float
    longest_losing_streak: int
    capital_base: float
    ordered_net_pnls: list[float]   # chronological


def pooled_metrics(
    per_symbol: dict[str, list[SimTrade]], capital_base: float | None = None
) -> PooledMetrics:
    """Pool every symbol's trades into one chronologically ordered stream.

    Sort order is load-bearing, not cosmetic: drawdown and losing streak walk
    the equity curve trade by trade, so concatenating symbol-by-symbol would
    produce a curve that never existed and a drawdown that never happened.
    """
    trades = sorted(
        (t for symbol_trades in per_symbol.values() for t in symbol_trades),
        key=lambda t: t.exit_fill_ts,
    )
    if not trades:
        return PooledMetrics(0, 0, 0.0, 0.0, None, 0.0, 0, capital_base or 0.0, [])

    pnls = [t.net_pnl for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    base = capital_base or max(t.entry_price * t.quantity for t in trades)

    equity = peak = 0.0
    max_dd = 0.0
    streak = longest = 0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        streak = streak + 1 if p < 0 else 0
        longest = max(longest, streak)

    return PooledMetrics(
        total_trades=len(trades),
        winning_trades=len(wins),
        net_pnl=round(sum(pnls), 4),
        win_rate_pct=round(100.0 * len(wins) / len(trades), 2),
        profit_factor=round(sum(wins) / abs(sum(losses)), 4) if losses else None,
        max_drawdown_pct=round(100.0 * max_dd / base, 2) if base else 0.0,
        longest_losing_streak=longest,
        capital_base=round(base, 4),
        ordered_net_pnls=pnls,
    )


# ---------------------------------------------------------------------------
# Dispersion: was the edge broad, or one lucky symbol?
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DispersionMetrics:
    """How the pooled result was distributed across symbols.

    Exists because a pooled headline cannot distinguish a broad edge from one
    outlier carrying four losers, and those deserve opposite decisions.
    """

    symbols_traded: int
    symbols_profitable: int
    median_symbol_pnl: float
    best_symbol: str | None
    best_symbol_pnl: float | None
    worst_symbol: str | None
    worst_symbol_pnl: float | None


def dispersion_metrics(per_symbol: dict[str, list[SimTrade]]) -> DispersionMetrics:
    """Per-symbol net P&L, summarised.

    A symbol that produced no trades counts as traded with a P&L of zero: it
    WAS tested, and dropping it would quietly raise the proportion of symbols
    the edge appeared to work on.
    """
    if not per_symbol:
        return DispersionMetrics(0, 0, 0.0, None, None, None, None)

    totals = {
        symbol: round(sum(t.net_pnl for t in trades), 4)
        for symbol, trades in per_symbol.items()
    }
    best = max(totals, key=lambda s: totals[s])
    worst = min(totals, key=lambda s: totals[s])
    return DispersionMetrics(
        symbols_traded=len(totals),
        symbols_profitable=sum(1 for v in totals.values() if v > 0),
        median_symbol_pnl=round(statistics.median(totals.values()), 4),
        best_symbol=best,
        best_symbol_pnl=totals[best],
        worst_symbol=worst,
        worst_symbol_pnl=totals[worst],
    )


# ---------------------------------------------------------------------------
# Risk-adjusted, on two explicitly separate bases
# ---------------------------------------------------------------------------

# Trading days in a year, the conventional annualisation factor for a daily
# return series. NSE trades ~250; 252 is the standard constant and keeps these
# figures comparable to published ones, which is the entire point of computing
# a daily-basis Sharpe at all.
TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class RiskMetrics:
    """Risk-adjusted measures on two explicitly separate bases.

    DAILY basis (sharpe, sortino, cagr) divides daily realised P&L by a capital
    base and annualises conventionally, so the numbers are comparable to
    published figures.

    TRADE basis (expectancy, system quality number) needs no capital
    assumption. It is intuitive but comparable to nothing outside this system.

    The two are never mixed, and a null means "not measurable from this data" -
    never zero. An absent measurement that reads as a measured zero is the
    quiet kind of wrong this codebase refuses everywhere else.
    """

    sharpe_daily: float | None
    sortino_daily: float | None
    cagr_pct: float | None
    expectancy_per_trade: float
    system_quality_number: float | None
    trading_days: int


def daily_pnl_series(per_symbol: dict[str, list[SimTrade]]) -> dict[Any, float]:
    """Realised net P&L per IST calendar date, keyed by the EXIT fill date.

    Realised only: a position still open contributes nothing until it closes.
    Marking to market would need a price series per open position and a second
    source of truth for P&L, so the lumpiness is accepted and documented rather
    than papered over.
    """
    from config import IST

    daily: dict[Any, float] = {}
    for trades in per_symbol.values():
        for t in trades:
            day = t.exit_fill_ts.astimezone(IST).date()
            daily[day] = daily.get(day, 0.0) + t.net_pnl
    return daily


def risk_metrics(
    per_symbol: dict[str, list[SimTrade]],
    *,
    capital_base: float,
    trading_days: int,
) -> RiskMetrics:
    """Daily-basis and trade-basis risk measures.

    `trading_days` is the length of the tested window, not the number of days
    that happened to produce a trade: a strategy that traded on 3 days out of
    250 is not a 3-day strategy, and dividing by 3 would flatter it enormously.
    """
    all_trades = [t for trades in per_symbol.values() for t in trades]
    pnls = [t.net_pnl for t in all_trades]

    expectancy = round(sum(pnls) / len(pnls), 4) if pnls else 0.0

    sqn: float | None = None
    if len(pnls) >= 2:
        sd = statistics.stdev(pnls)
        if sd > 0:
            sqn = round(sum(pnls) / len(pnls) / sd * (len(pnls) ** 0.5), 4)

    sharpe = sortino = cagr = None
    if capital_base > 0 and trading_days >= 2:
        daily = daily_pnl_series(per_symbol)
        # Every day in the window, not only days that traded: a flat day is a
        # real observation of how this strategy behaves.
        observed = [daily[d] for d in sorted(daily)]
        flat_days = max(trading_days - len(observed), 0)
        returns = [p / capital_base for p in observed] + [0.0] * flat_days

        if len(returns) >= 2:
            mean = sum(returns) / len(returns)
            sd = statistics.stdev(returns)
            if sd > 0:
                sharpe = round(mean / sd * (TRADING_DAYS_PER_YEAR ** 0.5), 4)
            downside = [r for r in returns if r < 0]
            if downside:
                dd = (sum(r * r for r in downside) / len(downside)) ** 0.5
                if dd > 0:
                    sortino = round(mean / dd * (TRADING_DAYS_PER_YEAR ** 0.5), 4)
            total_return = sum(returns)
            years = len(returns) / TRADING_DAYS_PER_YEAR
            if years > 0 and total_return > -1:
                cagr = round((((1 + total_return) ** (1 / years)) - 1) * 100, 4)

    return RiskMetrics(
        sharpe_daily=sharpe,
        sortino_daily=sortino,
        cagr_pct=cagr,
        expectancy_per_trade=expectancy,
        system_quality_number=sqn,
        trading_days=trading_days,
    )
