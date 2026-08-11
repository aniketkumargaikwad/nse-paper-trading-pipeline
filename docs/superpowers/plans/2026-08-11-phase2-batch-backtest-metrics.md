# Phase 2 — Batch Backtest Engine and Metric Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `universe: NIFTY50` actually backtest 50 symbols, and give each strategy one summary row that says whether the edge was broad or one lucky stock.

**Architecture:** Metric maths moves out of `backtest.py` into a pure `metrics.py` that never touches a database, network or clock, so a Sharpe ratio can be checked against a hand-computed number. `backtest.py` keeps orchestration and gains universe resolution. A new `backtest_runs` table holds the strategy-level verdict alongside the exact symbol list and constituent date, joined to the existing per-symbol `backtest_results` on `batch_id`.

**Tech Stack:** Python 3.11+, pandas, numpy, Supabase (`supabase-py`), pytest.

---

## Ground rules for every task

**Run tests with the project venv on Windows:**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

**Console encoding:** any command whose output contains `₹` needs `PYTHONIOENCODING=utf-8` — this console is cp1252 and will otherwise raise `UnicodeEncodeError`. Example:

```bash
PYTHONIOENCODING=utf-8 DATA_PROVIDER=yfinance .\.venv\Scripts\python.exe backtest.py --no-db --years 2
```

**Starting state:** 550 tests pass, 1 skipped. The suite must be green at every commit; this plan is sequenced so it always is.

**Real data is available.** All 50 NIFTY50 symbols have 2 years of 5-minute candles cached in Supabase (1.85M rows), and `symbol_groups` holds NIFTY50/100/500. Tasks that say "verify against real data" can actually do so.

---

## File structure

**Create:**

| Path | Responsibility |
|---|---|
| `metrics.py` | All metric maths — per-symbol, pooled, dispersion, daily and trade-basis risk measures. Pure: no I/O, no database, no clock |
| `sql/004_backtest_runs.sql` | The `backtest_runs` table |
| `tests/test_metrics.py` | Every metric against hand-computed values |
| `tests/test_backtest_universe.py` | Universe resolution inside the engine |

**Modify:**

| Path | Change |
|---|---|
| `backtest.py` | Resolve universes; build the run row; import from `metrics` |
| `db.py:641` | Add `insert_backtest_runs` |
| `tests/test_backtest.py` | Metric imports move to `metrics` |

---

## Task 1: Extract metrics into a pure module

Pure move first, so the behaviour-changing tasks that follow are reviewable on their own.

**Files:**
- Create: `metrics.py`
- Modify: `backtest.py`, `tests/test_backtest.py`
- Test: existing suite stays green at 550

- [ ] **Step 1: Record the baseline**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: `550 passed, 1 skipped`. Every later task compares against this.

- [ ] **Step 2: Create `metrics.py` and move the existing code verbatim**

Move `ComboMetrics`, `compute_metrics`, `evaluate_kill_rules`, and the three kill-rule constants (`MIN_TRADES`, `MAX_DRAWDOWN_PCT`, `MIN_PROFITABLE_SYMBOLS`) out of `backtest.py` **unchanged**. Do not fix, rename or improve anything while moving — later tasks change them deliberately.

File header:

```python
"""Metrics for backtest results. PURE: no I/O, no database, no clock.

Every number here feeds a decision about whether to risk real money, so the
module is deliberately isolated: each function takes trades (and where needed a
capital base) and returns numbers, which means every one can be checked against
a value worked out by hand.

That isolation is the point. A subtly wrong Sharpe ratio is worse than no
Sharpe ratio, because it looks authoritative — and the difference between right
and wrong here is invisible unless the test can state the expected number
independently.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from backtest_types import SimTrade
```

> `SimTrade` lives in `backtest.py` today and importing it from there would
> make `metrics` depend on the orchestration module — the opposite of the
> intended direction. Step 3 moves it.

- [ ] **Step 3: Create `backtest_types.py` holding the shared trade records**

Move `SimTrade`, `SkippedEntry` and `SimResult` out of `backtest.py` verbatim into a new `backtest_types.py`:

```python
"""Trade records shared by the simulator and the metrics module.

Separate from backtest.py so metrics.py can read a trade without importing the
orchestration module — which pulls in Supabase, the data provider and argparse,
none of which a pure metric function should ever need.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
```

Then the three dataclasses, unchanged.

- [ ] **Step 4: Re-import in `backtest.py`**

Replace the moved definitions with:

```python
from backtest_types import SimResult, SimTrade, SkippedEntry
from metrics import (
    MAX_DRAWDOWN_PCT,
    MIN_PROFITABLE_SYMBOLS,
    MIN_TRADES,
    ComboMetrics,
    compute_metrics,
    evaluate_kill_rules,
)
```

Keep `backtest.py` re-exporting these names so `from backtest import MIN_TRADES, SimTrade` keeps working — `tests/test_backtest.py` imports them that way.

- [ ] **Step 5: Verify nothing changed**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
.\.venv\Scripts\python.exe -c "import backtest, metrics, backtest_types, paper_engine; print('imports ok')"
```

Expected: `550 passed, 1 skipped`, then `imports ok`. A pure move changes nothing.

- [ ] **Step 6: Commit**

```bash
git add metrics.py backtest_types.py backtest.py
git commit -m "refactor(metrics): extract metric maths into a pure module

backtest.py holds simulation, metrics, kill rules, CLI and database writes in
one ~700-line file. Phase 2 adds pooled metrics, dispersion and risk-adjusted
measures on two bases, which would push it past 900.

