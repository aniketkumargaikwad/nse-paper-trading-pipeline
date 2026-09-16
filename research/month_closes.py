"""Per-month first and last close of a candle frame.

Its own module because both the sweep (which has the candles) and the
basket (which has only the results) need it, and the basket imports the
sweep's result type - so this cannot live in either without a cycle.
"""

from __future__ import annotations

import pandas as pd

from config import IST


def month_closes(frame: pd.DataFrame) -> tuple[tuple[str, float, float], ...]:
    """(month 'YYYY-MM' in IST, first close, last close) for every month in `frame`.

    Vectorised: the sweep calls this on ~1,177 frames a version, some with
    150,000 rows.
    """
    if frame is None or len(frame) == 0:
        return ()
    local = frame.index.tz_convert(IST)
    keys = local.year * 100 + local.month
    closes = pd.Series(frame["close"].to_numpy(dtype=float), index=keys)
    grouped = closes.groupby(level=0, sort=True)
    firsts, lasts = grouped.first(), grouped.last()
    return tuple(
        (f"{int(key) // 100:04d}-{int(key) % 100:02d}", float(firsts[key]), float(lasts[key]))
        for key in firsts.index
    )
