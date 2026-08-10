"""Unit tests for backtest.py's pure simulation core - no network, no DB.

Strategies here use trivially controllable rules (close vs. a threshold) so
each scenario can engineer exactly one behavior of the fill model.
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

from backtest import (  # noqa: E402
    MIN_TRADES,
    SimTrade,
    compute_metrics,
    evaluate_kill_rules,
    simulate,
    simulate_with_skips,
)
from strategy_schema import migrate_document, parse_strategies, parse_strategy_dict  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

SLIP = 0.05   # percent, matching the config default
COST = 30.0


def make_df(rows: list[tuple], start: datetime | None = None, step_min: int = 15) -> pd.DataFrame:
    """rows = [(open, high, low, close), ...]; volume fixed."""
    start = start or datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    index = pd.DatetimeIndex(
        [(start + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(len(rows))]
    )
    arr = np.asarray(rows, dtype=float)
    return pd.DataFrame(
        {
            "open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
            "close": arr[:, 3], "volume": np.full(len(rows), 1000.0),
        },
        index=index,
    )


def threshold_strategy(
    *, entry_above: float, exit_below: float, position_type: str = "long",
    sl_pct: float = 5.0, tgt_pct: float = 10.0, max_cycles: int = 10, qty: int = 1,
):
    """Entry: close > entry_above. Exit: close < exit_below. Wide SL/target
    by default so rule-based tests aren't disturbed by them."""
    doc = {
        "version": 2,
        "strategies": [
            {
                "name": "bt-test", "enabled": True, "position_type": position_type,
                "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "close", "operator": ">", "value": entry_above}]},
                "exit": {"any": [{"indicator": "close", "operator": "<", "value": exit_below}]},
                "risk": {
                    "stop_loss": {"type": "percent", "value": sl_pct},
                    "target": {"type": "percent", "value": tgt_pct},
                },
                "sizing": {"type": "fixed_quantity", "quantity": qty},
                "max_cycles_per_day": max_cycles,
            }
        ],
    }
    return parse_strategies(doc)[0]


def run(df, strat) -> list[SimTrade]:
    return simulate(df, strat, slippage_pct=SLIP, cost_per_trade_inr=COST)


# ---------------------------------------------------------------------------
# Migration identity — the whole migration promise, asserted rather than
# assumed: a v1 document and the equivalent hand-written v2 document must
# parse to the SAME Strategy, and therefore backtest identically.
# ---------------------------------------------------------------------------


def test_migrated_v1_strategy_produces_identical_trades() -> None:
    v1 = {
        "version": 1,
        "strategies": [
            {
                "name": "migrate-identity-test",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "close", "operator": ">", "value": 105}]},
                "exit": {"any": [{"indicator": "close", "operator": "<", "value": 90}]},
                "risk": {"stop_loss_pct": 1.0, "target_pct": 2.0},
                "sizing": {"type": "fixed_quantity", "quantity": 3},
                "max_cycles_per_day": 5,
            }
        ],
    }
    v2 = migrate_document(v1)

    # v1 can no longer be parsed directly (parse_strategies requires version
    # 2), so compare the migrated strategy against one hand-written in v2 form.
    hand_written_v2 = {
        "version": 2,
        "strategies": [
            {
                "name": "migrate-identity-test",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "close", "operator": ">", "value": 105}]},
                "exit": {"any": [{"indicator": "close", "operator": "<", "value": 90}]},
                "risk": {
                    "stop_loss": {"type": "percent", "value": 1.0},
                    "target": {"type": "percent", "value": 2.0},
                },
                "sizing": {"type": "fixed_quantity", "quantity": 3},
                "max_cycles_per_day": 5,
            }
        ],
    }

    migrated_strat = parse_strategies(v2)[0]
    hand_strat = parse_strategies(hand_written_v2)[0]
    assert migrated_strat == hand_strat

    # And the equality is not vacuous: run both through the simulator on the
    # same candles and confirm the trades themselves come out identical.
    df = make_df([
        (100, 101, 99, 100),
        (100, 107, 99, 106),   # signal candle (close 106 > 105)
        (108, 109, 107, 108),  # fill candle: open 108
        (108, 109, 107, 108),
        (108, 109, 50, 60),    # exit signal: close < 90
        (60, 61, 59, 60),      # exit fill at open 60
    ])
    assert run(df, migrated_strat) == run(df, hand_strat)


# ---------------------------------------------------------------------------
# Entry mechanics
# ---------------------------------------------------------------------------


