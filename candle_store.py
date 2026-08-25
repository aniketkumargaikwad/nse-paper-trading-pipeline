"""The candle repository - the ONLY module the platform reads candles through.

Everything downstream (backtester, paper engine, dashboard) calls
`get_candles()` and is unaware of which provider produced the data, or whether
the market is currently open. That is what makes backtests runnable at any
hour, on weekends, and on exchange holidays.

This module is also the single swap point if candle storage ever moves from
row-per-candle Postgres to day-blob arrays or Parquet: replace the backend,
change nothing else.

Safety property
---------------
Coverage is advanced ONLY over data genuinely retrieved and stored. If a fetch
fails part-way, everything already stored is kept but coverage stops at the
last good candle. Overstating coverage would silently corrupt every result
built on it while looking perfectly healthy.

Note on provider results: `CandleProvider.fetch` is NOT trimmed to the
requested window - day-granular sources return whole trading days. Extra
candles are real data, so they are stored, and coverage may legitimately
extend past the requested end.

Reads never chase newer data; an explicit `extend_to_now=True` does. This
keeps `get_candles` cheap and offline-capable while leaving refresh and
backfill able to catch up to the present.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from config import UTC, source_timeframe_for
from coverage_math import CoverageRange, extend_coverage, missing_ranges
from data_quality import check_ohlc_sanity, detect_suspected_splits
from price_adjust import Adjustment, apply_adjustments
from providers.base import empty_frame
from resample import resample_candles


# runtime_checkable so the contract can be ASSERTED, not just described.
# This is the seam the storage swap depends on (Supabase or Parquet), and
# an interface nothing verifies is one that drifts.
@runtime_checkable
class CandleBackend(Protocol):
    """Persistence for candles, coverage and quality flags."""

    def instrument_id(self, symbol: str) -> int: ...
    def read_candles(
        self, instrument_id: int, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame | None: ...
    def write_candles(
        self, instrument_id: int, timeframe: str, df: pd.DataFrame
    ) -> None: ...
    def read_coverage(
        self, instrument_id: int, timeframe: str
    ) -> CoverageRange | None: ...
    def write_coverage(
        self, instrument_id: int, timeframe: str, coverage: CoverageRange, source: str
    ) -> None: ...
    def write_quality_flags(self, rows: list[dict[str, Any]]) -> None: ...

    # Optional: backends predating sql/010 may omit it, and CandleStore
    # treats its absence as "no corrections known".
    def read_price_adjustments(
        self, instrument_id: int, timeframe: str
    ) -> list[Adjustment]: ...


class CandleProviderLike(Protocol):
    name: str

    def fetch(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame: ...
    def max_history_days(self, timeframe: str) -> int: ...


class CandleStore:
    """Reads candles, fetching and caching only what is missing."""

    def __init__(self, backend: CandleBackend, provider: CandleProviderLike) -> None:
        self._backend = backend
        self._provider = provider
        # Corrections change only when detection is re-run, but get_candles is
        # called once per symbol per backtest - re-reading them every time
        # would add a database round trip to a path that is otherwise served
        # entirely from cache.
        self._adjustments: dict[tuple[int, str], list[Adjustment]] = {}

    # -- public API ----------------------------------------------------------

    def get_candles(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Return candles for `timeframe`, fetching anything not yet cached.

        Intraday timeframes are served by resampling the stored 5-minute
        base; 'day' is served directly from stored daily candles.
        """
        stored_timeframe = source_timeframe_for(timeframe)
        self.ensure_coverage(symbol, stored_timeframe, from_utc, to_utc)

        instrument_id = self._backend.instrument_id(symbol)
        base = self._backend.read_candles(
            instrument_id, stored_timeframe, from_utc, to_utc
        )
        if base is None or base.empty:
            return empty_frame()

        # Corrections BEFORE resampling. A 1-hour candle built from raw
        # 5-minute prices and then rescaled would be arithmetically the same
        # here, but only because every factor is constant within a day; doing
        # it in this order keeps that from being a thing anyone has to check.
        base = apply_adjustments(
            base, self._price_adjustments(instrument_id, stored_timeframe)
        )

        if timeframe == stored_timeframe:
            return base
        return resample_candles(base, timeframe)

    def _price_adjustments(
        self, instrument_id: int, timeframe: str
    ) -> list[Adjustment]:
        """Corporate-action corrections for this instrument, memoised.

        Dhan's daily feed is adjusted for splits and bonuses; its intraday
        feed is raw, so EICHERMOT's 5-minute candles fall 90% overnight in
        August 2020 for a 1:10 split that changed nobody's wealth. Eighteen
        of the fifty NIFTY 50 symbols carry at least one such break, and a
        backtest spanning one neither errors nor looks odd - it just finds
        the strongest signal in its sample and reports the result.

        Daily candles arrive from Dhan ALREADY adjusted, so they are refused
        outright rather than merely expected to have no rows. sql/010 does
        constrain the table to intraday timeframes, but a read path that
        would silently halve nine years of daily prices if that constraint
        ever moved is not one worth having: dividing an already-correct
        2,178 by ten reinvents the very crash this removes.
        """
        if timeframe == "day":
            return []

        key = (instrument_id, timeframe)
        if key not in self._adjustments:
            reader = getattr(self._backend, "read_price_adjustments", None)
            self._adjustments[key] = (
                list(reader(instrument_id, timeframe)) if reader else []
            )
        return self._adjustments[key]

    def ensure_coverage(
        self,
        symbol: str,
        timeframe: str,
        from_utc: datetime,
        to_utc: datetime,
        *,
        extend_to_now: bool = False,
    ) -> None:
        """Fetch and store whatever part of [from, to] is not already cached.

        The FRONT (older-history) gap is always chased: a deeper backtest
        window genuinely needs older candles that were never fetched.

        The BACK (more-recent) gap is chased only when `extend_to_now` is
        True. Reads leave it alone deliberately - a provider response that
        legitimately falls short of `to_utc` (the market is closed, or the
        last session ended hours ago) would otherwise be re-requested on
        every single call, turning one shortfall into an unbounded stream of
        needless network calls. Discovering new sessions is an explicit
        refresh's job, not a read's.

        Callers that DO want to catch up to the present - the refresh action
        and the backfill CLI - pass `extend_to_now=True`.
        """
        instrument_id = self._backend.instrument_id(symbol)
        covered = self._backend.read_coverage(instrument_id, timeframe)

        # Never ask for more history than the provider actually serves: a
        # silently-empty response would look like "no data exists" rather
        # than "you asked for more than is available".
        earliest = datetime.now(tz=UTC) - timedelta(
            days=self._provider.max_history_days(timeframe)
        )
        effective_from = max(from_utc, earliest)
        if effective_from >= to_utc:
            return

        gaps = missing_ranges(covered, effective_from, to_utc)
        if covered is not None and not extend_to_now:
            # Drop the back-extension gap, if any: see docstring above.
            gaps = [g for g in gaps if g[0] != covered.last_ts]

        for gap_from, gap_to in gaps:
            fetched = self._provider.fetch(symbol, timeframe, gap_from, gap_to)
            if fetched.empty:
                continue

            clean = self._validate_and_flag(instrument_id, timeframe, fetched)
            if clean.empty:
                continue

            self._backend.write_candles(instrument_id, timeframe, clean)

            # Advance coverage only across what we actually stored. The end
            # is the last stored candle, which may legitimately exceed
            # gap_to because providers return whole trading days.
            covered = extend_coverage(
                covered, gap_from, clean.index[-1].to_pydatetime()
            )
            self._backend.write_coverage(
                instrument_id, timeframe, covered, self._provider.name
            )

    # -- internals -----------------------------------------------------------

    def _validate_and_flag(
        self, instrument_id: int, timeframe: str, df: pd.DataFrame
    ) -> pd.DataFrame:
        """Record quality findings, dropping only impossible candles.

        OHLC violations are the ONLY findings dropped rather than merely
        flagged: a candle whose high is below its close never existed.
        A suspected split is flagged and kept — the data may well be right,
        and a silently removed session is its own kind of lie.
        """
        invalid = check_ohlc_sanity(df)
        clean = df
        if invalid:
            bad_timestamps = {f.ts for f in invalid}
            clean = df[~df.index.isin(bad_timestamps)]

        flags = [*invalid, *self._split_flags(instrument_id, timeframe, clean)]
        if flags:
            self._backend.write_quality_flags(
                [f.to_row(instrument_id, timeframe) for f in flags]
            )
        return clean

    def _split_flags(
        self, instrument_id: int, timeframe: str, clean: pd.DataFrame
    ) -> list:
        """Overnight jumps the adjusted daily series does not corroborate.

        Intraday only. The daily feed is already corporate-action adjusted,
        so checking it against itself would flag every genuine large move —
        and it is the DISAGREEMENT between the two feeds that makes an
        unadjusted split detectable at all without a corporate-actions source.

        A missing daily series is not a reason to skip the check. With
        nothing to corroborate against, the jump is flagged as
        uncorroborated: raising a reviewable flag costs a glance, while
        staying silent lets an 80% split artefact reach a backtest as the
        best breakout signal it has ever seen.
        """
        if timeframe == "day" or clean.empty:
            return []
        # Widened by a day at each end on purpose: a daily candle is stamped
        # at the session CLOSE, which falls after the last intraday candle of
        # the same session. Reading the exact intraday span would miss the
        # very day being corroborated and report a genuine move as a split.
        margin = timedelta(days=1)
        adjusted_daily = self._backend.read_candles(
            instrument_id, "day",
            clean.index[0].to_pydatetime() - margin,
            clean.index[-1].to_pydatetime() + margin,
        )
        if adjusted_daily is None:
            adjusted_daily = empty_frame()
        return detect_suspected_splits(clean, adjusted_daily)