The metric code is the part that most needs isolated testing: a subtly wrong
Sharpe is worse than none because it looks authoritative. Now it takes trades
and returns numbers, with no database in the way.

backtest_types.py carries the shared trade records so metrics.py can read a
trade without importing the orchestration module.

No behaviour change: same tests, same count.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: Pooled metrics over chronologically ordered trades

**Files:**
- Modify: `metrics.py`
- Test: `tests/test_metrics.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics.py`:

```python
"""Metrics against hand-computed values. Pure — no network, no database.

Every expected number here is worked out independently in the test, not copied
from the implementation's output. A metric test that asserts what the code
happens to produce proves only that the code is deterministic.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest_types import SimTrade  # noqa: E402
from metrics import pooled_metrics  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def trade(net: float, *, exit_day: int = 1, entry_price: float = 100.0,
          quantity: int = 10) -> SimTrade:
    """One completed trade with a chosen net P&L and exit date."""
    ts = datetime(2026, 8, exit_day, 10, 0, tzinfo=IST).astimezone(UTC)
    return SimTrade(
        entry_signal_ts=ts - timedelta(minutes=30),
        entry_fill_ts=ts - timedelta(minutes=15),
        exit_signal_ts=ts,
        exit_fill_ts=ts,
        position_type="long",
        quantity=quantity,
        intended_entry_price=entry_price,
        entry_price=entry_price,
        intended_exit_price=entry_price + net / quantity,
        exit_price=entry_price + net / quantity,
        exit_reason="target",
        gross_pnl=net,
        costs=0.0,
        net_pnl=net,
    )


def test_pooled_sums_across_symbols():
    per_symbol = {"A": [trade(100), trade(-40)], "B": [trade(60)]}
    m = pooled_metrics(per_symbol)
    assert m.total_trades == 3
    assert m.winning_trades == 2
    assert m.net_pnl == pytest.approx(120.0)          # 100 - 40 + 60
    assert m.win_rate_pct == pytest.approx(66.67, abs=0.01)
    assert m.profit_factor == pytest.approx(4.0)      # 160 / 40


def test_pooled_orders_trades_chronologically_not_by_symbol():
    """Ordering is load-bearing: drawdown walks the equity curve trade by trade.

    A: day 1 +100, day 4 -300.   B: day 2 +50, day 3 +40.

    Chronological  (+100, +50, +40, -300): equity 100, 150, 190, -110.
                   Peak 190, so max drawdown 300.
    Symbol-by-symbol (+100, -300, +50, +40): equity 100, -200, -150, -110.
                   Peak 100, so max drawdown 300 by value but from a
                   DIFFERENT peak — and the losing streak differs too.

    Both the order and the streak are asserted, so a symbol-by-symbol
    concatenation cannot pass by coincidence.
    """
    per_symbol = {
        "A": [trade(100, exit_day=1), trade(-300, exit_day=4)],
        "B": [trade(50, exit_day=2), trade(40, exit_day=3)],
    }
    m = pooled_metrics(per_symbol)
    assert m.ordered_net_pnls == [100.0, 50.0, 40.0, -300.0]
    assert m.longest_losing_streak == 1
    # Peak 190 reached before the loss; drawdown measured from there.
    assert m.max_drawdown_pct == pytest.approx(
        100.0 * 300.0 / m.capital_base, abs=0.01
    )


def test_pooled_with_no_trades_is_zeroed_not_absent():
    m = pooled_metrics({"A": [], "B": []})
    assert m.total_trades == 0
    assert m.net_pnl == 0.0
    assert m.profit_factor is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
```

Expected: FAIL — `pooled_metrics` does not exist.

- [ ] **Step 3: Implement `pooled_metrics`**

Append to `metrics.py`:

```python
@dataclass(frozen=True)
class PooledMetrics:
    """Every trade across the universe treated as one stream.

    Answers "what would this have made". Says nothing about whether that came
    from a broad edge or one lucky symbol — that is what DispersionMetrics is
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
    ordered_net_pnls: list[float]   # chronological, retained for the daily series


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
```

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat(metrics): pooled metrics over chronologically ordered trades

Sort order is load-bearing. Drawdown and losing streak walk the equity curve
trade by trade, so concatenating symbol-by-symbol would produce a curve that
never existed and a drawdown that never happened.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Dispersion — was the edge broad, or one lucky symbol?

**Files:**
- Modify: `metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
from metrics import dispersion_metrics  # noqa: E402  (add to imports at top)


def test_dispersion_exposes_a_one_lucky_symbol_strategy():
    """The case the whole dispersion idea exists for.

    Pooled P&L is strongly positive, yet the strategy lost on four of five
    symbols. A headline number alone would call this a success.
    """
    per_symbol = {
        "LUCKY": [trade(1000)],
        "A": [trade(-50)], "B": [trade(-60)],
        "C": [trade(-40)], "D": [trade(-30)],
    }
    d = dispersion_metrics(per_symbol)
    assert d.symbols_traded == 5
    assert d.symbols_profitable == 1
    assert d.median_symbol_pnl == pytest.approx(-40.0)
    assert d.best_symbol == "LUCKY"
    assert d.best_symbol_pnl == pytest.approx(1000.0)
    assert d.worst_symbol == "B"
    assert d.worst_symbol_pnl == pytest.approx(-60.0)


def test_dispersion_on_a_broadly_profitable_strategy():
    per_symbol = {"A": [trade(30)], "B": [trade(40)], "C": [trade(-10)]}
    d = dispersion_metrics(per_symbol)
    assert d.symbols_profitable == 2
    assert d.median_symbol_pnl == pytest.approx(30.0)


def test_a_symbol_that_traded_nothing_still_counts_as_traded():
    """It was tested and produced no signal. Dropping it would overstate the
    proportion of symbols the edge worked on."""
    per_symbol = {"A": [trade(50)], "SILENT": []}
    d = dispersion_metrics(per_symbol)
    assert d.symbols_traded == 2
    assert d.symbols_profitable == 1
    assert d.median_symbol_pnl == pytest.approx(25.0)   # median of [0, 50]
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
```