def test_entry_fills_next_open_with_slippage() -> None:
    # Candle 1 closes above 105 -> entry fills at candle 2's OPEN.
    df = make_df([
        (100, 101, 99, 100),
        (100, 107, 99, 106),   # signal candle (close 106 > 105)
        (108, 109, 107, 108),  # fill candle: open 108
        (108, 109, 107, 108),
        (108, 109, 50, 60),    # exit: close < 90 -> queued
        (60, 61, 59, 60),      # exit fill at open 60
    ])
    strat = threshold_strategy(entry_above=105, exit_below=90, sl_pct=49, tgt_pct=49)
    trades = run(df, strat)
    assert len(trades) == 1
    t = trades[0]
    assert t.entry_signal_ts == df.index[1]
    assert t.entry_fill_ts == df.index[2]
    assert t.intended_entry_price == 108.0
    assert t.entry_price == pytest.approx(108.0 * 1.0005)  # buy pays slippage


def test_no_same_candle_close_fill_and_last_candle_signal_ignored() -> None:
    # Signal on the LAST candle has no next open -> no trade at all.
    df = make_df([
        (100, 101, 99, 100),
        (100, 101, 99, 100),
        (100, 107, 99, 106),  # signal, but nothing after it
    ])
    strat = threshold_strategy(entry_above=105, exit_below=90)
    assert run(df, strat) == []


def test_rule_exit_fills_next_open() -> None:
    df = make_df([
        (100, 107, 99, 106),   # entry signal
        (106, 107, 105, 106),  # entry fill at open 106
        (106, 107, 80, 85),    # exit signal (close 85 < 90)
        (84, 85, 83, 84),      # exit fill at open 84
        (84, 85, 83, 84),
    ])
    strat = threshold_strategy(entry_above=105, exit_below=90, sl_pct=49, tgt_pct=50)
    trades = run(df, strat)
    assert len(trades) == 1
    t = trades[0]
    assert t.exit_reason == "signal"
    assert t.exit_signal_ts == df.index[2]
    assert t.exit_fill_ts == df.index[3]
    assert t.intended_exit_price == 84.0
    assert t.exit_price == pytest.approx(84.0 * 0.9995)  # sell receives less


# ---------------------------------------------------------------------------
# Stop-loss / target mechanics
# ---------------------------------------------------------------------------


def test_target_hit_intra_candle_fills_at_target_level() -> None:
    strat = threshold_strategy(entry_above=105, exit_below=1, sl_pct=1.0, tgt_pct=2.0)
    df = make_df([
        (100, 107, 99, 106),     # entry signal
        (100, 100.5, 99.5, 100),  # fill at open 100 -> tgt=102.051*, sl=99.0495*
        (101, 103, 100.5, 102.5),  # high 103 crosses the target intra-candle
        (102, 103, 101, 102),
    ])
    trades = run(df, strat)
    assert len(trades) == 1
    t = trades[0]
    entry = 100 * 1.0005
    expected_target = entry * 1.02
    assert t.exit_reason == "target"
    assert t.exit_fill_ts == df.index[2]
    assert t.intended_exit_price == pytest.approx(expected_target)
    assert t.exit_price == pytest.approx(expected_target * 0.9995)
    assert t.net_pnl == pytest.approx(t.gross_pnl - COST)


def test_stop_beats_target_when_both_hit_in_one_candle() -> None:
    # Wide candle spans both levels -> worst case: stop assumed first.
    strat = threshold_strategy(entry_above=105, exit_below=1, sl_pct=1.0, tgt_pct=1.0)
    df = make_df([
        (100, 107, 99, 106),   # entry signal
        (100, 100.2, 99.9, 100),  # fill at open 100
        (100, 105, 95, 100),   # hits BOTH sl (~99.05) and tgt (~101.05)
        (100, 101, 99, 100),
    ])
    trades = run(df, strat)
    assert len(trades) == 1
    assert trades[0].exit_reason == "stop_loss"


def test_gap_through_stop_fills_at_open_not_level() -> None:
    strat = threshold_strategy(entry_above=105, exit_below=1, sl_pct=1.0, tgt_pct=50)
    df = make_df([
        (100, 107, 99, 106),   # entry signal
        (100, 101, 99.6, 100),  # fill at open 100 -> sl ~ 99.05
        (90, 91, 89, 90),      # gaps DOWN through the stop: open 90 << 99.05
        (90, 91, 89, 90),
    ])
    trades = run(df, strat)
    t = trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.intended_exit_price == 90.0  # the open, not the stop level
    assert t.exit_price == pytest.approx(90.0 * 0.9995)


