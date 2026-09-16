"""A signal that fires before its ATR stop can be priced is skipped, not fatal.

RSI(2) is ready on bar 3; ATR(14) on bar 15. Before 16 September 2026 the
engine raised on that gap and the WHOLE combination was lost - eight years of
history thrown away for its first two weeks. Now the early entries are
recorded as skips and the rest of the history stands.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import simulate_with_skips  # noqa: E402
from machine_backtest import simulate_machine  # noqa: E402
from risk_levels import WARMING_UP, RiskLevelError, atr_ready  # noqa: E402
from strategy.v3 import parse_machine  # noqa: E402
from strategy_schema import parse_strategy_dict  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def rising_days(n: int) -> pd.DataFrame:
    start = datetime(2024, 1, 1, 0, 0, tzinfo=IST)
    closes = [100.0 + i for i in range(n)]
    idx = pd.DatetimeIndex([(start + timedelta(days=i)).astimezone(UTC) for i in range(n)])
    return pd.DataFrame(
        {"open": [c - 0.5 for c in closes], "high": [c + 1 for c in closes],
         "low": [c - 1 for c in closes], "close": closes, "volume": [1000.0] * n},
        index=idx,
    )


ATR_RISK = {"stop_loss": {"type": "atr", "period": 14, "multiplier": 2.0},
            "target": {"type": "atr", "period": 14, "multiplier": 6.0}}


def v2_strategy():
    """Enters on the very first bar: close is always above zero."""
    return parse_strategy_dict({
        "name": "early", "enabled": False, "position_type": "long", "timeframe": "day",
        "instruments": ["NSE:A"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 0}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
        "risk": ATR_RISK,
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 1,
    })


def v3_machine(**risk):
    return parse_machine({
        "version": 3, "name": "early", "timeframe": "day", "instruments": ["NSE:A"],
        "initial": "flat",
        "states": [
            {"name": "flat", "transitions": [
                {"when": "close > 0", "enter": {"side": "long", **risk}, "goto": "holding"}]},
            {"name": "holding", "transitions": [
                {"when": "position.bars_held >= 3", "exit": {}, "goto": "flat"}]},
        ],
        "risk": ATR_RISK,
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 1,
    })


def test_v2_early_signals_are_skipped_and_later_ones_traded():
    result = simulate_with_skips(rising_days(60), v2_strategy(), slippage_pct=0.0, cost_per_trade_inr=0.0)
    early = [s for s in result.skipped if s.reason == WARMING_UP]
    assert early, "the first signals fire before ATR(14) exists"
    assert result.trades, "the history after warm-up is still traded"
    first = result.trades[0]
    assert first.entry_signal_ts > early[-1].signal_ts


def test_v3_early_signals_are_skipped_and_the_machine_is_not_stranded():
    result = simulate_machine(rising_days(60), v3_machine(), slippage_pct=0.0, cost_per_trade_inr=0.0)
    early = [s for s in result.skipped if s.reason == WARMING_UP]
    assert early
    assert len(result.trades) >= 5, "it keeps trading every few bars after warm-up"


def test_a_machine_that_names_its_own_levels_needs_no_atr():
    machine = v3_machine(stop="close * 0.98", target="close * 1.06")
    result = simulate_machine(rising_days(60), machine, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert not [s for s in result.skipped if s.reason == WARMING_UP]
    assert result.trades and result.trades[0].entry_signal_ts == rising_days(60).index[0]


def test_a_frame_shorter_than_the_atr_period_is_still_a_hard_error():
    with pytest.raises(RiskLevelError):
        simulate_with_skips(rising_days(10), v2_strategy(), slippage_pct=0.0, cost_per_trade_inr=0.0)


def test_atr_ready_reads_the_signal_bar():
    import numpy as np

    series = {14: np.array([np.nan, 0.0, 1.5])}
    spec = v2_strategy().risk.stop_loss
    assert atr_ready((spec, None), 0, series) is False
    assert atr_ready((spec,), 1, series) is False
    assert atr_ready((spec,), 2, series) is True
    assert atr_ready((None, v2_strategy().risk.target), 2, series) is True
