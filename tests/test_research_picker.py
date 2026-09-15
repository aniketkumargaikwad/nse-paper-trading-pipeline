"""The fixed rule that picks one stock x timeframe from the training results.

The rule's whole job is to refuse to be flattered. Most of these tests are
about what it declines to pick.
"""

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


def combo(symbol, timeframe, trade_list, skipped=None, hold=0.0):
    """A combination. `hold` is what simply holding returned over the window."""
    return ComboResult(symbol, timeframe, False, trade_list, skipped, hold_return_pct=hold)


def pick(results):
    return pick_best(results, FREE, window_days_for=lambda r: 730)


def test_fewer_than_30_training_trades_never_qualifies():
    assert pick([combo("A", "day", trades(29, 5.0))]) is None


def test_a_training_dip_deeper_than_30_percent_disqualifies():
    assert pick([combo("A", "day", trades(30, 0.1, extra=(-40.0,)))]) is None


def test_skipped_combinations_are_ignored():
    assert pick([combo("A", "day", (), "no candles in window")]) is None


# --- beating buy-and-hold, or nothing ----------------------------------------


def test_a_combination_that_lost_to_holding_is_not_picked():
    """30 trades at 1% compounds to about +35%. Holding made 200%."""
    assert pick([combo("A", "day", trades(30, 1.0), hold=200.0)]) is None


def test_a_day_where_nothing_beats_holding_picks_nothing():
    results = [combo("A", "day", trades(30, 1.0), hold=200.0),
               combo("B", "60m", trades(40, 0.5), hold=300.0)]
    assert pick(results) is None


def test_the_biggest_margin_over_holding_wins_not_the_biggest_return():
    """The point of the rule: a smaller return in a flat market is the edge."""
    beta = combo("BETA", "day", trades(40, 2.0), hold=200.0)      # huge, but behind
    edge = combo("EDGE", "day", trades(30, 0.5), hold=-5.0)       # modest, but ahead
    got = pick([beta, edge])
    assert got.result.symbol == "EDGE"


def test_the_pick_says_how_far_ahead_of_holding_it_was():
    got = pick([combo("A", "day", trades(30, 1.0), hold=5.0)])
    returned = 100 * (got.training.end_value - got.training.start_value) / got.training.start_value
    assert got.excess_vs_hold_pct == round(returned - 5.0, 4)
    assert got.excess_vs_hold_pct > 0


def test_a_combination_with_no_benchmark_cannot_be_judged():
    """Without a hold return there is no answer to "better than doing nothing"."""
    assert pick([combo("A", "day", trades(30, 5.0), hold=None)]) is None


def test_a_tie_on_margin_goes_to_more_trades():
    got = pick([combo("A", "day", trades(30, 1.0), hold=0.0),
                combo("B", "day", trades(30, 1.0, extra=(0.0,)), hold=0.0)])
    assert got.result.symbol == "B"


def test_the_pick_carries_its_training_figures():
    got = pick([combo("A", "day", trades(30, 1.0), hold=0.0)])
    assert got.training.trades == 30 and got.training.cagr_pct > 0
