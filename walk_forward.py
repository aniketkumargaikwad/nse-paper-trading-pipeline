"""Out-of-sample splitting: making a backtest result falsifiable.

THE PROBLEM THIS SOLVES
-----------------------
Every result this platform produced before now was in-sample. The rules, the
stop, the target, the timeframe and the universe were all chosen while looking
at the same candles that then scored them. A number produced that way is not a
measurement of an edge; it is a measurement of how hard the settings were
pushed against one particular stretch of history.

`sweep.py` already warns that testing 24 variants at a 5% threshold is
expected to produce a passing variant by chance alone. This module is the
other half of that warning: a way to check the survivor against data it was
never fitted to.

WHAT THIS IS AND IS NOT
-----------------------
This is a HOLDOUT split — one boundary, in-sample before it, out-of-sample
after. It is not rolling walk-forward optimisation (re-fitting in each window
and stitching the out-of-sample pieces together). Holdout is the honest
minimum: it cannot tell you a strategy is good, only that a result did or did
not survive data it had never seen.

WHY SPLIT TRADES RATHER THAN RE-RUN THE WINDOW
----------------------------------------------
The engine simulates the whole window once and the trades are divided
afterwards. Re-simulating a short out-of-sample window would start every
indicator cold, so an EMA(200) would spend the first stretch of the
out-of-sample period converging and the trades taken there would differ from
the ones a continuously-running system would have taken. Splitting completed
trades keeps the out-of-sample half exactly what it claims to be: what this
strategy would have done, having already been running.

Pure module: no I/O, no clock.
"""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

from backtest_types import SimTrade


class HoldoutError(ValueError):
    """Raised when a holdout fraction or window cannot produce two sides."""


def split_at(from_utc: datetime, to_utc: datetime, holdout: float) -> datetime:
    """The boundary timestamp reserving the LAST `holdout` of the window.

    The out-of-sample period is always the later part. Reserving the earlier
    part instead would let the strategy be shaped by market conditions that,
    at the time the out-of-sample trades were taken, had not happened yet —
    look-ahead bias wearing the costume of validation.
    """
    if not 0 < holdout < 1:
        raise HoldoutError(
            f"holdout must be between 0 and 1 exclusive, got {holdout}. "
            "0 leaves nothing to validate against and 1 leaves nothing to "
            "fit on; both make the split meaningless."
        )
    if to_utc <= from_utc:
        raise HoldoutError(
            f"window end {to_utc.isoformat()} is not after its start "
            f"{from_utc.isoformat()}"
        )
    span = to_utc - from_utc
    return to_utc - span * holdout


def split_trades(
    trades: Sequence[SimTrade], split_ts: datetime
) -> tuple[list[SimTrade], list[SimTrade]]:
    """Divide trades into (in_sample, out_of_sample) at `split_ts`.

    Split on the ENTRY, because a trade belongs to the period whose data
    caused it to be taken. Using the exit would move a position opened
    in-sample into the out-of-sample set merely for being held across the
    boundary — crediting the out-of-sample half with a decision it did not
    make, which is the one thing this split exists to prevent.

    The boundary itself is out-of-sample, so no trade can fall in both.
    """
    in_sample: list[SimTrade] = []
    out_sample: list[SimTrade] = []
    for trade in trades:
        if trade.entry_fill_ts < split_ts:
            in_sample.append(trade)
        else:
            out_sample.append(trade)
    return in_sample, out_sample
