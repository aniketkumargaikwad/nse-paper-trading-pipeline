"""One strategy across many combinations, serially or in worker processes."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import HoldingCostModel  # noqa: E402
from parquet_candle_backend import ParquetCandleBackend  # noqa: E402
from research.prices import FrozenPriceReader  # noqa: E402
from research.sweep import ComboResult, count_results, run_combo, run_sweep  # noqa: E402
from research.universe import Combo  # noqa: E402
from strategy_schema import parse_strategy_dict  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
CLOSES = [100, 102, 103, 100, 102, 104, 99, 98, 102, 103, 99]


def daily_frame() -> pd.DataFrame:
    start = datetime(2026, 8, 3, 0, 0, tzinfo=IST)
    idx = pd.DatetimeIndex([(start + timedelta(days=i)).astimezone(UTC) for i in range(len(CLOSES))])
    return pd.DataFrame(
        {"open": [c - 0.5 for c in CLOSES], "high": [c + 1 for c in CLOSES],
         "low": [c - 1 for c in CLOSES], "close": [float(c) for c in CLOSES],
         "volume": [1000.0] * len(CLOSES)},
        index=idx,
    )


def strategy():
    return parse_strategy_dict({
        "name": "sweep-test", "enabled": False, "position_type": "long", "timeframe": "day",
        "instruments": ["NSE:ABC"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 101}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 101}]},
        "risk": {"stop_loss": {"type": "percent", "value": 20},
                 "target": {"type": "percent", "value": 50}},
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 1,
    })


def reader(tmp_path) -> FrozenPriceReader:
    backend = ParquetCandleBackend(None, str(tmp_path))
    backend.write_candles(1, "day", daily_frame())
    backend.write_candles(2, "day", daily_frame())
    return FrozenPriceReader(str(tmp_path), {"NSE:ABC": 1, "NSE:DEF": 2, "NSE:EMPTY": 3}, {}, frozenset())


def run(combo, rdr):
    return run_combo(combo, strategy(), rdr, WINDOW, slippage_pct=0.05, cost_model=HoldingCostModel())


def test_a_stored_combination_produces_trades(tmp_path):
    result = run(Combo("NSE:ABC", "day", False), reader(tmp_path))
    assert result.skipped_reason is None
    assert len(result.trades) >= 1
    assert result.first_candle == daily_frame().index[0].to_pydatetime()


def test_no_candles_is_a_skip_not_an_error(tmp_path):
    result = run(Combo("NSE:EMPTY", "day", False), reader(tmp_path))
    assert result.skipped_reason == "no candles in window"
    assert result.trades == ()
    assert result.first_candle is None


def test_an_unreadable_combination_is_a_skip_with_the_reason(tmp_path):
    result = run(Combo("NSE:UNKNOWN", "day", False), reader(tmp_path))
    assert result.skipped_reason.startswith("price read failed")


def test_workers_give_the_same_results_as_serial(tmp_path):
    rdr = reader(tmp_path)
    combos = [Combo("NSE:ABC", "day", False), Combo("NSE:DEF", "day", False),
              Combo("NSE:EMPTY", "day", False)]

    def go(workers):
        return run_sweep(strategy(), combos, rdr, lambda c: WINDOW,
                         slippage_pct=0.05, cost_model=HoldingCostModel(), workers=workers)

    assert go(1) == go(2)


def test_results_come_back_in_the_order_the_combinations_were_given(tmp_path):
    rdr = reader(tmp_path)
    combos = [Combo("NSE:DEF", "day", False), Combo("NSE:EMPTY", "day", False),
              Combo("NSE:ABC", "day", False)]
    got = run_sweep(strategy(), combos, rdr, lambda c: WINDOW,
                    slippage_pct=0.05, cost_model=HoldingCostModel(), workers=2)
    assert [r.symbol for r in got] == ["NSE:DEF", "NSE:EMPTY", "NSE:ABC"]


def test_counts_separate_skipped_tested_and_profitable(tmp_path):
    some_trade = run(Combo("NSE:ABC", "day", False), reader(tmp_path)).trades[0]
    results = [
        ComboResult("A", "day", False, (replace(some_trade, net_pnl=500.0),)),
        ComboResult("B", "day", False, (replace(some_trade, net_pnl=-500.0),)),
        ComboResult("C", "day", False, (), "no candles in window"),
    ]
    counts = count_results(results)
    assert (counts.tested, counts.skipped, counts.profitable) == (2, 1, 1)
