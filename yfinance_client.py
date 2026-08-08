"""FREE market-data provider backed by Yahoo Finance (yfinance).

This is the default data source so the whole pipeline can run at zero cost
with **no broker account, no API key, and no daily login**. It returns the
exact same canonical DataFrame as the Kite provider, so indicators, signals,
the backtester, and the paper engine cannot tell which source is in use.

WHAT YOU GIVE UP VERSUS A PAID BROKER FEED (know these before trusting it)
-------------------------------------------------------------------------
* **Short intraday history.** Yahoo only serves ~60 days of 15m/30m candles
  and ~730 days of 60m. Daily goes back years. So deep backtests belong on
  the 60m/day timeframes; a 15m backtest is a small sample and the kill
  rules will usually (correctly) flag it as thin evidence.
* **Unofficial API.** Yahoo can change or throttle this endpoint without
  notice. Failures here are loud, and the engine simply records a skip.
* **Lower data quality.** Occasional missing/NaN bars and odd volume values,
  especially at session edges. NaN bars are dropped rather than guessed at.
* **Prices are split/dividend adjusted** (`auto_adjust=True`). This is
  deliberate: unadjusted history makes a 1:5 split look like an 80% crash
  and would fire fake stop-losses in backtests. The trade-off is that
  historical prices won't match the exact rupee prices traded that day —
  which is fine here, because every rule is percentage or indicator based.

Upgrade path: set `DATA_PROVIDER=kite` once you're paying for Kite Connect.
No other change is needed.
"""

from __future__ import annotations

import time as time_module
from datetime import date, datetime, timedelta
from typing import Callable, Sequence

import pandas as pd

from config import IST, UTC
from kite_client import (
    KiteClientError,  # shared error type so engines catch ONE exception class
    drop_forming_candle,
    lookback_start_utc,
    split_instrument,
)

# Our timeframe label -> yfinance `interval` string.
TIMEFRAME_TO_YF_INTERVAL: dict[str, str] = {
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "60m": "1h",
    "day": "1d",
}

# How far back each interval may reach, in days.
#
# VERIFIED empirically against the live API on 2026-08-03: Yahoo documents
# "within the last 60 days" for 15m/30m and 730 for 60m, but the boundary is
# EXCLUSIVE — a start of exactly -60d/-730d returns ZERO rows, while -59d and
# -729d work. A silent empty frame looks like "no signals" instead of "no
# data", which is the worst possible failure, so we keep a 2-day safety
# margin. Cost: ~1.5% less history. Benefit: the boundary can never bite,
# even with IST/UTC clock skew between us and Yahoo's servers.
YF_MAX_HISTORY_DAYS: dict[str, int] = {
    "5m": 58,
    "15m": 58,
    "30m": 58,
    "60m": 728,
    "day": 10_000,  # effectively unlimited for our purposes
}

# Exchange prefix in strategies.yaml -> Yahoo symbol suffix.
EXCHANGE_SUFFIX: dict[str, str] = {"NSE": ".NS", "BSE": ".BO"}

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1
_SECONDS_BETWEEN_CALLS = 0.2  # be polite to a free, unofficial endpoint


class DataUnavailableError(KiteClientError):
    """Yahoo returned nothing usable for this symbol/timeframe."""


def to_yahoo_symbol(instrument: str) -> str:
    """'NSE:RELIANCE' -> 'RELIANCE.NS'. Raises for unsupported exchanges."""
    exchange, symbol = split_instrument(instrument)
    suffix = EXCHANGE_SUFFIX.get(exchange)
    if suffix is None:
        raise KiteClientError(
            f"The free yfinance provider cannot fetch {instrument!r}: only "
            f"{', '.join(sorted(EXCHANGE_SUFFIX))} equities are supported "
            "(Yahoo has no reliable NSE F&O/derivatives feed). Use cash-market "
            "symbols, or switch to DATA_PROVIDER=kite for derivatives."
        )
    return f"{symbol}{suffix}"


def normalize_yf_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """Convert a yfinance result into our canonical candle DataFrame.

    Canonical form: columns [open, high, low, close, volume] as floats,
    indexed by candle START time as tz-aware UTC, sorted, deduplicated.
    """
    empty = pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], tz="UTC", name="ts"),
    )
    if raw is None or raw.empty:
        return empty

    df = raw.copy()

    # yfinance returns MultiIndex columns (field, ticker) even for a single
    # ticker in recent versions; flatten to the field level.
    if isinstance(df.columns, pd.MultiIndex):
        level0 = set(df.columns.get_level_values(0))
        # Layout is either (field, ticker) or (ticker, field) depending on
        # how download() was called; pick whichever level holds the fields.
        if {"Open", "Close"} & level0:
            df.columns = df.columns.get_level_values(0)
        else:
            df.columns = df.columns.get_level_values(-1)

    df.columns = [str(c).lower().replace(" ", "_") for c in df.columns]
    missing = {"open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise DataUnavailableError(
            f"Yahoo response is missing column(s): {sorted(missing)}. "
            "The yfinance API may have changed; try upgrading the package."
        )
    df = df[["open", "high", "low", "close", "volume"]]

    # Index -> tz-aware UTC. Intraday comes back in Asia/Kolkata already;
    # daily candles are tz-naive midnights and must be read as IST.
    index = pd.to_datetime(df.index)
    index = index.tz_localize(IST) if index.tz is None else index
    df.index = index.tz_convert("UTC")
    df.index.name = "ts"

    df = df.astype(float)
    # Yahoo emits all-NaN rows for halts/holidays. Drop them rather than
    # forward-filling: a fabricated candle could fire a fake signal.
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df if not df.empty else empty


