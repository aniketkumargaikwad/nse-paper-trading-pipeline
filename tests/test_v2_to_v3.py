"""A migrated v2 strategy must trade IDENTICALLY through the v3 engine.

This is the strongest correctness evidence available for the whole v3 stack.
v2's evaluator carries hundreds of tests and every result already in the
database; if v3 reproduces it trade-for-trade — same bars, same fills, same
exit reasons — then the expression parser, the namespace, the state machine
and the machine simulator have all been checked against a known good answer
at once.

"Equivalent in spirit" is not the bar. Same trades, or the migration is wrong.
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

from backtest import simulate_with_skips  # noqa: E402
from machine_backtest import simulate_machine  # noqa: E402
from strategy.parse import parse_strategy_dict  # noqa: E402
from strategy.to_v3 import (  # noqa: E402
    MigrationToV3Error,
    condition_to_expression,
    group_to_expression,
    migrate_v2_to_v3,
    operand_to_expression,
)
from strategy.v3 import parse_machine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
BARS_PER_SESSION = 25


def wandering_prices(n: int, seed: int = 7) -> np.ndarray:
    """A deterministic random walk — varied enough to exercise every branch."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 1.2, size=n)
    return 100.0 + np.cumsum(steps)


def market(n: int, seed: int = 7) -> pd.DataFrame:
    closes = wandering_prices(n, seed)
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) + 0.6
    lows = np.minimum(opens, closes) - 0.6
    session_start = datetime(2026, 3, 2, 9, 15, tzinfo=IST)
    stamps = []
    for i in range(n):
        day, slot = divmod(i, BARS_PER_SESSION)
        stamps.append(
            (session_start + timedelta(days=day, minutes=15 * slot)).astimezone(UTC)
        )
    rng = np.random.default_rng(seed + 1)
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes,
         "volume": rng.uniform(800, 1200, size=n)},
        index=pd.DatetimeIndex(stamps, name="ts"),
    )


def v2_doc(entry, exit_, **overrides) -> dict:
    doc = {
        "name": "eq", "enabled": True, "position_type": "long",
        "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
        "entry": entry, "exit": exit_,
        "risk": {"stop_loss": {"type": "percent", "value": 1.5},
                 "target": {"type": "percent", "value": 2.5}},
        "sizing": {"type": "fixed_quantity", "quantity": 10},
        "max_cycles_per_day": 3,
    }
    doc.update(overrides)
    return doc


def assert_same_trades(document: dict, df: pd.DataFrame) -> int:
    """Run both engines on the same strategy and demand identical trades."""
    strategy = parse_strategy_dict(document)
    machine = parse_machine(migrate_v2_to_v3(strategy))

    params = {"slippage_pct": 0.05, "cost_per_trade_inr": 30.0}
    v2 = simulate_with_skips(df, strategy, **params)
    v3 = simulate_machine(df, machine, **params)

    assert len(v2.trades) == len(v3.trades), (
        f"v2 made {len(v2.trades)} trades, v3 made {len(v3.trades)}"
    )
    for a, b in zip(v2.trades, v3.trades):
        assert a.entry_fill_ts == b.entry_fill_ts, "entry bar differs"
        assert a.exit_fill_ts == b.exit_fill_ts, "exit bar differs"
        assert a.exit_reason == b.exit_reason, "exit reason differs"
        assert a.quantity == b.quantity
        assert a.entry_price == pytest.approx(b.entry_price)
        assert a.exit_price == pytest.approx(b.exit_price)
        assert a.net_pnl == pytest.approx(b.net_pnl)
    return len(v2.trades)


# --- translation of the pieces ----------------------------------------------


def operand_of(node: dict):
    doc = v2_doc({"all": [dict(node, operator=">", value=1)]},
                 {"any": [{"indicator": "close", "operator": "<", "value": 0}]})
    return parse_strategy_dict(doc).entry.items[0].left


def test_a_price_operand_translates() -> None:
    assert operand_to_expression(operand_of({"indicator": "close"}), "15m") == "close"


def test_an_indicator_operand_translates() -> None:
    assert operand_to_expression(
        operand_of({"indicator": "rsi", "params": {"period": 14}}), "15m"
    ) == "rsi(14)"


def test_a_sourced_moving_average_translates() -> None:
    """v2's `source: volume` becomes a first argument."""
    assert operand_to_expression(
        operand_of({"indicator": "sma", "params": {"period": 20}, "source": "volume"}),
        "15m",
    ) == "sma(volume, 20)"


def test_a_multi_output_indicator_translates() -> None:
    assert operand_to_expression(
        operand_of({"indicator": "macd",
                    "params": {"fast": 12, "slow": 26, "signal": 9},
                    "output": "signal"}),
        "15m",
    ) == "macd.signal(12, 26, 9)"


def test_an_offset_translates() -> None:
    assert operand_to_expression(
        operand_of({"indicator": "high", "offset": 2}), "15m"
    ) == "high[2]"


def test_a_daily_operand_translates() -> None:
    assert operand_to_expression(
        operand_of({"indicator": "high", "timeframe": "day"}), "15m"
    ) == "daily.high"


def test_an_unnameable_timeframe_is_refused_not_dropped() -> None:
    """Silently dropping a trend filter changes what the strategy trades
    while leaving what it says unchanged."""
    with pytest.raises(MigrationToV3Error) as exc:
        operand_to_expression(
            operand_of({"indicator": "high", "timeframe": "25m"}), "15m"
        )
    assert "25m" in str(exc.value)


