"""The CandleProvider contract every data source must satisfy.

Because every provider returns the identical canonical frame, nothing
downstream of candle_store can tell which source produced the data - or
whether the market is currently open. That is what makes backtests runnable
at any hour, on weekends, and on exchange holidays.

Canonical frame
---------------
Columns [open, high, low, close, volume] as floats, indexed by candle START
time as tz-aware UTC, sorted ascending, duplicates removed.

Naive timestamps are read as IST: every supported source serves Indian market
data in local time, so assuming UTC would shift every candle by 5.5 hours.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

import pandas as pd

from config import IST

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


class ProviderError(RuntimeError):
    """A data source failed or returned something unusable."""


# ---------------------------------------------------------------------------
# Canonical frame helpers
# ---------------------------------------------------------------------------


def empty_frame() -> pd.DataFrame:
    """The canonical frame with no rows."""
    return pd.DataFrame(
        columns=OHLCV_COLUMNS,
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )


def canonical_frame(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Coerce a provider's frame into canonical form.

    Raises:
        ProviderError: if any required OHLCV column is absent - named
            explicitly, because a silently-missing column would surface much
            later as a confusing failure deep in the indicator maths.
    """
    if raw is None or raw.empty:
        return empty_frame()

    missing = [c for c in OHLCV_COLUMNS if c not in raw.columns]
    if missing:
        raise ProviderError(
            f"Provider frame is missing column(s): {missing}. "
            "The upstream API shape may have changed."
        )

    df = raw[OHLCV_COLUMNS].astype(float).copy()
    index = pd.to_datetime(raw.index)
    index = index.tz_localize(IST) if index.tz is None else index
    df.index = index.tz_convert("UTC")
    df.index.name = "ts"
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


# ---------------------------------------------------------------------------
# The provider contract
# ---------------------------------------------------------------------------


@runtime_checkable
class CandleProvider(Protocol):
    """Read-only market-data source.

    No provider may expose order endpoints. Live execution belongs behind a
    separate, deliberately-added adapter - never here.
    """

    name: str

    def fetch(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Return canonical candles covering [from_utc, to_utc].

        NOT trimmed to the window. Day-granular sources (Dhan sends
        fromDate/toDate as dates) return whole trading days, so the result may
        begin before from_utc and end after to_utc - by up to one session at
        each end. Extra candles are real data, never gaps: callers may store
        them and may treat the last returned timestamp as genuinely covered.
        Reconciling against the exact requested range is the caller's job.
        """
        ...

    def max_history_days(self, timeframe: str) -> int:
        """How far back this provider can serve the given timeframe."""
        ...