Expected: FAIL — `dispersion_metrics` does not exist.

- [ ] **Step 3: Implement**

Append to `metrics.py`:

```python
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
```

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat(metrics): dispersion across symbols

A pooled headline cannot tell a broad edge from one outlier carrying four
losers, and those deserve opposite decisions. A symbol that traded nothing
counts as traded with zero P&L — it WAS tested, and dropping it would quietly
raise the proportion of symbols the edge appeared to work on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Risk-adjusted metrics on two clearly separated bases

The heart of the phase. Every expected value below is computed by hand in the test.

**Files:**
- Modify: `metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
import math  # noqa: E402  (add to imports at top)

from metrics import RiskMetrics, risk_metrics  # noqa: E402


def test_daily_series_attributes_pnl_to_the_exit_date():
    """Two trades closing the same day are one daily observation, not two."""
    per_symbol = {"A": [trade(100, exit_day=1), trade(-40, exit_day=1)],
                  "B": [trade(60, exit_day=2)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=2)
    # Day 1: +60. Day 2: +60. Returns: 0.006, 0.006. Zero variance.
    assert r.sharpe_daily is None, "zero variance must not fabricate a Sharpe"


def test_sharpe_daily_matches_a_hand_computed_value():
    # Day 1 +100, day 2 -50, day 3 +150 on a 10,000 capital base.
    per_symbol = {"A": [trade(100, exit_day=1), trade(-50, exit_day=2),
                        trade(150, exit_day=3)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    returns = [0.01, -0.005, 0.015]
    mean = sum(returns) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in returns) / (3 - 1))  # sample sd
    expected = mean / sd * math.sqrt(252)
    assert r.sharpe_daily == pytest.approx(expected, rel=1e-6)


def test_sortino_uses_only_downside_deviation():
    per_symbol = {"A": [trade(100, exit_day=1), trade(-50, exit_day=2),
                        trade(150, exit_day=3)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    returns = [0.01, -0.005, 0.015]
    mean = sum(returns) / 3
    downside = [x for x in returns if x < 0]
    dd = math.sqrt(sum(x ** 2 for x in downside) / len(downside))
    expected = mean / dd * math.sqrt(252)
    assert r.sortino_daily == pytest.approx(expected, rel=1e-6)
    assert r.sortino_daily > r.sharpe_daily, (
        "with one loser among three, downside deviation is smaller than total "
        "deviation, so Sortino must exceed Sharpe"
    )


def test_expectancy_and_sqn_are_trade_basis_and_need_no_capital():
    per_symbol = {"A": [trade(100), trade(-50), trade(150)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=3)

    pnls = [100.0, -50.0, 150.0]
    mean = sum(pnls) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in pnls) / 2)
    assert r.expectancy_per_trade == pytest.approx(mean)
    assert r.system_quality_number == pytest.approx(mean / sd * math.sqrt(3), rel=1e-6)


def test_a_single_trading_day_yields_null_not_zero():
    """An absent measurement must never read as a measured zero."""
    per_symbol = {"A": [trade(100, exit_day=1)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=1)
    assert r.sharpe_daily is None
    assert r.sortino_daily is None
    assert r.system_quality_number is None
    assert r.expectancy_per_trade == pytest.approx(100.0)  # still meaningful


def test_no_trades_gives_null_risk_metrics_and_zero_expectancy():
    r = risk_metrics({"A": []}, capital_base=10000.0, trading_days=250)
    assert r.sharpe_daily is None
    assert r.sortino_daily is None
    assert r.expectancy_per_trade == 0.0


def test_sortino_is_null_when_nothing_lost():
    """No downside means no downside deviation — a division, not an infinity."""
    per_symbol = {"A": [trade(100, exit_day=1), trade(50, exit_day=2)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=2)
    assert r.sortino_daily is None


def test_cagr_matches_a_hand_computed_value():
    per_symbol = {"A": [trade(1000, exit_day=1)]}
    r = risk_metrics(per_symbol, capital_base=10000.0, trading_days=365)
    # 10% total return over 365 days -> exactly 10% annualised.
    assert r.cagr_pct == pytest.approx(10.0, abs=0.01)
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
```

Expected: FAIL — `risk_metrics` does not exist.

- [ ] **Step 3: Implement**

Append to `metrics.py`:

