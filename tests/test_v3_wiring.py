"""A v3 machine has to travel the same road a v2 strategy does.

Being able to simulate a machine is not the same as being able to RUN one.
The batch runner resolves universes, scores metrics, applies kill rules and
writes rows — and if a machine cannot go through all of that, v3 is a library
rather than a feature.

These tests pin the seams where the two formats meet, because a mismatch
there fails as a TypeError deep in a batch run rather than at the boundary.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import resolve_strategy_symbols, simulate_any  # noqa: E402
from metrics import compute_metrics  # noqa: E402
from strategy.parse import parse_strategy_dict  # noqa: E402
from strategy.to_v3 import migrate_v2_to_v3  # noqa: E402
from strategy.v3 import parse_machine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
BARS_PER_SESSION = 25


def market(n: int, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.2, size=n))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    stamps = []
    start = datetime(2026, 3, 2, 9, 15, tzinfo=IST)
    for i in range(n):
        day, slot = divmod(i, BARS_PER_SESSION)
        stamps.append((start + timedelta(days=day, minutes=15 * slot)).astimezone(UTC))
    return pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + 0.6,
         "low": np.minimum(opens, closes) - 0.6, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=pd.DatetimeIndex(stamps, name="ts"),
    )


def v2_doc(**overrides) -> dict:
    doc = {
        "name": "wired", "enabled": True, "position_type": "long",
        "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
        "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                           "operator": "<", "value": 45}]},
        "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                          "operator": ">", "value": 55}]},
        "risk": {"stop_loss": {"type": "percent", "value": 1.5},
                 "target": {"type": "percent", "value": 2.5}},
        "sizing": {"type": "fixed_quantity", "quantity": 10},
    }
    doc.update(overrides)
    return doc


def a_machine(**overrides):
    return parse_machine(migrate_v2_to_v3(parse_strategy_dict(v2_doc(**overrides))))


# --- the dispatch seam ------------------------------------------------------


def test_simulate_any_routes_a_v2_strategy() -> None:
    strategy = parse_strategy_dict(v2_doc())
    result = simulate_any(market(400), strategy,
                          slippage_pct=0.05, cost_per_trade_inr=30.0)
    assert result.trades


def test_simulate_any_routes_a_v3_machine() -> None:
    result = simulate_any(market(400), a_machine(),
                          slippage_pct=0.05, cost_per_trade_inr=30.0)
    assert result.trades


def test_both_routes_agree() -> None:
    df = market(500, seed=4)
    params = {"slippage_pct": 0.05, "cost_per_trade_inr": 30.0}
    v2 = simulate_any(df, parse_strategy_dict(v2_doc()), **params)
    v3 = simulate_any(df, a_machine(), **params)
    assert [t.net_pnl for t in v2.trades] == pytest.approx(
        [t.net_pnl for t in v3.trades]
    )


# --- what the batch runner reads off a strategy -----------------------------


def test_a_machine_reports_its_symbols_like_a_strategy() -> None:
    resolved = resolve_strategy_symbols(a_machine(), None)
    assert resolved.symbols == ("NSE:RELIANCE",)


def test_a_machine_carries_a_universe_name() -> None:
    doc = v2_doc()
    del doc["instruments"]
    doc["universe"] = "NIFTY50"
    machine = parse_machine(migrate_v2_to_v3(parse_strategy_dict(doc)))
    assert machine.universe == "NIFTY50"
    assert machine.instruments == ()


def test_position_type_is_derived_from_the_entries() -> None:
    assert a_machine().position_type == "long"
    assert a_machine(position_type="short").position_type == "short"


def test_a_machine_that_enters_both_ways_says_both() -> None:
    """A v2 strategy declares one side; a machine can honestly do either, and
    claiming a single side would be a claim the document never made."""
    doc = migrate_v2_to_v3(parse_strategy_dict(v2_doc()))
    doc["states"][0]["on"].append({
        "when": "rsi(14) > 80", "enter": {"side": "short"}, "goto": "holding",
    })
    assert parse_machine(doc).position_type == "both"


def test_a_machine_carries_sizing_for_the_capital_base() -> None:
    machine = a_machine(sizing={"type": "notional", "notional_per_trade": 60000})
    assert machine.sizing.type == "notional"
    assert machine.sizing.notional_per_trade == 60000


def test_metrics_score_a_machine_run_unchanged() -> None:
    result = simulate_any(market(500), a_machine(),
                          slippage_pct=0.05, cost_per_trade_inr=30.0)
    metrics = compute_metrics(result.trades)
    assert metrics.total_trades == len(result.trades)