def test_short_position_pnl_and_slippage_directions() -> None:
    # Short: entry close < threshold logic -> use entry close > value on an
    # inverted framing instead: entry when close > 95 is fine; profit needs
    # the price to FALL after entry.
    strat = threshold_strategy(
        entry_above=95, exit_below=1, position_type="short", sl_pct=50, tgt_pct=5
    )
    df = make_df([
        (100, 101, 99, 100),   # entry signal (close 100 > 95)
        (100, 100.4, 99.6, 100),  # fill: SELL at open 100 -> receives 99.95
        (96, 97, 94, 95),      # falls: target 99.95*0.95=94.95 hit (low 94)
        (95, 96, 94, 95),
    ])
    trades = run(df, strat)
    assert len(trades) == 1
    t = trades[0]
    entry = 100 * (1 - SLIP / 100)  # short entry is a sell
    target = entry * 0.95
    assert t.entry_price == pytest.approx(entry)
    assert t.exit_reason == "target"
    assert t.intended_exit_price == pytest.approx(target)
    assert t.exit_price == pytest.approx(target * (1 + SLIP / 100))  # buy-back pays
    # gross_pnl is rounded to 4 decimals when the trade is recorded.
    assert t.gross_pnl == pytest.approx((t.entry_price - t.exit_price) * 1, abs=1e-3)
    assert t.gross_pnl > 0


def test_end_of_data_closes_open_position() -> None:
    strat = threshold_strategy(entry_above=105, exit_below=1, sl_pct=49, tgt_pct=50)
    df = make_df([
        (100, 107, 99, 106),   # signal
        (108, 109, 107, 108),  # fill; then history simply ends
        (108, 109, 107, 108.5),
    ])
    trades = run(df, strat)
    assert len(trades) == 1
    assert trades[0].exit_reason == "end_of_data"
    assert trades[0].intended_exit_price == 108.5  # last close


# ---------------------------------------------------------------------------
# Cycle limits
# ---------------------------------------------------------------------------


def test_max_cycles_per_day_enforced() -> None:
    # Entry always true (close > 0), exit always true (close < 1e9): the
    # system would churn every other candle; cap at 2 cycles per day.
    doc_strat = threshold_strategy(entry_above=0, exit_below=1e9, max_cycles=2, sl_pct=49, tgt_pct=50)
    rows = [(100, 101, 99, 100)] * 20  # one IST day of candles
    df = make_df(rows)
    trades = run(df, doc_strat)
    assert len(trades) == 2  # third entry blocked by the daily cycle cap


def test_cycle_cap_resets_next_day() -> None:
    doc_strat = threshold_strategy(entry_above=0, exit_below=1e9, max_cycles=1, sl_pct=49, tgt_pct=50)
    day1 = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    day2 = datetime(2026, 7, 17, 9, 15, tzinfo=IST)
    df1 = make_df([(100, 101, 99, 100)] * 6, start=day1)
    df2 = make_df([(100, 101, 99, 100)] * 6, start=day2)
    df = pd.concat([df1, df2])
    trades = run(df, doc_strat)
    # One cycle each day (the second day's entry re-arms after midnight IST).
    assert len(trades) == 2
    fill_days = {t.entry_fill_ts.astimezone(IST).date() for t in trades}
    assert len(fill_days) == 2


# ---------------------------------------------------------------------------
# Sizing — notional quantity per symbol, with skips recorded
# ---------------------------------------------------------------------------


def strategy_with(**overrides):
    """A minimal v2 strategy with one field group replaced."""
    doc = {
        "name": "t", "enabled": True, "position_type": "long", "timeframe": "15m",
        "instruments": ["NSE:TEST"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 0}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 0}]},
        "risk": {"stop_loss": {"type": "percent", "value": 0.7},
                 "target": {"type": "percent", "value": 1.5}},
        "sizing": {"type": "fixed_quantity", "quantity": 1},
    }
    doc.update(overrides)
    return parse_strategy_dict(doc)


def frame_with_one_round_trip(entry_open: float) -> pd.DataFrame:
    """One signal candle, then a fill candle whose high crosses the 1.5%
    target intra-candle (and whose low stays clear of the 0.7% stop) — a
    single, unambiguous round trip regardless of the entry price scale."""
    return make_df([
        (entry_open * 0.9, entry_open * 0.91, entry_open * 0.89, entry_open * 0.9),
        (entry_open, entry_open * 1.02, entry_open * 0.995, entry_open * 1.01),
    ])


