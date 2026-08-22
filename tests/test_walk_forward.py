"""Out-of-sample splitting.

Every result this platform has produced so far is IN-SAMPLE: the rules were
chosen while looking at the same candles used to score them. That is the
single easiest way to believe a number that will not survive contact with a
live market, and it gets worse the more variants a sweep tries.

Splitting the window does not make a strategy good. It makes the claim
falsifiable, which is the part that was missing.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backtest_types import SimTrade  # noqa: E402
from walk_forward import (  # noqa: E402
    HoldoutError,
    split_at,
    split_trades,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

START = datetime(2026, 1, 1, tzinfo=UTC)
END = datetime(2026, 12, 31, tzinfo=UTC)


def trade_on(day: datetime) -> SimTrade:
    return SimTrade(
        entry_signal_ts=day,
        entry_fill_ts=day,
        exit_signal_ts=day + timedelta(hours=1),
        exit_fill_ts=day + timedelta(hours=1),
        position_type="long",
        quantity=1,
        intended_entry_price=100.0,
        entry_price=100.0,
        intended_exit_price=101.0,
        exit_price=101.0,
        exit_reason="target",
        gross_pnl=1.0,
        costs=0.0,
        net_pnl=1.0,
    )


# --- choosing the split point ----------------------------------------------


def test_holdout_reserves_the_END_of_the_window() -> None:
    """The out-of-sample period must be the LATER part.

    Holding out the earlier part and fitting on the later one would let the
    strategy be shaped by conditions that, in real time, had not happened yet.
    """
    split = split_at(START, END, holdout=0.25)
    total = (END - START).total_seconds()
    assert (END - split).total_seconds() == pytest.approx(total * 0.25, rel=1e-6)
    assert START < split < END


def test_half_and_half() -> None:
    split = split_at(START, END, holdout=0.5)
    assert (split - START) == (END - split)


def test_holdout_must_leave_both_sides_non_empty() -> None:
    with pytest.raises(HoldoutError):
        split_at(START, END, holdout=0.0)
    with pytest.raises(HoldoutError):
        split_at(START, END, holdout=1.0)


def test_holdout_outside_zero_to_one_is_rejected() -> None:
    for bad in (-0.1, 1.5):
        with pytest.raises(HoldoutError):
            split_at(START, END, holdout=bad)


def test_inverted_window_is_rejected() -> None:
    with pytest.raises(HoldoutError):
        split_at(END, START, holdout=0.3)


# --- splitting trades -------------------------------------------------------


def test_trades_are_split_by_ENTRY_time() -> None:
    """A trade belongs to the period it was DECIDED in.

    Splitting on the exit would move a trade opened in-sample into the
    out-of-sample set purely because it was held across the boundary, which
    credits the honest half with a decision it never made.
    """
    split = datetime(2026, 6, 1, tzinfo=UTC)
    before = trade_on(split - timedelta(days=1))
    across = SimTrade(
        **{**before.__dict__,
           "entry_signal_ts": split - timedelta(hours=1),
           "entry_fill_ts": split - timedelta(hours=1),
           "exit_fill_ts": split + timedelta(days=5)}
    )
    after = trade_on(split + timedelta(days=1))

    in_sample, out_sample = split_trades([before, across, after], split)
    assert len(in_sample) == 2      # both opened before the boundary
    assert len(out_sample) == 1


def test_a_trade_exactly_on_the_boundary_is_out_of_sample() -> None:
    """The boundary belongs to the out-of-sample side.

    Arbitrary either way, but it has to be decided once: a trade landing in
    both halves would be counted twice.
    """
    split = datetime(2026, 6, 1, tzinfo=UTC)
    in_sample, out_sample = split_trades([trade_on(split)], split)
    assert (len(in_sample), len(out_sample)) == (0, 1)


def test_every_trade_lands_in_exactly_one_side() -> None:
    split = datetime(2026, 6, 1, tzinfo=UTC)
    trades = [trade_on(START + timedelta(days=30 * i)) for i in range(12)]
    in_sample, out_sample = split_trades(trades, split)
    assert len(in_sample) + len(out_sample) == len(trades)
    assert not set(id(t) for t in in_sample) & set(id(t) for t in out_sample)


def test_no_trades_splits_into_two_empty_sides() -> None:
    split = datetime(2026, 6, 1, tzinfo=UTC)
    assert split_trades([], split) == ([], [])