```python
# Trading days in a year, the conventional annualisation factor for a daily
# return series. NSE trades ~250; 252 is the standard constant and keeps our
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

    They are never mixed, and a null means "not measurable from this data" —
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
        # Every day in the window, not only days that traded — a flat day is a
        # real observation of this strategy's behaviour.
        returns = [daily.get(d, 0.0) / capital_base for d in _window_days(daily, trading_days)]
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
            years = trading_days / TRADING_DAYS_PER_YEAR
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


def _window_days(daily: dict[Any, float], trading_days: int) -> list[Any]:
    """The date keys to build a return series over.

    Uses the observed trading dates, padded with zero-P&L days up to
    `trading_days`, so a strategy that traded on 3 of 250 days is measured
    across 250 — its flat days are part of what it did.
    """
    observed = sorted(daily)
    if len(observed) >= trading_days:
        return observed
    # Pad with placeholder keys that are absent from `daily`, so they read 0.0.
    return observed + [f"__flat_{i}" for i in range(trading_days - len(observed))]
```

- [ ] **Step 4: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS. If `test_sharpe_daily_matches_a_hand_computed_value` fails, check whether `trading_days=3` produced exactly the three observed days rather than padding — the hand computation assumes three returns.

- [ ] **Step 5: Commit**

```bash
git add metrics.py tests/test_metrics.py
git commit -m "feat(metrics): risk-adjusted measures on two separated bases

A Sharpe computed on trade returns is not comparable to any published Sharpe
and rewards trading more often, so the daily basis (Sharpe/Sortino/CAGR) and
the trade basis (expectancy/SQN) are computed separately and never mixed.

Every expected value is hand-computed in the test rather than copied from the
implementation, because a metric test that asserts what the code happens to
produce proves only that the code is deterministic.

Null means not measurable, never zero: one trading day, zero variance, no
losing days. An absent measurement reading as a measured zero is the quiet
kind of wrong this codebase refuses everywhere else.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Proportional symbol-robustness kill rule

**Files:**
- Modify: `metrics.py`
- Test: `tests/test_metrics.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_metrics.py`:

```python
from metrics import MIN_PROFITABLE_SYMBOL_PCT, evaluate_kill_rules  # noqa: E402
from metrics import ComboMetrics  # noqa: E402


def combo(net_pnl=100.0, trades=50, dd=5.0) -> ComboMetrics:
    return ComboMetrics(
        total_trades=trades, winning_trades=trades // 2, net_pnl=net_pnl,
        win_rate_pct=50.0, profit_factor=1.5, max_drawdown_pct=dd,
        longest_losing_streak=3,
    )


def test_symbol_robustness_is_a_proportion_not_a_fixed_count():
    """3 of 50 used to pass. It is a 6% bar and would clear a strategy losing
    money on 47 of 50 stocks — exactly the curve-fitting the rule exists to
    catch."""
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=3, total_symbols=50)
    assert flags["symbol_robustness"]["passed"] is False


@pytest.mark.parametrize(
    "profitable,total,expected",
    [
        (20, 50, True),    # 40% exactly — the boundary passes
        (19, 50, False),   # 38%
        (21, 50, True),    # 42%
        (2, 5, True),      # 40% of a small universe
        (1, 5, False),     # 20%
    ],
)
def test_the_threshold_holds_at_its_boundaries(profitable, total, expected):
    _, flags = evaluate_kill_rules(combo(), profitable, total)
    assert flags["symbol_robustness"]["passed"] is expected


def test_the_flag_reports_both_the_percentage_and_the_counts():
    """The dashboard shows WHY something failed, so the numbers must be there."""
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=10, total_symbols=50)
    rule = flags["symbol_robustness"]
    assert rule["required_pct"] == MIN_PROFITABLE_SYMBOL_PCT
    assert rule["actual_pct"] == pytest.approx(20.0)
    assert rule["actual_profitable_symbols"] == 10
    assert rule["total_symbols"] == 50


def test_a_single_symbol_strategy_must_be_profitable_on_it():
    _, flags = evaluate_kill_rules(combo(), profitable_symbols=1, total_symbols=1)
    assert flags["symbol_robustness"]["passed"] is True
    _, flags = evaluate_kill_rules(combo(net_pnl=-10.0), 0, 1)
    assert flags["symbol_robustness"]["passed"] is False
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_metrics.py -q
```

Expected: FAIL — `MIN_PROFITABLE_SYMBOL_PCT` does not exist and the flag still reports a fixed count.

- [ ] **Step 3: Replace the constant and the rule**

In `metrics.py`, delete `MIN_PROFITABLE_SYMBOLS` and add:

```python
# Profitable on at least this share of the symbols actually traded.
#
# Replaces a fixed count of 3, which was written when strategies named five
# symbols by hand — a 60% bar. Applied unchanged to NIFTY50 it becomes 3 of 50:
# a 6% bar that would pass a strategy losing money on 47 stocks, which is
# precisely the curve-fitting the rule exists to catch.
#
# 40% is a judgement call, recorded as one. It preserves roughly what the old
# rule meant on a small universe while scaling honestly to 50 or 500.
MIN_PROFITABLE_SYMBOL_PCT = 40.0
```

Replace the `symbol_robustness` entry in `evaluate_kill_rules`:

```python
    actual_pct = (
        100.0 * profitable_symbols / total_symbols if total_symbols else 0.0
    )
    flags["symbol_robustness"] = {
        "required_pct": MIN_PROFITABLE_SYMBOL_PCT,
        "actual_pct": round(actual_pct, 2),
        "actual_profitable_symbols": profitable_symbols,
        "total_symbols": total_symbols,
        "passed": actual_pct >= MIN_PROFITABLE_SYMBOL_PCT,
    }
