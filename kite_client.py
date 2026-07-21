"""Read-only Kite Connect market-data client.

This module is the ONLY place the system talks to Zerodha, and it exposes
exactly three capabilities: resolve instrument tokens, fetch historical
candles, fetch recent candles. There is deliberately NO way to reach Kite's
order endpoints from here — the wrapped KiteConnect object is private and no
method of this class places, modifies, or cancels orders. If you are looking
for live trading: it does not belong in this system.

Responsibilities
----------------
* Authenticate with the daily access token stored in Supabase by login.py.
* Resolve 'NSE:RELIANCE'-style names to Kite instrument tokens, using the
  Supabase cache first so 15-minute runs avoid Kite's multi-MB instrument
  dump (free-tier friendliness).
* Fetch candles as pandas DataFrames indexed by UTC candle-START time, with
  the still-forming candle dropped by default (decisions on closed candles
  only — the system's hard constraint).
* Retry transient network errors with exponential backoff; convert an
  expired/missing token into one clear, actionable error.
"""

from __future__ import annotations

import math
import time as time_module
from datetime import date, datetime, time, timedelta
from typing import Callable, Sequence

import pandas as pd
from kiteconnect import KiteConnect
from kiteconnect.exceptions import (
    DataException,
    GeneralException,
    InputException,
    KiteException,
    NetworkException,
    TokenException,
)

from config import (
    IST,
    MARKET_CLOSE_IST,
    SUPPORTED_TIMEFRAMES,
    TIMEFRAME_MINUTES,
    TIMEFRAME_TO_KITE_INTERVAL,
    UTC,
    Settings,
)
from db import SupabaseStore

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Kite caps how many days one historical_data call may span, per interval.
# ASSUMPTION: documented Kite Connect v3 limits; verify if a long backtest
# fetch starts failing with InputException.
TIMEFRAME_MAX_DAYS_PER_REQUEST: dict[str, int] = {
    "15m": 200,
    "30m": 200,
    "60m": 400,
    "day": 2000,
}

# Kite rate-limits historical data to ~3 requests/second; we stay under it.
_SECONDS_BETWEEN_CHUNKS = 0.35

# Transient failures worth retrying. InputException (bad request) and
# TokenException (expired session) are NOT here: retrying them cannot help.
_RETRYABLE_EXCEPTIONS = (NetworkException, GeneralException, DataException)
_MAX_ATTEMPTS = 4          # 1 try + 3 retries
_BACKOFF_BASE_SECONDS = 1  # 1s, 2s, 4s


class KiteClientError(RuntimeError):
    """Unrecoverable market-data failure with an actionable message."""


