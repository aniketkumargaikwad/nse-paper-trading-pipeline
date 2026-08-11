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
from risk_levels import RiskLevelError  # noqa: E402
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
    """A 100000 notional at a 1000 entry fill buys 100 shares, not 1.

    Sizing in rupees rather than shares is what makes cost drag comparable
    across a universe — at a fixed share count it is 20x heavier on a cheap
    stock than an expensive one.
    """
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
# ATR stops — the level must be read from the SIGNAL candle (the last CLOSED
# candle before the fill), never the fill candle itself, and insufficient
# warm-up history must be a loud error rather than a quiet zero-trade result.
# ---------------------------------------------------------------------------


def frame_with_known_atr(entry_open: float, atr_at_signal: float) -> pd.DataFrame:
    """4 candles engineered so indicators.atr(df, 2) reads EXACTLY
    `atr_at_signal` at candle 1 — the entry SIGNAL candle — and candle 3
    trades down through the resulting long stop.

    Candle 0's close is exactly 0, so the default `close > 0` entry rule
    from strategy_with() does not fire there; candle 1's close is the first
    positive one, so entry signals THERE instead (one candle later than the
    naive case). That one-candle delay is what makes a *valid* ATR(2) reading
    available at the signal candle at all — ATR(2) needs two candles of true
    range before pandas' min_periods stops masking it as NaN.

    Candles 0 and 1 both carry a true range of exactly `atr_at_signal`
    (candle 0 from its own high-low span; candle 1 from the same span, with
    its previous close pinned at 0 so the |high - prev_close| term never
    dominates). A period-2 Wilder average of two identical values is that
    same value, so ATR at candle 1 is exactly `atr_at_signal` — no need to
    hand-derive the EWM recursion for an arbitrary run.

    Candle 2 is the fill candle (open = entry_open); candle 3's low crosses
    back down through `entry_open - 1.5 * atr_at_signal` without gapping
    past it, so the stop fills AT the level rather than at candle 3's open.
    """
    half = atr_at_signal / 2.0
    stop = entry_open - 1.5 * atr_at_signal
    return make_df([
        (0.0, half, -half, 0.0),                             # candle 0: TR = atr_at_signal
        (0.0, half, -half, 1.0),                              # candle 1: signal, TR = atr_at_signal
        (entry_open, entry_open + 1, entry_open - 1, entry_open),  # candle 2: fill, no hit
        (stop + 5, stop + 10, stop - 5, stop - 3),            # candle 3: low crosses the stop
    ])


def frame_with_known_atr_short(entry_open: float, atr_at_signal: float) -> pd.DataFrame:
    """Mirror of frame_with_known_atr for a short: the stop sits ABOVE the
    entry, so candle 3's high crosses back up through it instead."""
    half = atr_at_signal / 2.0
    stop = entry_open + 1.5 * atr_at_signal
    return make_df([
        (0.0, half, -half, 0.0),
        (0.0, half, -half, 1.0),
        (entry_open, entry_open + 1, entry_open - 1, entry_open),
        (stop - 5, stop + 10, stop - 10, stop - 3),           # candle 3: high crosses the stop
    ])


def test_atr_stop_is_set_from_the_candle_before_the_fill():
    """1.5 x ATR below the entry fill, using the last CLOSED candle's ATR."""
    strategy = strategy_with(risk={
        "stop_loss": {"type": "atr", "period": 2, "multiplier": 1.5},
        "target": {"type": "percent", "value": 1.5},
    })
    df = frame_with_known_atr(entry_open=1000.0, atr_at_signal=10.0)
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    # stop = 1000 - 1.5*10 = 985
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].intended_exit_price == pytest.approx(985.0)


def test_atr_stop_for_a_short_is_above_the_entry():
    strategy = strategy_with(
        position_type="short",
        risk={"stop_loss": {"type": "atr", "period": 2, "multiplier": 1.5},
              "target": {"type": "percent", "value": 1.5}},
    )
    df = frame_with_known_atr_short(entry_open=1000.0, atr_at_signal=10.0)
    trades = simulate(df, strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].intended_exit_price == pytest.approx(1015.0)