def test_notional_sizing_derives_quantity_from_the_entry_price():
    """100000 notional at a ~1000 entry fill buys 99 shares, not 1."""
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 100000})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades[0].quantity == 100


def test_a_share_dearer_than_the_notional_produces_no_trade():
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 500})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades == []


def test_a_skipped_entry_is_reported_not_silently_dropped():
    strategy = strategy_with(sizing={"type": "notional", "notional_per_trade": 500})
    result = simulate_with_skips(frame_with_one_round_trip(entry_open=1000.0),
                                 strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert result.trades == []
    assert len(result.skipped) == 1
    assert result.skipped[0].reason == "notional_below_price"
    assert result.skipped[0].price == 1000.0


def test_fixed_quantity_is_unaffected():
    strategy = strategy_with(sizing={"type": "fixed_quantity", "quantity": 7})
    trades = simulate(frame_with_one_round_trip(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=30.0)
    assert trades[0].quantity == 7


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def fake_trade(net: float, entry_price: float = 100.0, qty: int = 1) -> SimTrade:
    ts = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    return SimTrade(
        entry_signal_ts=ts, entry_fill_ts=ts, exit_signal_ts=ts, exit_fill_ts=ts,
        position_type="long", quantity=qty,
        intended_entry_price=entry_price, entry_price=entry_price,
        intended_exit_price=entry_price + net, exit_price=entry_price + net,
        exit_reason="signal", gross_pnl=net + COST, costs=COST, net_pnl=net,
    )


def test_metrics_hand_computation() -> None:
    trades = [fake_trade(x) for x in (50, -20, -30, 40, -10, -10, 60)]
    m = compute_metrics(trades)
    assert m.total_trades == 7
    assert m.winning_trades == 3
    assert m.net_pnl == pytest.approx(80.0)
    assert m.win_rate_pct == pytest.approx(round(300 / 7, 2))
    assert m.profit_factor == pytest.approx(round(150 / 70, 4))
    assert m.longest_losing_streak == 2
    # Equity: 50,30,0,40,30,20,80 -> worst peak-to-trough = 50 (50 -> 0).
    # Base = entry notional 100 -> 50%.
    assert m.max_drawdown_pct == pytest.approx(50.0)


def test_metrics_no_losses_gives_null_profit_factor() -> None:
    m = compute_metrics([fake_trade(10), fake_trade(20)])
    assert m.profit_factor is None
    assert m.max_drawdown_pct == 0.0


def test_metrics_empty() -> None:
    m = compute_metrics([])
    assert m.total_trades == 0 and m.net_pnl == 0.0


# ---------------------------------------------------------------------------
# Kill rules
# ---------------------------------------------------------------------------


def good_metrics(**overrides):
    base = dict(
        total_trades=50, winning_trades=30, net_pnl=5000.0, win_rate_pct=60.0,
        profit_factor=1.8, max_drawdown_pct=10.0, longest_losing_streak=4,
    )
    base.update(overrides)
    from backtest import ComboMetrics
    return ComboMetrics(**base)


def test_kill_rules_all_pass() -> None:
    passed, flags = evaluate_kill_rules(good_metrics(), profitable_symbols=4, total_symbols=5)
    assert passed
    assert all(f["passed"] for f in flags.values())


@pytest.mark.parametrize(
    "metrics_kw, symbols",
    [
        ({"total_trades": MIN_TRADES - 1}, 4),   # too few trades
        ({"net_pnl": -1.0}, 4),                  # loses money net of costs
        ({"max_drawdown_pct": 35.0}, 4),         # blows the drawdown cap
        ({}, 2),                                 # profitable on too few symbols
    ],
)
def test_each_kill_rule_can_fail_alone(metrics_kw, symbols) -> None:
    passed, flags = evaluate_kill_rules(
        good_metrics(**metrics_kw), profitable_symbols=symbols, total_symbols=5
    )
    assert not passed
    assert sum(1 for f in flags.values() if not f["passed"]) == 1


def test_symbol_requirement_capped_by_instrument_count() -> None:
    # A 1-instrument strategy cannot be asked for 3 profitable symbols.
    passed, flags = evaluate_kill_rules(good_metrics(), profitable_symbols=1, total_symbols=1)
    assert passed
    assert flags["symbol_robustness"]["required_profitable_symbols"] == 1

