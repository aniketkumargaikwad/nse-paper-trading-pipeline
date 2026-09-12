"""Run one strategy over many stock x timeframe combinations.

Each combination is independent - one position at a time per symbol - so the
work splits cleanly across processes. Workers receive the strategy, the price
reader and the fee model once, at start-up; each task carries only a Combo and
its window. Results come back in the order the combinations were given.

A combination that cannot be read or simulated becomes a skip with a reason,
never an exception that ends the run: one young stock with too little history
for an ATR must not cost the other 1,212 results.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backtest import simulate_any
from backtest_types import SimTrade
from research.prices import FrozenPriceReader
from research.universe import Combo

Window = tuple[datetime, datetime]


@dataclass(frozen=True)
class ComboResult:
    symbol: str
    timeframe: str
    is_index: bool
    trades: tuple[SimTrade, ...]
    skipped_reason: str | None = None
    # When this combination's candles actually begin inside the window. A
    # stock listed in 2024 has far fewer training days than the window allows,
    # and annualising its return over the whole window would understate it.
    first_candle: datetime | None = None

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)


@dataclass(frozen=True)
class SweepCounts:
    tested: int
    skipped: int
    profitable: int


def run_combo(
    combo: Combo,
    strategy: Any,
    reader: FrozenPriceReader,
    window: Window,
    *,
    slippage_pct: float,
    cost_model: Any,
) -> ComboResult:
    def skip(reason: str) -> ComboResult:
        return ComboResult(combo.symbol, combo.timeframe, combo.is_index, (), reason)

    try:
        frame = reader.candles(combo.symbol, combo.timeframe, *window)
    except Exception as exc:        # noqa: BLE001 - becomes a recorded skip
        return skip(f"price read failed: {exc!r}")
    if len(frame) < 2:
        return skip("no candles in window")
    try:
        result = simulate_any(
            frame, strategy,
            slippage_pct=slippage_pct, cost_per_trade_inr=0.0, cost_model=cost_model,
        )
    except Exception as exc:        # noqa: BLE001 - becomes a recorded skip
        return skip(f"simulation failed: {exc!r}")
    return ComboResult(
        combo.symbol, combo.timeframe, combo.is_index, tuple(result.trades),
        first_candle=frame.index[0].to_pydatetime(),
    )


_WORKER: dict[str, Any] = {}


def _init_worker(strategy: Any, reader: FrozenPriceReader, slippage_pct: float, cost_model: Any) -> None:
    _WORKER.update(strategy=strategy, reader=reader, slippage_pct=slippage_pct, cost_model=cost_model)


def _run_task(task: tuple[Combo, Window]) -> ComboResult:
    combo, window = task
    return run_combo(
        combo, _WORKER["strategy"], _WORKER["reader"], window,
        slippage_pct=_WORKER["slippage_pct"], cost_model=_WORKER["cost_model"],
    )


def run_sweep(
    strategy: Any,
    combos: Sequence[Combo],
    reader: FrozenPriceReader,
    window_for: Callable[[Combo], Window],
    *,
    slippage_pct: float,
    cost_model: Any,
    workers: int,
) -> list[ComboResult]:
    tasks = [(combo, window_for(combo)) for combo in combos]
    if workers <= 1:
        _init_worker(strategy, reader, slippage_pct, cost_model)
        return [_run_task(task) for task in tasks]
    # spawn: the only start method on Windows, and the same everywhere else,
    # so a run behaves identically on the laptop and on a Linux runner.
    context = multiprocessing.get_context("spawn")
    with context.Pool(
        workers, initializer=_init_worker,
        initargs=(strategy, reader, slippage_pct, cost_model),
    ) as pool:
        return pool.map(_run_task, tasks, chunksize=4)


def count_results(results: Sequence[ComboResult]) -> SweepCounts:
    tested = [r for r in results if r.skipped_reason is None]
    return SweepCounts(
        tested=len(tested),
        skipped=len(results) - len(tested),
        profitable=sum(1 for r in tested if r.net_pnl > 0),
    )