def test_a_cross_becomes_two_comparisons() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "ema", "params": {"period": 9},
                  "operator": "crosses_above",
                  "compare_to": {"indicator": "ema", "params": {"period": 21}}}]},
        {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
    )
    cond = parse_strategy_dict(doc).entry.items[0]
    assert condition_to_expression(cond, "15m") == (
        "(ema(9)[1] <= ema(21)[1]) and (ema(9) > ema(21))"
    )


def test_nesting_is_preserved_with_brackets() -> None:
    doc = v2_doc(
        {"all": [
            {"indicator": "close", "operator": ">", "value": 1},
            {"any": [
                {"indicator": "close", "operator": ">", "value": 2},
                {"indicator": "close", "operator": ">", "value": 3},
            ]},
        ]},
        {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
    )
    group = parse_strategy_dict(doc).entry
    assert group_to_expression(group, "15m") == (
        "(close > 1) and ((close > 2) or (close > 3))"
    )


# --- the document -----------------------------------------------------------


def test_the_migrated_document_is_a_valid_machine() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 55}]},
        {"any": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": "<", "value": 45}]},
    )
    machine = parse_machine(migrate_v2_to_v3(parse_strategy_dict(doc)))
    assert [s.name for s in machine.states] == ["flat", "holding"]
    assert machine.on_position_closed == "flat"
    assert machine.max_cycles_per_day == 3


def test_the_risk_and_sizing_blocks_carry_over() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
        {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
        sizing={"type": "notional", "notional_per_trade": 75000},
    )
    machine = parse_machine(migrate_v2_to_v3(parse_strategy_dict(doc)))
    assert machine.sizing.notional_per_trade == 75000
    assert machine.risk.stop_loss.value == 1.5


# --- the equivalence that matters -------------------------------------------


def test_a_threshold_strategy_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": "<", "value": 45}]},
        {"any": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 55}]},
    )
    assert assert_same_trades(doc, market(600)) > 0


def test_a_crossover_strategy_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "ema", "params": {"period": 9},
                  "operator": "crosses_above",
                  "compare_to": {"indicator": "ema", "params": {"period": 21}}}]},
        {"any": [{"indicator": "ema", "params": {"period": 9},
                  "operator": "crosses_below",
                  "compare_to": {"indicator": "ema", "params": {"period": 21}}}]},
    )
    assert assert_same_trades(doc, market(800, seed=11)) > 0


def test_a_nested_multi_condition_strategy_is_identical() -> None:
    doc = v2_doc(
        {"all": [
            {"indicator": "close", "operator": ">",
             "compare_to": {"indicator": "ema", "params": {"period": 20}}},
            {"any": [
                {"indicator": "rsi", "params": {"period": 14},
                 "operator": ">", "value": 50},
                {"indicator": "volume", "operator": ">",
                 "compare_to": {"indicator": "sma", "params": {"period": 20},
                                "source": "volume"}},
            ]},
        ]},
        {"any": [{"indicator": "close", "operator": "<",
                  "compare_to": {"indicator": "ema", "params": {"period": 20}}}]},
    )
    assert assert_same_trades(doc, market(900, seed=3)) > 0


def test_a_short_strategy_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 55}]},
        {"any": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": "<", "value": 45}]},
        position_type="short",
    )
    assert assert_same_trades(doc, market(700, seed=5)) > 0


def test_a_strategy_with_a_session_square_off_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": "<", "value": 48}]},
        {"any": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 58}]},
        session={"square_off": "15:00", "no_entry_after": "14:30"},
    )
    assert assert_same_trades(doc, market(700, seed=13)) > 0


def test_a_previous_bar_reference_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "close", "operator": ">",
                  "compare_to": {"indicator": "high", "offset": 1}}]},
        {"any": [{"indicator": "close", "operator": "<",
                  "compare_to": {"indicator": "low", "offset": 1}}]},
    )
    assert assert_same_trades(doc, market(600, seed=21)) > 0


def test_a_daily_reference_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "close", "operator": ">",
                  "compare_to": {"indicator": "high", "timeframe": "day"}}]},
        {"any": [{"indicator": "close", "operator": "<",
                  "compare_to": {"indicator": "low", "timeframe": "day"}}]},
    )
    assert assert_same_trades(doc, market(900, seed=17)) > 0


def test_an_atr_stop_is_identical() -> None:
    doc = v2_doc(
        {"all": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": "<", "value": 45}]},
        {"any": [{"indicator": "rsi", "params": {"period": 14},
                  "operator": ">", "value": 55}]},
        risk={"stop_loss": {"type": "atr", "period": 14, "multiplier": 1.5},
              "target": {"type": "atr", "period": 14, "multiplier": 3.0}},
    )
    assert assert_same_trades(doc, market(700, seed=29)) > 0


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
def test_equivalence_holds_across_many_markets(seed: int) -> None:
    """Different price paths hit different branches: gap-through-stop, target
    on the open, square-off, back-to-back re-entry."""
    doc = v2_doc(
        {"all": [{"indicator": "ema", "params": {"period": 5},
                  "operator": "crosses_above",
                  "compare_to": {"indicator": "ema", "params": {"period": 13}}}]},
        {"any": [{"indicator": "ema", "params": {"period": 5},
                  "operator": "crosses_below",
                  "compare_to": {"indicator": "ema", "params": {"period": 13}}}]},
    )
    assert_same_trades(doc, market(700, seed=seed))
