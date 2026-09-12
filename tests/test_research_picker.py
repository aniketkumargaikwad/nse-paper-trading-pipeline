"""The fixed rule that picks one stock x timeframe from the training results."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.picker import pick_best  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402


def trades(n: int, pct: float, *, extra: tuple[float, ...] = ()):
    """n same-day trades returning `pct` percent each, then any `extra` returns."""
    returns = [pct] * n + list(extra)
    start = ist(2023, 1, 2, 10)
    return tuple(
        trade(entry=start + timedelta(days=i), exit_=start + timedelta(days=i, hours=4),
              entry_price=100.0, exit_price=100.0 * (1 + r / 100))
        for i, r in enumerate(returns)
    )


def combo(symbol, timeframe, trade_list, skipped=None):
    return ComboResult(symbol, timeframe, False, trade_list, skipped)


def pick(results):
    return pick_best(results, FREE, window_days_for=lambda r: 730)


def test_fewer_than_30_training_trades_never_qualifies():
    assert pick([combo("A", "day", trades(29, 5.0))]) is None


def test_a_training_dip_deeper_than_30_percent_disqualifies():
    assert pick([combo("A", "day", trades(30, 0.1, extra=(-40.0,)))]) is None


def test_the_highest_compounded_annual_return_wins():
    got = pick([combo("A", "day", trades(30, 0.5)), combo("B", "60m", trades(30, 1.0))])
    assert (got.result.symbol, got.result.timeframe) == ("B", "60m")


def test_a_tie_goes_to_more_trades():
    got = pick([combo("A", "day", trades(30, 1.0)), combo("B", "day", trades(30, 1.0, extra=(0.0,)))])
    assert got.result.symbol == "B"


def test_skipped_combinations_are_ignored():
    assert pick([combo("A", "day", (), "no candles in window")]) is None


def test_the_pick_carries_its_training_figures():
    got = pick([combo("A", "day", trades(30, 1.0))])
    assert got.training.trades == 30 and got.training.cagr_pct > 0


def test_a_shorter_history_is_annualised_over_its_own_window():
    """A stock listed late must not be scored as if it had the full window."""
    results = [combo("A", "day", trades(30, 1.0))]
    long_window = pick_best(results, FREE, window_days_for=lambda r: 3000)
    short_window = pick_best(results, FREE, window_days_for=lambda r: 300)
    assert short_window.training.cagr_pct > long_window.training.cagr_pct