def test_insufficient_history_for_the_atr_period_is_a_hard_error():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "atr", "period": 500, "multiplier": 1.5},
        "target": {"type": "percent", "value": 1.5},
    })
    with pytest.raises(RiskLevelError) as exc:
        simulate(frame_with_one_round_trip(entry_open=1000.0),
                 strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    msg = str(exc.value)
    assert "500" in msg          # candles needed
    assert "atr" in msg.lower()


# ---------------------------------------------------------------------------
# Trailing stop — the level updates at candle CLOSE and applies from the NEXT
# candle. A candle that makes a new high AND falls back through the level
# implied by that same high must NOT exit there: at the moment the low
# happened, the high had not yet been observed. Update-after-checks is the
# whole correctness question here.
# ---------------------------------------------------------------------------


def frame_trailing(entry_open: float, highs: list[float], drop_to: float) -> pd.DataFrame:
    """Entry at `entry_open` (1% trailing, 5% stop, 50% target — wide enough
    that only the trailing stop is ever in play).

    Candle 0 has close == 0 so the default `close > 0` entry rule from
    strategy_with() does not fire there; candle 1 is the first positive
    close, so entry signals there; candle 2 is the flat fill candle
    (open = entry_open, no wiggle).

    Each `highs` value gets its own candle AFTER that: its low is kept
    comfortably above the trail level that was active BEFORE it (i.e. set
    from the previous candle's close), so it cannot trigger a premature
    exit, and the trail is advanced (never loosened) after it closes — this
    mirrors the production update-after-checks rule so the fixture stays
    honest about what "safe" means at each step.

    The final candle drops through the trail level established by the last
    `highs` candle, down to `drop_to`, without gapping past it — so the
    exit price is the trail level itself, not the candle's open.
    """
    rows = [
        (0.0, 0.5, -0.5, 0.0),                                       # candle 0: no entry
        (0.0, 0.5, -0.5, 1.0),                                       # candle 1: signal
        (entry_open, entry_open, entry_open, entry_open),            # candle 2: flat fill
    ]
    best = entry_open
    trail = entry_open * 0.99   # trail set from the fill candle's own (flat) close
    for h in highs:
        safe_low = trail + 1.0
        rows.append((safe_low + 1.0, h, safe_low, safe_low + 0.5))
        best = max(best, h)
        trail = max(trail, best * 0.99)
    final_open = trail + 5.0
    rows.append((final_open, final_open + 1.0, drop_to, final_open - 1.0))
    return make_df(rows)


def frame_high_and_reversal_in_one_candle(entry_open: float) -> pd.DataFrame:
    """The look-ahead case, in ONE candle: a new high of entry_open * 1.05,
    followed — within that SAME candle — by a pullback to just below the
    trailing level that high would imply (1% below it). At the moment that
    low happened, the high had not yet been "observed" (the trail only
    updates at candle CLOSE); the trail active during this candle is still
    the one set from the flat fill candle before it (entry_open * 0.99),
    which this candle's low never approaches. So a correct implementation
    must not exit here at all — the position runs off the end of the data
    and closes as "end_of_data" instead.
    """
    high = entry_open * 1.05
    level_from_this_candles_own_high = high * 0.99
    low = level_from_this_candles_own_high - 1.0
    return make_df([
        (0.0, 0.5, -0.5, 0.0),
        (0.0, 0.5, -0.5, 1.0),
        (entry_open, entry_open, entry_open, entry_open),   # flat fill
        (low + 6.5, high, low, low + 3.5),                  # high, then reversal — one candle
    ])


def frame_immediate_drop(entry_open: float, to: float) -> pd.DataFrame:
    """The fill candle itself drops straight through the fixed stop.

    No trail has been established yet (that only happens AFTER a candle's
    exit checks) so `trail_price` is still None during this very candle —
    only the fixed stop can possibly apply here, which is exactly what this
    fixture is for.
    """
    return make_df([
        (0.0, 0.5, -0.5, 0.0),
        (0.0, 0.5, -0.5, 1.0),
        (entry_open, entry_open + 1.0, to, entry_open - 5.0),
    ])


def test_trailing_stop_follows_the_high_and_exits_on_the_pullback():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},      # far, so trailing wins
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    # Entry at 1000; highs 1010 then 1020; then a drop through 1020*0.99 = 1009.8
    trades = simulate(frame_trailing(entry_open=1000.0, highs=[1010, 1020], drop_to=1000),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "trailing_stop"
    assert trades[0].intended_exit_price == pytest.approx(1009.8)


def test_trailing_stop_does_not_use_the_same_candle_it_was_set_from():
    """The look-ahead case.

    A candle that makes a new high AND falls back through the level implied by
    that same high must NOT exit at it — at the time the low happened, the
    high had not yet been observed.
    """
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    trades = simulate(frame_high_and_reversal_in_one_candle(entry_open=1000.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason != "trailing_stop"


def test_trailing_stop_never_loosens():
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 5.0},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    # High 1020 sets the trail at 1009.8; a LOWER subsequent high (1015) must
    # not pull it back down (1015 * 0.99 = 1004.85 would, if the trail were
    # allowed to loosen) — the level must stay at 1009.8.
    trades = simulate(frame_trailing(entry_open=1000.0, highs=[1020, 1015], drop_to=1000),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].intended_exit_price == pytest.approx(1009.8)


def test_the_tighter_of_fixed_and_trailing_wins():
    """Early in a trade the fixed stop is tighter and must still apply."""
    strategy = strategy_with(risk={
        "stop_loss": {"type": "percent", "value": 0.5},
        "target": {"type": "percent", "value": 50.0},
        "trailing_stop": {"type": "percent", "value": 5.0},
    })
    trades = simulate(frame_immediate_drop(entry_open=1000.0, to=990.0),
                      strategy, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "stop_loss"
    assert trades[0].intended_exit_price == pytest.approx(995.0)


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



# ---------------------------------------------------------------------------
# Regression: a signal firing on the same candle a notional skip happened must
# still be seen. An earlier version `continue`d past the bottom-of-loop block
# that queues a fresh entry from this candle's close, so such a signal
# vanished entirely — no trade AND no SkippedEntry — which is precisely the
# invisibility the skip record exists to prevent.
# ---------------------------------------------------------------------------


def notional_strategy(*, notional: float, entry_above: float, exit_below: float):
    doc = {
        "version": 2,
        "strategies": [
            {
                "name": "notional-test", "enabled": True, "position_type": "long",
                "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "close", "operator": ">", "value": entry_above}]},
                "exit": {"any": [{"indicator": "close", "operator": "<", "value": exit_below}]},
                "risk": {
                    "stop_loss": {"type": "percent", "value": 5.0},
                    "target": {"type": "percent", "value": 10.0},
                },
                "sizing": {"type": "notional", "notional_per_trade": notional},
                "max_cycles_per_day": 10,
            }
        ],
    }
    return parse_strategies(doc)[0]


def test_a_signal_on_the_skip_candle_is_not_lost():
    """The share is too dear at the first fill, then cheap enough at the next.

    The second signal fires at the close of the very candle whose fill was
    skipped. It must still reach a trade — dropping it would under-count
    trades in exactly the case the skip record was built to make visible.
    """
    # candle 0 close 1000 -> signal. candle 1 open 1000: too dear for a 500
    # notional -> skip; its close 600 signals again. candle 2 open 50: fillable.
    df = make_df([
        (1000, 1000, 1000, 1000),
        (1000, 1000, 600, 600),
        (50, 50, 50, 50),
        (50, 50, 50, 50),
    ])
    strat = notional_strategy(notional=500, entry_above=500, exit_below=10)
    result = simulate_with_skips(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)

    assert len(result.skipped) == 1, "the dear-share fill should be recorded"
    assert result.trades, "the signal on the skip candle must still reach a trade"
    assert result.trades[0].quantity == 10       # 500 notional / 50


def test_each_trade_carries_its_own_quantity():
    """Two round trips at different entry prices in one run.

    Quantity is per-trade state; a module-level variable would leave both
    trades reporting the last value computed.
    """
    # Entry at 1000 (qty 100), exit, then entry at 2000 (qty 50).
    df = make_df([
        (1000, 1000, 1000, 1000),   # signal
        (1000, 1000, 1000, 1000),   # fill @1000
        (1000, 1000, 1000, 5),      # exit signal
        (1000, 1000, 1000, 1000),   # exit fill; re-signal at close
        (2000, 2000, 2000, 2000),   # fill @2000
        (2000, 2000, 2000, 5),      # exit signal
        (2000, 2000, 2000, 2000),   # exit fill
    ])
    strat = notional_strategy(notional=100000, entry_above=500, exit_below=10)
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)

    assert len(trades) == 2
    assert [t.quantity for t in trades] == [100, 50]


# ---------------------------------------------------------------------------
# Session rules: entry window and intraday square-off.
#
# Square-off is the one that closes a real correctness gap — without it a
# 15-minute strategy with a 0.7% stop can hold overnight, so the backtest
# quietly includes gap risk the stop never protected against.
# ---------------------------------------------------------------------------


def session_strategy(*, session: dict, entry_above=105.0, exit_below=1.0):
    doc = {
        "version": 2,
        "strategies": [{
            "name": "sess-test", "enabled": True, "position_type": "long",
            "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
            "entry": {"all": [{"indicator": "close", "operator": ">", "value": entry_above}]},
            "exit": {"any": [{"indicator": "close", "operator": "<", "value": exit_below}]},
            "risk": {
                "stop_loss": {"type": "percent", "value": 40.0},
                "target": {"type": "percent", "value": 40.0},
            },
            "sizing": {"type": "fixed_quantity", "quantity": 1},
            "session": session,
            "max_cycles_per_day": 10,
        }],
    }
    return parse_strategies(doc)[0]


def flat_day(n: int, start_hhmm=(9, 15)):
    """n candles of identical, signal-firing price from a given IST start."""
    start = datetime(2026, 7, 16, *start_hhmm, tzinfo=IST)
    return make_df([(110, 110, 110, 110)] * n, start=start)


def ist_hhmm(ts) -> str:
    return ts.astimezone(IST).strftime("%H:%M")


def test_no_entry_before_blocks_the_early_fill():
    # 09:15 signal would fill 09:30; the window pushes the first fill to 10:00.
    df = flat_day(8)                       # 09:15 .. 11:00
    strat = session_strategy(session={"no_entry_before": "10:00"})
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades, "an entry should eventually fill once the window opens"
    assert ist_hhmm(trades[0].entry_fill_ts) == "10:00"


def test_no_entry_after_blocks_the_late_fill():
    # Every candle signals, but all fills land after the cutoff.
    df = make_df([(110, 110, 110, 110)] * 4,
                 start=datetime(2026, 7, 16, 14, 30, tzinfo=IST))
    strat = session_strategy(session={"no_entry_after": "14:00"})
    assert simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0) == []


def test_the_window_applies_to_the_fill_candle_not_the_signal_candle():
    """A 09:45 signal fills at 10:00 and is allowed by no_entry_before 10:00."""
    df = flat_day(6)                       # 09:15 .. 10:30
    strat = session_strategy(session={"no_entry_before": "10:00"})
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert ist_hhmm(trades[0].entry_signal_ts) == "09:45"   # signal before
    assert ist_hhmm(trades[0].entry_fill_ts) == "10:00"     # fill at the bound


def test_square_off_closes_at_the_first_candle_at_or_after_it():
    # 09:15 .. 15:30; entry fills 09:30 and nothing else would ever exit it.
    df = flat_day(25)
    strat = session_strategy(session={"square_off": "15:15"})
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "square_off"
    assert ist_hhmm(trades[0].exit_fill_ts) == "15:15"


def test_square_off_means_no_position_survives_the_day():
    df = flat_day(25)
    strat = session_strategy(session={"square_off": "15:15"})
    for t in simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0):
        assert t.entry_fill_ts.astimezone(IST).date() == t.exit_fill_ts.astimezone(IST).date()