```

- [ ] **Step 4: Fix every consumer of the old constant**

```bash
grep -rn "MIN_PROFITABLE_SYMBOLS" --include=*.py .
```

Update each hit. `tests/test_backtest.py` asserts on the old flag shape — retarget those assertions at `required_pct` / `actual_pct` rather than deleting them.

- [ ] **Step 5: Run the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add metrics.py tests/
git commit -m "feat(metrics): scale symbol robustness to a proportion

A fixed count of 3 was a 60% bar across five hand-picked symbols. On NIFTY50
it is a 6% bar that would pass a strategy losing money on 47 of 50 stocks —
exactly the curve-fitting the rule was written to catch.

40% is a judgement call and is recorded as one. The flag now reports the
percentage and both counts, so the dashboard can still say why something
failed rather than only that it did.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Resolve universes inside the engine

The bug this phase exists for.

**Files:**
- Modify: `backtest.py:512` (the `all_instruments` line and the per-strategy loop)
- Test: `tests/test_backtest_universe.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_universe.py`:

```python
"""Universe resolution inside the backtest engine.

Before this, backtest.py iterated strategy.instruments — empty for a universe
strategy — so `universe: NIFTY50` produced zero combinations and reported
success. A silent nothing that looks like a clean result is the failure mode
every other layer of this system refuses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import resolve_strategy_symbols  # noqa: E402
from strategy_schema import parse_strategies  # noqa: E402
from universes import UniverseError  # noqa: E402


def strategy_with(**overrides):
    doc = {
        "name": "u-test", "enabled": True, "position_type": "long",
        "timeframe": "15m",
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]},
        "risk": {"stop_loss": {"type": "percent", "value": 1.0},
                 "target": {"type": "percent", "value": 2.0}},
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }
    doc.update(overrides)
    return parse_strategies({"version": 2, "strategies": [doc]})[0]


class FakeGroups:
    def __init__(self, members):
        self._members = members

    def universe_members(self, name):
        if name not in self._members:
            raise UniverseError(
                f"unknown universe {name!r}. Available: "
                + ", ".join(sorted(self._members))
            )
        return self._members[name]


def test_an_instruments_strategy_is_unchanged():
    s = strategy_with(instruments=["NSE:TCS", "NSE:INFY"])
    resolved = resolve_strategy_symbols(s, FakeGroups({}))
    assert resolved.symbols == ("NSE:INFY", "NSE:TCS")
    assert resolved.universe_name is None
    assert resolved.constituents_as_of is None


def test_a_universe_strategy_resolves_to_its_members():
    s = strategy_with(universe="NIFTY3")
    groups = FakeGroups({"NIFTY3": (("NSE:A", "NSE:B", "NSE:C"), "2026-08-11")})
    resolved = resolve_strategy_symbols(s, groups)
    assert resolved.symbols == ("NSE:A", "NSE:B", "NSE:C")
    assert resolved.universe_name == "NIFTY3"
    assert resolved.constituents_as_of == "2026-08-11"


def test_an_unknown_universe_is_a_hard_error_naming_what_exists():
    s = strategy_with(universe="NIFTY_NOPE")
    groups = FakeGroups({"NIFTY50": ((), "2026-08-11")})
    with pytest.raises(UniverseError) as exc:
        resolve_strategy_symbols(s, groups)
    assert "NIFTY_NOPE" in str(exc.value)
    assert "NIFTY50" in str(exc.value)


def test_an_empty_universe_is_a_hard_error_not_an_empty_result():
    """A zero-symbol run would report success having tested nothing."""
    s = strategy_with(universe="EMPTY")
    groups = FakeGroups({"EMPTY": ((), "2026-08-11")})
    with pytest.raises(UniverseError) as exc:
        resolve_strategy_symbols(s, groups)
    assert "EMPTY" in str(exc.value)
    assert "zero" in str(exc.value).lower() or "no symbols" in str(exc.value).lower()
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest_universe.py -q
```

Expected: FAIL — `resolve_strategy_symbols` does not exist.

- [ ] **Step 3: Implement in `backtest.py`**

Add above `run_backtest`:

```python
@dataclass(frozen=True)
class ResolvedSymbols:
    """What a strategy will actually be tested over.

    Carries the universe name and the constituent-list date so the run record
    can state what the result was computed over and how old that membership
    was — index membership is TODAY's applied to past data, and that
    survivorship caveat has to travel with the number.
    """

    symbols: tuple[str, ...]
    universe_name: str | None
    constituents_as_of: str | None


def resolve_strategy_symbols(strategy: Strategy, groups: Any) -> ResolvedSymbols:
    """Turn a strategy's universe or instrument list into symbols to test.

    A universe resolving to nothing is a HARD ERROR. Returning an empty list
    would produce a run that reports success having tested no stocks at all —
    indistinguishable from a strategy that simply found no signals.
    """
    if not strategy.universe:
        return ResolvedSymbols(
            symbols=tuple(sorted(strategy.instruments)),
            universe_name=None,
            constituents_as_of=None,
        )

    symbols, as_of = groups.universe_members(strategy.universe)
    if not symbols:
        raise UniverseError(
            f"strategy {strategy.name!r} uses universe "
            f"{strategy.universe!r}, which resolved to zero symbols. Refusing "
            "to run: the result would report success having tested nothing. "
            "Populate it with scripts/refresh_universes.py."
        )
    return ResolvedSymbols(
        symbols=tuple(sorted(symbols)),
        universe_name=strategy.universe,
        constituents_as_of=as_of,
    )
```

Add `from universes import UniverseError` to `backtest.py`'s imports.

- [ ] **Step 4: Add `universe_members` to the Supabase store**

In `db.py`, beside `known_universe_names`:

```python
    def universe_members(self, name: str) -> tuple[tuple[str, ...], str | None]:
        """(symbols, constituents_as_of) for a named universe.

        Paged, like known_symbols: PostgREST caps an unpaged select at 1000
        rows and NIFTY500 has more members than that would return, which would
        silently shrink the universe being tested.
        """
        try:
            groups = (
                self._table("symbol_groups")
                .select("id,constituents_as_of")
                .eq("name", name)
                .execute()
            )
        except APIError as exc:
            raise self._wrap(exc, f"reading universe {name}") from exc
        if not groups.data:
            known = ", ".join(sorted(self.known_universe_names())) or "(none)"
            raise UniverseError(
                f"unknown universe {name!r}. Available: {known}. "
                "Create it with scripts/refresh_universes.py."
            )
        group_id = groups.data[0]["id"]
        as_of = groups.data[0].get("constituents_as_of")

        instrument_ids: list[int] = []
        start = 0
        while True:
            page = (
                self._table("symbol_group_members")
                .select("instrument_id")
                .eq("group_id", group_id)
                .order("instrument_id")
                .range(start, start + 999)
                .execute()
            ).data
            instrument_ids += [r["instrument_id"] for r in page]
            if len(page) < 1000:
                break
            start += 1000

        symbols: list[str] = []
        for i in range(0, len(instrument_ids), 200):
            chunk = instrument_ids[i:i + 200]
            rows = (
                self._table("instruments").select("symbol").in_("id", chunk).execute()
            ).data
            symbols += [r["symbol"] for r in rows]
        return tuple(sorted(symbols)), as_of
```

Add `from universes import UniverseError` to `db.py`'s imports.

- [ ] **Step 5: Use it in `run_backtest`**

Replace `backtest.py:512`:

```python
    all_instruments = sorted({i for s in strategies for i in s.instruments})
```

with resolution up front, so a bad universe fails before any fetching:

```python
    resolved_by_strategy = {
        s.name: resolve_strategy_symbols(s, store) for s in strategies
    }
    all_instruments = sorted(
        {sym for r in resolved_by_strategy.values() for sym in r.symbols}
    )
```

Then inside the per-strategy loop, replace `for instrument in strategy.instruments:` with:

```python
        resolved = resolved_by_strategy[strategy.name]
        if resolved.universe_name:
            print(
                f"  universe {resolved.universe_name}: {len(resolved.symbols)} "
                f"symbols (list dated {resolved.constituents_as_of})"
            )
        for instrument in resolved.symbols:
```

and `len(strategy.instruments)` in the kill-rule call with `len(resolved.symbols)`.

> `run_backtest` takes `store: SupabaseStore | None`. With `--no-db` it is
> `None`, so a universe strategy cannot resolve. Guard it: if `store is None`
> and any strategy uses a universe, raise a clear error saying `--no-db`
> cannot resolve universes and to drop the flag.

- [ ] **Step 6: Run the tests, then the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest_universe.py -q
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: both PASS.

- [ ] **Step 7: Verify against real data**

All 50 NIFTY50 symbols have 2 years of cached 5-minute candles. Create a scratch strategy using `universe: NIFTY50` at `60m` (resampled from the 5m base), save it, and run:

```bash
PYTHONIOENCODING=utf-8 .\.venv\Scripts\python.exe backtest.py --years 2
```

Expected: 50 per-symbol lines, not zero. Record the count in your report.

- [ ] **Step 8: Commit**

```bash
git add backtest.py db.py tests/test_backtest_universe.py
git commit -m "fix(backtest): resolve universes instead of silently testing nothing

backtest.py iterated strategy.instruments, which is empty for a universe
strategy, so \`universe: NIFTY50\` produced zero combinations and reported
success. Everything Phase 1 built was unreachable from the engine, and it
failed as a silent nothing rather than a loud error.

A universe resolving to zero symbols is now a hard error: an empty result
would report success having tested no stocks, which is indistinguishable from
a strategy that found no signals.

universe_members is paged — PostgREST caps an unpaged select at 1000 rows and
NIFTY500 has more members than that, which would have silently shrunk the
universe being tested.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: The `backtest_runs` table

**Files:**
- Create: `sql/004_backtest_runs.sql`
- Test: applied to Supabase, verified by query

- [ ] **Step 1: Create the migration**

Copy the `create table backtest_runs (...)` statement, its indexes and its three `comment on` statements verbatim from §4 of `docs/superpowers/specs/2026-08-11-phase2-batch-backtest-metrics-design.md`, then append the RLS block matching `backtest_results`:

```sql
alter table backtest_runs enable row level security;
drop policy if exists "anon read backtest_runs" on backtest_runs;
create policy "anon read backtest_runs" on backtest_runs for select to anon using (true);
```

- [ ] **Step 2: Apply it**

Paste into the Supabase SQL editor and run. (The Python client cannot execute DDL.)

- [ ] **Step 3: Verify the columns exist**

```bash
.\.venv\Scripts\python.exe -c "from config import get_settings; from db import SupabaseStore; c=SupabaseStore.connect(get_settings())._client; print(c.table('backtest_runs').select('id,batch_id,strategy_name,universe_name,constituents_as_of,sharpe_daily,symbols_profitable').limit(1).execute().data)"
```

Expected: `[]` with no column error.

- [ ] **Step 4: Commit**

```bash
git add sql/004_backtest_runs.sql
git commit -m "feat(db): add backtest_runs for the strategy-level verdict

Per-symbol results and a strategy verdict are different grains. Putting both
in backtest_results with a null instrument would make every query responsible
for filtering it, and a forgotten filter double-counts P&L — silent wrongness
rather than a loud failure.

It is also where the survivorship caveat belongs: the resolved symbol list and
the constituent-list date live on the run, so a result can always state what it
was computed over and how old that membership was.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Assemble and persist the run row

**Files:**
- Modify: `backtest.py` (`run_backtest` return shape), `db.py`
- Test: `tests/test_backtest_universe.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backtest_universe.py`:

```python
from backtest import build_run_row  # noqa: E402


def test_the_run_row_records_what_it_was_computed_over():
    """Without this a result cannot be reproduced and its survivorship caveat
    has nowhere to live."""
    import uuid

    from backtest_types import SimTrade

    resolved = type("R", (), {
        "symbols": ("NSE:A", "NSE:B"), "universe_name": "NIFTY2",
        "constituents_as_of": "2026-08-11",
    })()
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(universe="NIFTY2"),
        resolved=resolved,
        per_symbol={"NSE:A": [], "NSE:B": []},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["universe_name"] == "NIFTY2"
    assert row["constituents_as_of"] == "2026-08-11"
    assert row["symbols"] == ["NSE:A", "NSE:B"]
    assert row["symbols_requested"] == 2
    assert row["symbols_resolved"] == 2


def test_a_run_with_no_trades_is_still_recorded():
    """A strategy that traded nothing is a result, and must be visible rather
    than absent."""
    import uuid

    resolved = type("R", (), {
        "symbols": ("NSE:A",), "universe_name": None, "constituents_as_of": None,
    })()
    row = build_run_row(
        batch_id=uuid.uuid4(), strategy=strategy_with(instruments=["NSE:A"]),
        resolved=resolved, per_symbol={"NSE:A": []},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["total_trades"] == 0
    assert row["passed_kill_rules"] is False
    assert row["sharpe_daily"] is None
```

- [ ] **Step 2: Run to verify they fail**

```bash
.\.venv\Scripts\python.exe -m pytest tests/test_backtest_universe.py -q
```

Expected: FAIL — `build_run_row` does not exist.

- [ ] **Step 3: Implement `build_run_row` in `backtest.py`**

```python
def build_run_row(
    *,
    batch_id: uuid.UUID,
    strategy: Strategy,
    resolved: Any,
    per_symbol: dict[str, list[SimTrade]],
    start_date: str,
    end_date: str,
    trading_days: int,
    entries_skipped: int,
    missing: set[str],
) -> dict[str, Any]:
    """One strategy-level row: the pooled verdict plus how it was distributed.

    Capital base is notional_per_trade x symbols traded — the worst case under
    per-symbol independent sizing, since any symbol could hold a position at
    any time. It biases the daily Sharpe LOW rather than flattering, which is
    the correct direction for a number a deploy decision rests on.
    """
    symbols = list(resolved.symbols)
    if strategy.sizing.type == "notional":
        capital_base = float(strategy.sizing.notional_per_trade) * max(len(symbols), 1)
    else:
        all_trades = [t for ts in per_symbol.values() for t in ts]
        capital_base = (
            max(t.entry_price * t.quantity for t in all_trades) if all_trades else 0.0
        )

    pooled = pooled_metrics(per_symbol, capital_base=capital_base)
    spread = dispersion_metrics(per_symbol)
    risk = risk_metrics(
        per_symbol, capital_base=capital_base, trading_days=trading_days
    )

    combo = ComboMetrics(
        total_trades=pooled.total_trades,
        winning_trades=pooled.winning_trades,
        net_pnl=pooled.net_pnl,
        win_rate_pct=pooled.win_rate_pct,
        profit_factor=pooled.profit_factor,
        max_drawdown_pct=pooled.max_drawdown_pct,
        longest_losing_streak=pooled.longest_losing_streak,
    )
    passed, flags = evaluate_kill_rules(
        combo, spread.symbols_profitable, spread.symbols_traded
    )

    return {
        "batch_id": str(batch_id),
        "strategy_name": strategy.name,
        "timeframe": strategy.timeframe,
        "start_date": start_date,
        "end_date": end_date,
        "universe_name": resolved.universe_name,
        "constituents_as_of": resolved.constituents_as_of,
        # Requested is what the universe listed; resolved is what actually
        # produced candles. They differ when a symbol has no cached history,
        # and reporting only the resolved count would quietly present a
        # 47-symbol result as a NIFTY50 one.
        "symbols_requested": len(symbols),
        "symbols_resolved": len(symbols) - len(missing),
        "symbols": [s for s in symbols if s not in missing],
        "symbols_missing": sorted(missing),
        "total_trades": pooled.total_trades,
        "winning_trades": pooled.winning_trades,
        "net_pnl": pooled.net_pnl,
        "win_rate_pct": pooled.win_rate_pct,
        "profit_factor": pooled.profit_factor,
        "max_drawdown_pct": pooled.max_drawdown_pct,
        "longest_losing_streak": pooled.longest_losing_streak,
        "entries_skipped": entries_skipped,
        "symbols_profitable": spread.symbols_profitable,
        "median_symbol_pnl": spread.median_symbol_pnl,
        "best_symbol": spread.best_symbol,
        "best_symbol_pnl": spread.best_symbol_pnl,
        "worst_symbol": spread.worst_symbol,
        "worst_symbol_pnl": spread.worst_symbol_pnl,
        "capital_base": round(capital_base, 4),
        "sharpe_daily": risk.sharpe_daily,
        "sortino_daily": risk.sortino_daily,
        "cagr_pct": risk.cagr_pct,
        "expectancy_per_trade": risk.expectancy_per_trade,
        "system_quality_number": risk.system_quality_number,
        "passed_kill_rules": passed,
        "kill_rule_flags": flags,
    }
```

Import `dispersion_metrics`, `pooled_metrics`, `risk_metrics` from `metrics`.

- [ ] **Step 4: Wire it into `run_backtest`**

`run_backtest` currently returns `(batch_id, rows)`. Change it to
`(batch_id, rows, run_rows)`, appending one `build_run_row(...)` per strategy
after the per-symbol loop.

Collect three things inside that loop:

* `trading_days` — the count of distinct IST dates across the fetched frames
* `entries_skipped` — the sum of `len(result.skipped)`
* `missing` — a `set[str]` of symbols that produced no result. The existing
  loop already `continue`s on a fetch failure and on an empty frame; add the
  symbol to `missing` at both points, and also on the `RiskLevelError` path so
  a symbol whose ATR period outran its history is reported rather than
  vanishing. Pass it as `missing=missing`.

Update `main()` to insert run rows when a store is present:

```python
        store.insert_backtest_runs(run_rows)
```

and print the strategy verdict:

```python
        print(
            f"  VERDICT  net=₹{row['net_pnl']:,.0f}  "
            f"profitable on {row['symbols_profitable']}/{row['symbols_resolved']} symbols  "
            f"sharpe={row['sharpe_daily'] if row['sharpe_daily'] is not None else 'n/a'}  "
            f"{'PASSED' if row['passed_kill_rules'] else 'FAILED'} kill rules"
        )
```

- [ ] **Step 5: Add `insert_backtest_runs` to `db.py`**

Beside `insert_backtest_results`:

```python
    def insert_backtest_runs(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Bulk-insert strategy-level run rows (shaped by backtest.py)."""
        payload = [dict(r) for r in rows]
        if not payload:
            return 0
        try:
            self._table("backtest_runs").insert(payload).execute()
        except APIError as exc:
            raise self._wrap(exc, "inserting backtest runs") from exc
        return len(payload)
