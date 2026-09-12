"""Read the frozen price history without ever fetching.

candle_store.CandleStore.get_candles fills gaps from the data provider. With
the Dhan subscription lapsed and prices deliberately frozen, a gap must show
up as a missing combination, never as a network call - so research reads the
parquet files directly, applies the stored corrections, and resamples.

The reader holds only plain data, so it can be pickled into worker processes;
everything that needs the database happens once, in the parent, via the
loaders below.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from config import MINUTE_TIMEFRAME, SUPPORTED_TIMEFRAMES, ConfigError, source_timeframe_for
from parquet_candle_backend import ParquetCandleBackend
from price_adjust import Adjustment, apply_adjustments
from providers.base import empty_frame
from resample import resample_candles

_CHUNK = 100   # symbols or ids per Supabase `in` filter

# PostgREST returns at most 1,000 rows per request by default. A page this
# full is a sign the result was truncated, not that an instrument genuinely
# has that many corrections - see the check in load_adjustments.
_POSTGREST_PAGE_LIMIT = 1000


@dataclass(frozen=True)
class FrozenPriceReader:
    root: str
    instrument_ids: dict[str, int]
    adjustments: dict[int, list[Adjustment]]    # 5-minute corrections by instrument id
    index_symbols: frozenset[str]

    def __post_init__(self) -> None:
        if "://" in self.root:
            raise ValueError(
                f"FrozenPriceReader root {self.root!r} looks like a remote "
                "store URL; research reads a local parquet store only, so a "
                "misconfigured root should fail once here rather than on "
                "every worker's first call."
            )

    def candles(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Candles in [from_utc, to_utc]. Empty when nothing is stored.

        Raises KeyError for a symbol with no instrument id, config.ConfigError
        for an unsupported timeframe (checked up front so a stock symbol and
        an index symbol fail identically - a typo must never read as "no
        data"), and ValueError for the unsupported 1-minute timeframe.
        """
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise ConfigError(
                f"Unsupported timeframe {timeframe!r}. "
                f"Allowed: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )
        if timeframe == MINUTE_TIMEFRAME:
            raise ValueError(
                "1m candles are not supported by FrozenPriceReader: 1m is not "
                "resampled from the 5-minute base (config.STORED_TIMEFRAMES "
                "keeps it independent), so the 5-minute corrections loaded by "
                "load_adjustments cannot be applied to it, and only two "
                "symbols hold 1-minute data at all."
            )

        instrument_id = self.instrument_ids[symbol]
        backend = ParquetCandleBackend(None, self.root)

        if symbol in self.index_symbols:
            # Index timeframes are stored exactly as downloaded (60m and day).
            frame = backend.read_candles(instrument_id, timeframe, from_utc, to_utc)
            return frame if frame is not None else empty_frame()

        stored = source_timeframe_for(timeframe)
        base = backend.read_candles(instrument_id, stored, from_utc, to_utc)
        if base is None or base.empty:
            return empty_frame()
        if stored != "day":
            # Daily candles arrive already adjusted; see candle_store._price_adjustments.
            base = apply_adjustments(base, self.adjustments.get(instrument_id, []))
        return base if timeframe == stored else resample_candles(base, timeframe)


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_instrument_ids(client: Any, symbols: Iterable[str]) -> dict[str, int]:
    """'NSE:RELIANCE' -> instruments.id for every symbol, or LookupError."""
    wanted = sorted(set(symbols))
    ids: dict[str, int] = {}
    for chunk in _chunks(wanted, _CHUNK):
        rows = (
            client.table("instruments").select("id,symbol")
            .in_("symbol", chunk).execute().data
        )
        ids.update({row["symbol"]: int(row["id"]) for row in rows})
    missing = [s for s in wanted if s not in ids]
    if missing:
        raise LookupError(f"not in the instruments table: {', '.join(missing)}")
    return ids


def load_adjustments(client: Any, instrument_ids: Iterable[int]) -> dict[int, list[Adjustment]]:
    """5-minute corporate-action corrections, oldest first, for every id.

    Deliberately diverges from supabase_candle_backend.read_price_adjustments,
    which swallows a missing price_adjustments table and returns []. That is
    right for the live platform, where an optional table should not take the
    whole thing down. It is wrong here: silently returning uncorrected
    intraday prices across a split is exactly what price_adjust.py exists to
    prevent, so a failure here propagates instead of being swallowed.
    """
    wanted = sorted(set(instrument_ids))
    out: dict[int, list[Adjustment]] = {i: [] for i in wanted}
    for chunk in _chunks(wanted, _CHUNK):
        rows = (
            client.table("price_adjustments")
            .select("instrument_id,effective_from,effective_to,price_factor,volume_factor,sample_days")
            .in_("instrument_id", chunk)
            .eq("timeframe", "5m")
            .order("effective_from")
            .execute().data
        )
        if len(rows) >= _POSTGREST_PAGE_LIMIT:
            raise RuntimeError(
                f"price_adjustments returned {len(rows)} rows for one chunk, "
                f"which looks like a truncated PostgREST page rather than "
                f"genuine data - lower _CHUNK in research/prices.py so a "
                f"chunk's adjustments cannot reach this limit."
            )
        for row in rows:
            out[int(row["instrument_id"])].append(Adjustment(
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=date.fromisoformat(row["effective_to"]),
                price_factor=float(row["price_factor"]),
                volume_factor=float(row["volume_factor"] or 1.0),
                sample_days=int(row["sample_days"]),
            ))
    return out