class YFinanceMarketDataClient:
    """Read-only Yahoo Finance data source (same interface as the Kite one).

    There is no authentication and no order capability of any kind here —
    yfinance is a market-data library only.
    """

    #: Engines check this to decide whether login.py is needed.
    requires_daily_login = False
    name = "yfinance"

    def __init__(self, sleep_fn: Callable[[float], None] = time_module.sleep) -> None:
        self._sleep = sleep_fn  # injectable so tests never really sleep

    # -- instruments ---------------------------------------------------------

    def resolve_instrument_tokens(
        self, instruments: Sequence[str], today_ist: date
    ) -> dict[str, str]:
        """Map 'NSE:RELIANCE' -> 'RELIANCE.NS'.

        Yahoo needs no token lookup, so this is a pure string transform and
        costs zero API calls (the Kite provider's cache exists precisely to
        avoid the call this provider never has to make).
        """
        return {inst: to_yahoo_symbol(inst) for inst in dict.fromkeys(instruments)}

    # -- candles -------------------------------------------------------------

    def _download(self, symbol: str, interval: str, start: datetime, end: datetime):
        """One yfinance call with retries on transient failures."""
        import logging

        import yfinance as yf

        # yfinance prints multi-line "possibly delisted" blocks to stderr for
        # any empty result, which buries our own actionable messages. We
        # already detect and report empty frames ourselves, so quieten it.
        logging.getLogger("yfinance").setLevel(logging.CRITICAL)

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return yf.download(
                    symbol,
                    start=start.astimezone(IST).replace(tzinfo=None),
                    end=end.astimezone(IST).replace(tzinfo=None),
                    interval=interval,
                    progress=False,
                    auto_adjust=True,   # see module docstring: split-safe
                    threads=False,      # deterministic, gentler on the endpoint
                )
            except Exception as exc:  # yfinance raises assorted network types
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    self._sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        raise DataUnavailableError(
            f"Yahoo Finance failed for {symbol} after {_MAX_ATTEMPTS} attempts: "
            f"{last_exc}. This free endpoint is unofficial and can throttle; "
            "the next scheduled run will try again."
        )

    def fetch_historical_candles(
        self,
        instrument_token: str,
        timeframe: str,
        from_utc: datetime,
        to_utc: datetime,
        *,
        closed_only: bool = True,
        now_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch candles for [from_utc, to_utc], clamped to Yahoo's limits.

        `instrument_token` is the Yahoo symbol produced by
        resolve_instrument_tokens (the parameter keeps the Kite provider's
        name so the engines can use either provider unchanged).
        """
        if timeframe not in TIMEFRAME_TO_YF_INTERVAL:
            raise KiteClientError(
                f"The free yfinance provider cannot serve {timeframe!r}. "
                f"It supports: {', '.join(TIMEFRAME_TO_YF_INTERVAL)}. "
                "(Yahoo has no 25-minute interval; use the dhan provider, "
                "which derives it from a stored 5-minute base.)"
            )
        now = now_utc or datetime.now(tz=UTC)
        interval = TIMEFRAME_TO_YF_INTERVAL[timeframe]

        # Clamp the start date to what Yahoo will actually serve. Asking for
        # more silently returns an EMPTY frame, which would look like "no
        # signals" instead of "no data" — so we clamp explicitly and let the
        # caller report the shortened window.
        earliest = now - timedelta(days=YF_MAX_HISTORY_DAYS[timeframe])
        effective_from = max(from_utc, earliest)
        self.last_window_was_clamped = effective_from > from_utc
        self.last_effective_from = effective_from

        if effective_from >= to_utc:
            return normalize_yf_frame(None)

        self._sleep(_SECONDS_BETWEEN_CALLS)
        raw = self._download(instrument_token, interval, effective_from, to_utc)
        df = normalize_yf_frame(raw)

        if closed_only:
            df = drop_forming_candle(df, timeframe, now)
        return df

    def fetch_recent_candles(
        self,
        instrument_token: str,
        timeframe: str,
        num_candles: int,
        *,
        now_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch at least `num_candles` most-recent CLOSED candles."""
        now = now_utc or datetime.now(tz=UTC)
        from_utc = lookback_start_utc(timeframe, num_candles, now)
        return self.fetch_historical_candles(
            instrument_token, timeframe, from_utc, now,
            closed_only=True, now_utc=now,
        )

    def max_history_days(self, timeframe: str) -> int:
        """How far back this provider can serve the given timeframe."""
        if timeframe not in YF_MAX_HISTORY_DAYS:
            raise KiteClientError(
                f"The free yfinance provider cannot serve {timeframe!r}. "
                f"It supports: {', '.join(TIMEFRAME_TO_YF_INTERVAL)}. "
                "(Yahoo has no 25-minute interval; use the dhan provider, "
                "which derives it from a stored 5-minute base.)"
            )
        return YF_MAX_HISTORY_DAYS[timeframe]
