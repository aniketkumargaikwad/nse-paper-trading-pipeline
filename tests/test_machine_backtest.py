"""Backtesting a v3 machine: fills, stops, and staying in sync.

The interesting tests here are not "does it produce a trade". They are the
ones where the simulator and the machine could disagree about whether a
position exists — because every one of those disagreements produces a clean,
plausible, wrong backtest rather than an error.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from machine_backtest import simulate_machine  # noqa: E402
from strategy.v3 import parse_machine  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
BARS_PER_SESSION = 25


def candles(rows) -> pd.DataFrame:
    session_start = datetime(2026, 7, 16, 9, 15, tzinfo=IST)
    stamps = []
    for i in range(len(rows)):
        day, slot = divmod(i, BARS_PER_SESSION)
        stamps.append(
            (session_start + timedelta(days=day, minutes=15 * slot)).astimezone(UTC)
        )
    return pd.DataFrame(
        {
            "open": [r[0] for r in rows], "high": [r[1] for r in rows],
            "low": [r[2] for r in rows], "close": [r[3] for r in rows],
            "volume": [1000.0] * len(rows),
        },
        index=pd.DatetimeIndex(stamps, name="ts"),
    ).astype(float)


def doc(**overrides) -> dict:
    base = {
        "version": 3, "name": "t", "timeframe": "15m",
        "instruments": ["NSE:RELIANCE"],
        "initial": "flat",
        "states": [
            {"name": "flat",
             "transitions": [{"when": "close > 100", "enter": {"side": "long"},
                     "goto": "holding"}]},
            {"name": "holding",
             "transitions": [{"when": "close < 90", "exit": {}, "goto": "flat"}]},
        ],
        "risk": {"stop_loss": {"type": "percent", "value": 2.0},
                 "target": {"type": "percent", "value": 5.0}},
        "sizing": {"type": "fixed_quantity", "quantity": 10},
        "max_cycles_per_day": 5,
    }
    base.update(overrides)
    return base


def run(document, rows, **kwargs):
    params = {"slippage_pct": 0.0, "cost_per_trade_inr": 0.0}
    params.update(kwargs)
    return simulate_machine(candles(rows), parse_machine(document), **params)


# --- fills ------------------------------------------------------------------


def test_an_entry_fills_at_the_next_open_not_the_signal_close() -> None:
    """The property that keeps a backtest honest: you cannot trade the close
    you just used to decide."""
    result = run(doc(), [
        (95, 96, 94, 101),      # signal at close
        (102, 103, 101, 102),   # fill HERE, at 102
        (102, 103, 101, 102),
    ])
    assert len(result.trades) == 1
    assert result.trades[0].entry_price == pytest.approx(102.0)


def test_slippage_is_adverse_on_both_legs() -> None:
    result = run(doc(), [
        (95, 96, 94, 101),
        (100, 101, 99, 88),     # fill long at 100, then exit signal
        (100, 101, 99, 100),    # exit fills here
    ], slippage_pct=1.0)
    trade = result.trades[0]
    assert trade.entry_price == pytest.approx(101.0)   # bought higher
    assert trade.exit_price == pytest.approx(99.0)     # sold lower


def test_costs_are_charged() -> None:
    result = run(doc(), [
        (95, 96, 94, 101), (100, 101, 99, 88), (100, 101, 99, 100),
    ], cost_per_trade_inr=30.0)
    assert result.trades[0].costs == 30.0
    assert result.trades[0].net_pnl == pytest.approx(
        result.trades[0].gross_pnl - 30.0
    )


# --- the machine's own levels ----------------------------------------------


def test_a_machine_stop_expression_beats_the_risk_block() -> None:
    """`stop: floor` is the reason v3 exists — a level the strategy
    remembered, not a percentage from entry."""
    document = doc()
    document["states"][0]["transitions"][0] = {
        "when": "close > 100", "set": {"floor": "low"},
        "enter": {"side": "long", "stop": "floor"}, "goto": "holding",
    }
    result = run(document, [
        (95, 96, 93, 101),      # signal; floor = 93
        (100, 101, 99, 100),    # fill at 100
        (99, 99, 92, 93),       # low 92 pierces 93 -> stop fills AT 93
    ])
    assert len(result.trades) == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].intended_exit_price == pytest.approx(93.0)


def test_the_risk_block_applies_when_the_machine_names_no_stop() -> None:
    result = run(doc(), [
        (95, 96, 94, 101),
        (100, 101, 99, 100),    # fill at 100, stop 2% -> 98
        (99, 99, 97, 98),       # low 97 pierces 98
    ])
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].intended_exit_price == pytest.approx(98.0)


def test_the_stop_is_checked_before_the_target() -> None:
    """A candle that spans both must record the worse outcome."""
    result = run(doc(), [
        (95, 96, 94, 101),
        (100, 101, 99, 100),        # fill 100; stop 98, target 105
        (100, 106, 97, 100),        # hits BOTH
    ])
    assert result.trades[0].exit_reason == "stop_loss"


# --- staying in sync --------------------------------------------------------


def test_a_stop_out_frees_the_machine_to_trade_again() -> None:
    """THE integration bug. If the machine is not told the stop closed the
    position, it waits in `holding` forever and every later setup is lost —
    a backtest that quietly reports one trade instead of two."""
    result = run(doc(), [
        (95, 96, 94, 101),      # signal
        (100, 101, 99, 100),    # fill at 100, stop 98
        (99, 99, 97, 97),       # stopped out
        (97, 98, 96, 101),      # fresh signal, only possible if machine reset
        (100, 101, 99, 100),    # second fill
        (100, 101, 99, 100),
    ])
    assert len(result.trades) == 2


def test_a_skipped_entry_does_not_strand_the_machine() -> None:
    """Sizing that cannot afford one share is not a position, and the machine
    must not believe it holds one."""
    document = doc(sizing={"type": "notional", "notional_per_trade": 50})
    result = run(document, [
        (95, 96, 94, 101),
        (100, 101, 99, 100),    # notional 50 < price 100 -> qty 0, skipped
        (100, 101, 99, 101),    # machine free to signal again
        (100, 101, 99, 100),
    ])
    assert len(result.skipped) == 2
    assert result.trades == []


def test_max_cycles_per_day_does_not_strand_the_machine() -> None:
    """The day's quota blocks a FILL, not the machine.

    The proof has to reach into the next session: if being blocked leaves the
    machine sitting in `holding`, it never trades again — and a run that
    stopped trading on day one still looks like a perfectly ordinary result.
    """
    document = doc(max_cycles_per_day=1)
    document["states"][1]["transitions"][0] = {
        "when": "close < 99", "exit": {}, "goto": "flat",
    }
    rows = (
        [
            (95, 96, 94, 101),      # signal 1
            (100, 101, 99, 98),     # fill 1; exit signal
            (100, 101, 99, 101),    # exit fills; signal 2
            (100, 101, 99, 100),    # blocked: day's quota already used
        ]
        + [(100, 101, 99, 100)] * 21    # rest of session one, no signals
        + [
            (95, 96, 94, 101),      # session TWO: quota resets, signal
            (100, 101, 99, 100),    # fills here
            (100, 101, 99, 100),
        ]
    )
    result = run(document, rows)
    assert len(result.trades) == 2
    fill_days = {t.entry_fill_ts.astimezone(IST).date() for t in result.trades}
    assert len(fill_days) == 2      # one on each session


def test_square_off_closes_and_resets() -> None:
    document = doc(session={"square_off": "15:00"})
    # 25 bars a session; 15:00 IST is bar 23.
    rows = [(95, 96, 94, 101)] + [(100, 101, 99, 100)] * 24
    result = run(document, rows)
    assert any(t.exit_reason == "square_off" for t in result.trades)


def test_an_open_position_at_the_end_is_closed_and_flagged() -> None:
    result = run(doc(), [
        (95, 96, 94, 101), (100, 101, 99, 100), (100, 101, 99, 100),
    ])
    assert result.trades[0].exit_reason == "end_of_data"


# --- position introspection -------------------------------------------------


def test_a_machine_can_exit_on_bars_held() -> None:
    """A time stop, which needs `position.bars_held`."""
    document = doc()
    document["states"][1]["transitions"][0] = {
        "when": "position.bars_held >= 2", "exit": {}, "goto": "flat",
    }
    result = run(document, [
        (95, 96, 94, 101),
        (100, 101, 99, 100),    # fill; bars_held 0
        (100, 101, 99, 100),    # 1
        (100, 101, 99, 100),    # 2 -> exit signal
        (100, 101, 99, 100),    # exit fills
    ])
    assert result.trades[0].exit_reason == "signal"


def test_a_machine_can_exit_on_unrealised_pnl() -> None:
    document = doc()
    document["states"][1]["transitions"][0] = {
        "when": "position.pnl_pct > 1", "exit": {}, "goto": "flat",
    }
    result = run(document, [
        (95, 96, 94, 101),
        (100, 101, 99, 100),    # fill at 100
        (100, 102, 99, 102),    # +2% -> exit signal
        (102, 103, 101, 102),   # fills
    ])
    assert result.trades[0].exit_reason == "signal"


def test_position_is_flat_before_any_entry() -> None:
    document = doc()
    document["states"][0]["transitions"][0] = {
        "when": "not position.is_open and close > 100",
        "enter": {"side": "long"}, "goto": "holding",
    }
    result = run(document, [
        (95, 96, 94, 101), (100, 101, 99, 100), (100, 101, 99, 100),
    ])
    assert len(result.trades) == 1


# --- shorts -----------------------------------------------------------------


def test_a_short_profits_when_price_falls() -> None:
    document = doc()
    document["states"][0]["transitions"][0] = {
        "when": "close > 100", "enter": {"side": "short"}, "goto": "holding",
    }
    document["states"][1]["transitions"][0] = {
        "when": "close < 95", "exit": {}, "goto": "flat",
    }
    result = run(document, [
        (95, 96, 94, 101),
        (100, 101, 99, 94),     # short fills at 100; exit signal
        (90, 91, 89, 90),       # exit fills at 90
    ])
    trade = result.trades[0]
    assert trade.position_type == "short"
    assert trade.gross_pnl == pytest.approx((100.0 - 90.0) * 10)