```

- [ ] **Step 6: Run the suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

Expected: PASS. Any test calling `run_backtest` and unpacking two values needs the third.

- [ ] **Step 7: Verify end to end against real data**

```bash
PYTHONIOENCODING=utf-8 .\.venv\Scripts\python.exe backtest.py --years 2
```

Then confirm a run row landed:

```bash
.\.venv\Scripts\python.exe -c "from config import get_settings; from db import SupabaseStore; c=SupabaseStore.connect(get_settings())._client; print(c.table('backtest_runs').select('strategy_name,universe_name,symbols_resolved,symbols_profitable,net_pnl,sharpe_daily,passed_kill_rules').order('created_at',desc=True).limit(3).execute().data)"
```

Report what it shows.

- [ ] **Step 8: Commit**

```bash
git add backtest.py db.py tests/
git commit -m "feat(backtest): write a strategy-level run row

Fifty per-symbol rows are not an answer to 'does this edge hold broadly'. The
run row pairs the pooled headline with its dispersion, so a strategy carried by
one outlier is visibly different from a broadly sound one.

Capital base is notional x symbols traded: the worst case under per-symbol
independent sizing, biasing Sharpe low rather than flattering — the correct
direction for a number a deploy decision rests on.

A run with no trades is still written. A strategy that traded nothing is a
result and must be visible rather than absent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Final verification