class TokenExpiredError(KiteClientError):
    """The daily Kite access token is missing or no longer valid."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without any network)
# ---------------------------------------------------------------------------


def chunk_date_range(
    from_utc: datetime, to_utc: datetime, max_days: int
) -> list[tuple[datetime, datetime]]:
    """Split [from, to] into consecutive spans of at most `max_days` days.

    Kite rejects requests longer than its per-interval cap, so multi-year
    backtests must be fetched in slices. Spans are inclusive and contiguous;
    any duplicate boundary candles are dropped later by candles_to_dataframe.
    """
    if from_utc >= to_utc:
        raise ValueError(f"from ({from_utc}) must be before to ({to_utc})")
    chunks: list[tuple[datetime, datetime]] = []
    start = from_utc
    step = timedelta(days=max_days)
    while start < to_utc:
        end = min(start + step, to_utc)
        chunks.append((start, end))
        start = end
    return chunks


def candles_to_dataframe(raw: Sequence[dict]) -> pd.DataFrame:
    """Convert Kite's candle payload into our canonical DataFrame.

    Canonical form: columns [open, high, low, close, volume], indexed by the
    candle START time as tz-aware UTC, sorted, duplicates dropped (chunked
    fetches can overlap at boundaries).
    """
    if not raw:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="ts"),
        )
    df = pd.DataFrame(raw)
    missing = {"date", "open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise KiteClientError(
            f"Kite candle payload is missing field(s): {sorted(missing)}. "
            "The Kite API may have changed; check the historical-data docs."
        )
    ts = pd.to_datetime(df["date"])
    # Kite returns IST-offset datetimes; localize defensively if naive.
    ts = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts
    df = df.assign(ts=ts.dt.tz_convert("UTC")).set_index("ts")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    df.index.name = "ts"
    return df


def drop_forming_candle(
    df: pd.DataFrame, timeframe: str, now_utc: datetime
) -> pd.DataFrame:
    """Remove any candle that has not CLOSED yet as of `now_utc`.

    This enforces the system's hard rule: decisions are made on closed
    candles only, never the one still forming (look-ahead/repaint bias).

    * Intraday: a candle starting at T is closed once now >= T + duration.
    * Daily: Kite stamps day candles at midnight IST; the candle is closed
      once the session has ended (15:30 IST) on that date.
    """
    if df.empty:
        return df
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")

    if timeframe == "day":
        now_ist = now_utc.astimezone(IST)
        closed_mask = [
            (ts.astimezone(IST).date() < now_ist.date())
            or (
                ts.astimezone(IST).date() == now_ist.date()
                and now_ist.time() >= MARKET_CLOSE_IST
            )
            for ts in df.index
        ]
        return df[closed_mask]

    duration = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    return df[[ts + duration <= now_utc for ts in df.index]]


def lookback_start_utc(timeframe: str, num_candles: int, now_utc: datetime) -> datetime:
    """Earliest fetch time that safely yields `num_candles` CLOSED candles.

    Candles only form during the 375-minute NSE session, so we convert the
    needed trading time into trading days, then pad generously for weekends
    and holidays. Over-fetching a few days is cheap; under-fetching starves
    the indicators (an EMA(21) fed 10 candles is silently wrong).
    """
    trading_minutes_needed = num_candles * TIMEFRAME_MINUTES[timeframe]
    trading_days_needed = math.ceil(trading_minutes_needed / 375)
    # x7/5 converts trading days to calendar days; +5 absorbs holiday runs.
    calendar_days = math.ceil(trading_days_needed * 7 / 5) + 5
    return now_utc - timedelta(days=calendar_days)


def split_instrument(instrument: str) -> tuple[str, str]:
    """'NSE:RELIANCE' -> ('NSE', 'RELIANCE'), validating the shape."""
    exchange, sep, symbol = instrument.partition(":")
    if not sep or not exchange or not symbol:
        raise KiteClientError(
            f"Malformed instrument {instrument!r}; expected 'EXCHANGE:TRADINGSYMBOL'."
        )
    return exchange, symbol


# ---------------------------------------------------------------------------
# The client
# ---------------------------------------------------------------------------


class MarketDataClient:
    """Authenticated, retrying, READ-ONLY wrapper around Kite Connect."""

    def __init__(
        self,
        kite: KiteConnect,
        store: SupabaseStore | None,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        # `sleep_fn` is injectable so tests can run backoff logic instantly.
        self._kite = kite
        self._store = store
        self._sleep = sleep_fn

    @classmethod
    def from_stored_token(
        cls, settings: Settings, store: SupabaseStore, token_date: date
    ) -> "MarketDataClient":
        """Build a client from the token login.py stored for `token_date`.

        Raises:
            TokenExpiredError: if no token exists for that IST date — the
                morning login step has not been run yet.
        """
        access_token = store.get_daily_token(token_date)
        if not access_token:
            raise TokenExpiredError(
                f"No Kite access token stored for {token_date.isoformat()}. "
                "Run the morning login step first:  python login.py"
            )
        kite = KiteConnect(api_key=settings.kite_api_key)
        kite.set_access_token(access_token)
        return cls(kite, store)

    # -- retry plumbing ------------------------------------------------------

    def _call(self, doing: str, fn: Callable, *args, **kwargs):
        """Run one Kite call with exponential backoff on transient errors."""
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return fn(*args, **kwargs)
            except TokenException as exc:
                # SEBI-mandated daily expiry, or a revoked session. Retrying
                # cannot fix it; only a fresh morning login can.
                raise TokenExpiredError(
                    f"Kite session rejected while {doing}: {exc}. The daily "
                    "token has expired or was invalidated. Run: python login.py"
                ) from exc
            except InputException as exc:
                raise KiteClientError(
                    f"Kite rejected the request while {doing}: {exc}. This is "
                    "a bad parameter (wrong instrument token, interval, or a "
                    "date range longer than Kite allows) — not a network issue."
                ) from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                if attempt < _MAX_ATTEMPTS:
                    delay = _BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                    self._sleep(delay)
            except KiteException as exc:  # unknown Kite error type: fail loud
                raise KiteClientError(f"Kite error while {doing}: {exc}") from exc
        raise KiteClientError(
            f"Still failing after {_MAX_ATTEMPTS} attempts while {doing}: "
            f"{last_exc}. Kite or the network is likely down; the run should "
            "exit and let the next scheduled run try again."
        )

    # -- instruments -----------------------------------------------------------

    def resolve_instrument_tokens(
        self, instruments: Sequence[str], today_ist: date
    ) -> dict[str, int]:
        """Map 'NSE:RELIANCE'-style names to Kite instrument tokens.

        Cache-first: tokens already in Supabase are reused (equity tokens are
        stable; an F&O contract gets a NEW symbol each expiry, so it simply
        misses the cache and is resolved fresh). Only on a miss do we download
        an exchange's instrument dump — and then once per exchange, not per
        symbol.
        """
        wanted = list(dict.fromkeys(instruments))  # dedupe, keep order
        resolved: dict[str, int] = {}
        if self._store is not None:
            resolved = self._store.get_cached_instrument_tokens(wanted)

        missing = [inst for inst in wanted if inst not in resolved]
        if not missing:
            return resolved

        by_exchange: dict[str, list[str]] = {}
        for inst in missing:
            exchange, symbol = split_instrument(inst)
            by_exchange.setdefault(exchange, []).append(symbol)

        newly_resolved: dict[str, int] = {}
        for exchange, symbols in by_exchange.items():
            dump = self._call(
                f"downloading the {exchange} instrument list",
                self._kite.instruments,
                exchange,
            )
            lookup = {row["tradingsymbol"]: int(row["instrument_token"]) for row in dump}
            for symbol in symbols:
                token = lookup.get(symbol)
                if token is None:
                    raise KiteClientError(
                        f"Instrument '{exchange}:{symbol}' not found on Kite. "
                        "Check the tradingsymbol spelling in strategies.yaml "
                        "(it must match Kite exactly, e.g. NSE:RELIANCE, and "
                        "F&O contracts must not be expired)."
                    )
                newly_resolved[f"{exchange}:{symbol}"] = token

        if self._store is not None and newly_resolved:
            self._store.upsert_instrument_tokens(newly_resolved, refreshed_on=today_ist)
        resolved.update(newly_resolved)
        return resolved

    # -- candles -------------------------------------------------------------------

    def fetch_historical_candles(
        self,
        instrument_token: int,
        timeframe: str,
        from_utc: datetime,
        to_utc: datetime,
        *,
        closed_only: bool = True,
        now_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch candles for [from_utc, to_utc], chunked to Kite's span limits.

        Returns the canonical DataFrame (UTC index, ohlcv float columns).
        With closed_only=True (the default, and what every engine should
        use), the still-forming candle is removed.
        """
        if timeframe not in SUPPORTED_TIMEFRAMES:
            raise KiteClientError(
                f"Unsupported timeframe {timeframe!r}. "
                f"Allowed: {', '.join(SUPPORTED_TIMEFRAMES)}"
            )
        interval = TIMEFRAME_TO_KITE_INTERVAL[timeframe]
        max_days = TIMEFRAME_MAX_DAYS_PER_REQUEST[timeframe]

        frames: list[pd.DataFrame] = []
        chunks = chunk_date_range(from_utc, to_utc, max_days)
        for i, (chunk_from, chunk_to) in enumerate(chunks):
            if i > 0:
                self._sleep(_SECONDS_BETWEEN_CHUNKS)  # stay under rate limit
            raw = self._call(
                f"fetching {interval} candles for token {instrument_token}",
                self._kite.historical_data,
                instrument_token,
                # Kite expects naive IST datetimes ('yyyy-mm-dd hh:mm:ss').
                chunk_from.astimezone(IST).replace(tzinfo=None),
                chunk_to.astimezone(IST).replace(tzinfo=None),
                interval,
            )
            frames.append(candles_to_dataframe(raw))

        df = pd.concat(frames) if frames else candles_to_dataframe([])
        df = df[~df.index.duplicated(keep="first")].sort_index()

        if closed_only:
            df = drop_forming_candle(df, timeframe, now_utc or datetime.now(tz=UTC))
        return df

    def fetch_recent_candles(
        self,
        instrument_token: int,
        timeframe: str,
        num_candles: int,
        *,
        now_utc: datetime | None = None,
    ) -> pd.DataFrame:
        """Fetch at least `num_candles` most-recent CLOSED candles.

        The paper engine calls this every 15 minutes with num_candles sized
        to its longest indicator lookback. May return MORE than requested
        (callers slice) and can return fewer only for a newly listed symbol
        with a short history — signals.py treats insufficient history as
        'no signal', never as an error.
        """
        now = now_utc or datetime.now(tz=UTC)
        from_utc = lookback_start_utc(timeframe, num_candles, now)
        return self.fetch_historical_candles(
            instrument_token, timeframe, from_utc, now,
            closed_only=True, now_utc=now,
        )