def test_square_off_also_blocks_a_late_entry():
    """A position opened at or after square-off is not a trade, just costs."""
    df = make_df([(110, 110, 110, 110)] * 4,
                 start=datetime(2026, 7, 16, 15, 0, tzinfo=IST))
    strat = session_strategy(session={"square_off": "15:15"})
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert all(ist_hhmm(t.entry_fill_ts) < "15:15" for t in trades)


def test_no_session_block_leaves_previous_behaviour_unchanged():
    df = flat_day(25)
    with_none = session_strategy(session={})
    trades = simulate(df, with_none, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert all(t.exit_reason != "square_off" for t in trades)


def test_a_stop_on_the_square_off_candle_still_wins():
    """Worst-case ordering: the stop would really have fired first."""
    start = datetime(2026, 7, 16, 14, 45, tzinfo=IST)
    # 14:45 signal -> 15:00 fill @110; 15:15 candle craters through the stop.
    df = make_df([
        (110, 110, 110, 110),
        (110, 110, 110, 110),
        (110, 110, 10, 10),
    ], start=start)
    strat = session_strategy(session={"square_off": "15:15"})
    trades = simulate(df, strat, slippage_pct=0.0, cost_per_trade_inr=0.0)
    assert trades[0].exit_reason == "stop_loss"