- [ ] **Full suite**

```bash
.\.venv\Scripts\python.exe -m pytest tests/ -q
```

- [ ] **A real NIFTY50 run, end to end**

```bash
PYTHONIOENCODING=utf-8 .\.venv\Scripts\python.exe backtest.py --years 2
```

Expected: 50 per-symbol lines, one VERDICT line, one `backtest_runs` row.

- [ ] **An `instruments:` strategy still behaves as before** — run one and compare its per-symbol numbers against a run from before this phase.

- [ ] **Walk §9 of the spec** and confirm each box.

---

## Self-review notes

Checked against the spec:

- §1.1 universes unreachable → Task 6
- §1.2 no verdict → Tasks 3, 8
- §1.3 no risk-adjusted metrics → Task 4
- §1.4 robustness rule → Task 5
- §2.1 loud resolution → Task 6
- §2.2 pooled + dispersion → Tasks 2, 3
- §2.3 two bases, capital base → Tasks 4, 8
- §2.4 proportional rule → Task 5
- §2.5 separate table → Tasks 7, 8
- §2.6 pure metrics module → Task 1
- §4 data model → Task 7
- §5 metric definitions → Tasks 2, 3, 4
- §6 error handling → Task 6 (universe cases); per-symbol fetch failures already skip with a warning in existing code
- §7 testing → each task's tests

**§6's `symbols_missing` row is fully covered.** An earlier draft of this plan
wrote `symbols_missing: []` unconditionally and left a note suggesting the
executor wire it up. That would have shipped a column that is always empty
while claiming to report missing symbols — precisely the silent wrongness this
phase exists to remove, and a note in a plan is not a substitute for the code.
Task 8 now threads the skipped-symbol set through `build_run_row`, and
`symbols_resolved` counts only symbols that actually produced candles, so a
47-symbol result can never present itself as a NIFTY50 one.
