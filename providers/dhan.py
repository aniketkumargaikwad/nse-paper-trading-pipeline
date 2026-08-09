"""Dhan (DhanHQ v2) market-data adapter. READ-ONLY.

This module reaches exactly two endpoints - intraday charts and historical
(daily) charts. It contains no order code of any kind, and none may be added:
live execution belongs behind a separate, deliberately-added adapter.

Shape notes
-----------
* Dhan returns PARALLEL ARRAYS (open[], high[], ..., timestamp[]), not a list
  of candle objects.
* A single request may span at most 90 days, so long ranges are paged.
* Only the STORED timeframes ('5m' and 'day') are fetchable; every other
  intraday timeframe is derived by resampling the 5-minute base.

Timestamp interpretation
------------------------
Dhan sends epoch SECONDS, read here as true UTC instants. This is UNVERIFIED:
Dhan credentials are not yet configured, so no live response has been
captured. Decoding them as already-IST would shift every candle by 5h30m and
silently corrupt every backtest. Before the first live backfill, run
scripts/capture_dhan_fixture.py so that
tests/fixtures/dhan_intraday_5m.json exists and
test_parser_handles_the_recorded_real_response stops skipping.
"""

from __future__ import annotations

import time as time_module
from datetime import datetime, timedelta
from typing import Any, Callable, Protocol

import pandas as pd
import requests

from config import IST, STORED_TIMEFRAMES
from dhan_auth import DHAN_API_BASE
from providers.base import OHLCV_COLUMNS, ProviderError, canonical_frame, empty_frame

# Dhan serves at most 90 days of intraday data per request.
MAX_DAYS_PER_REQUEST = 90

# Documented history depth: 5 years of intraday, daily to inception.
INTRADAY_HISTORY_DAYS = 5 * 365
DAILY_HISTORY_DAYS = 40 * 365

# (connect, read) - a scalar would apply the full timeout to EACH phase.
REQUEST_TIMEOUT_SECONDS = (5, 30)
_SECONDS_BETWEEN_PAGES = 0.35   # stay well under Dhan's rate limits
_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1
# A 429 means a quota bucket is empty, not that the server hiccuped. Dhan's
# limits include per-minute buckets, so 1/2/4s exhausts all four attempts
# well inside a window that simply needs waiting out.
_RATE_LIMIT_BACKOFF_SECONDS = 20
_MAX_BACKOFF_SECONDS = 120

INTRADAY_ENDPOINT = f"{DHAN_API_BASE}/charts/intraday"
HISTORICAL_ENDPOINT = f"{DHAN_API_BASE}/charts/historical"

# Our timeframe -> Dhan's `interval` value (intraday only).
TIMEFRAME_TO_DHAN_INTERVAL: dict[str, str] = {"5m": "5"}

REQUIRED_PAYLOAD_KEYS = ("open", "high", "low", "close", "volume", "timestamp")


class TokenProvider(Protocol):
    def get_access_token(self) -> str: ...


class InstrumentResolver(Protocol):
    def resolve(self, symbol: str) -> tuple[str, str, str]:
        """Return (security_id, exchange_segment, instrument_type)."""
        ...


def date_windows(
    from_utc: datetime, to_utc: datetime, max_days: int = MAX_DAYS_PER_REQUEST
) -> list[tuple[datetime, datetime]]:
    """Split a range into contiguous windows Dhan will accept."""
    if from_utc >= to_utc:
        raise ValueError(f"from ({from_utc}) must be before to ({to_utc})")
    windows: list[tuple[datetime, datetime]] = []
    start = from_utc
    step = timedelta(days=max_days)
    while start < to_utc:
        end = min(start + step, to_utc)
        windows.append((start, end))
        start = end
    return windows


def parse_candle_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Convert Dhan's parallel arrays into the canonical frame."""
    missing = [k for k in REQUIRED_PAYLOAD_KEYS if k not in payload]
    if missing:
        raise ProviderError(
            f"Dhan candle payload is missing key(s): {missing}. "
            "The API shape may have changed; check the DhanHQ v2 chart docs."
        )

    lengths = {k: len(payload[k]) for k in REQUIRED_PAYLOAD_KEYS}
    if len(set(lengths.values())) > 1:
        raise ProviderError(
            f"Dhan candle arrays are not the same length: {lengths}. "
            "Refusing to guess how they align."
        )
    if lengths["timestamp"] == 0:
        return empty_frame()

    # Epoch SECONDS read as true UTC instants. UNVERIFIED against a live
    # response - see the module docstring. The fixture test is the real
    # guard; the unit test below cannot catch this because its own fixture is
    # built under the same assumption.
    index = pd.to_datetime(payload["timestamp"], unit="s", utc=True)
    frame = pd.DataFrame({name: payload[name] for name in OHLCV_COLUMNS}, index=index)
    return canonical_frame(frame)


class DhanProvider:
    """Read-only Dhan candle source satisfying the CandleProvider protocol."""

    name = "dhan"

    def __init__(
        self,
        token_manager: TokenProvider,
        instrument_resolver: InstrumentResolver,
        client_id: str,
        http: Any = requests,
        sleep_fn: Callable[[float], None] = time_module.sleep,
    ) -> None:
        # http and sleep_fn are injectable so tests never touch the network
        # and never actually sleep.
        self._tokens = token_manager
        self._instruments = instrument_resolver
        # Not sent as a header: the chart endpoints take no client-id (that
        # belongs to the auth API). Kept as a constructor parameter because
        # callers still pass it and may need it for other purposes later.
        self._client_id = client_id
        self._http = http
        self._sleep = sleep_fn

    def max_history_days(self, timeframe: str) -> int:
        return DAILY_HISTORY_DAYS if timeframe == "day" else INTRADAY_HISTORY_DAYS

    def fetch(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Fetch canonical candles for [from_utc, to_utc], paging as needed."""
        if timeframe not in STORED_TIMEFRAMES:
            raise ProviderError(
                f"Dhan is only asked for stored timeframes "
                f"({', '.join(STORED_TIMEFRAMES)}); {timeframe!r} is derived by "
                "resampling and must not be fetched."
            )

        security_id, segment, instrument_type = self._instruments.resolve(symbol)
        endpoint = HISTORICAL_ENDPOINT if timeframe == "day" else INTRADAY_ENDPOINT

        frames: list[pd.DataFrame] = []
        for i, (window_from, window_to) in enumerate(date_windows(from_utc, to_utc)):
            if i > 0:
                self._sleep(_SECONDS_BETWEEN_PAGES)
            body: dict[str, Any] = {
                "securityId": security_id,
                "exchangeSegment": segment,
                "instrument": instrument_type,
                # Open interest is an F&O concept; we trade cash equity and
                # never use it, but the API expects the field.
                "oi": False,
            }
            if timeframe == "day":
                body["expiryCode"] = 0
                body["fromDate"] = window_from.astimezone(IST).strftime("%Y-%m-%d")
                body["toDate"] = window_to.astimezone(IST).strftime("%Y-%m-%d")
            else:
                body["interval"] = TIMEFRAME_TO_DHAN_INTERVAL[timeframe]
                # Intraday windows are datetime-precise, unlike daily.
                body["fromDate"] = window_from.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
                body["toDate"] = window_to.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")

            payload = self._post(
                endpoint, body, f"fetching {timeframe} candles for {symbol}"
            )
            frames.append(parse_candle_payload(payload))

        frames = [f for f in frames if not f.empty]
        if not frames:
            # Every window was a holiday/pre-listing span. Concatenating empty
            # frames is deprecated in pandas and would eventually make the
            # OHLCV columns object-dtype.
            return empty_frame()
        combined = pd.concat(frames)
        # Not trimmed to [from_utc, to_utc]: Dhan returns whole days, and
        # callers (candle_store, a later task) own reconciling against the
        # exact requested range - this adapter's job ends at "canonical and
        # deduplicated".
        return combined[~combined.index.duplicated(keep="first")].sort_index()

    def _post(self, url: str, body: dict[str, Any], doing: str) -> dict[str, Any]:
        """One request with backoff on transient failures.

        A requests exception must never escape carrying its PreparedRequest,
        whose body holds credentials - so transport errors are converted here
        and the original is dropped entirely.
        """
        # One token read per request, not one per attempt: a 401 is a
        # non-retryable 4xx here, so re-reading between attempts cannot help -
        # it only adds a token-store round-trip.
        headers = {
            # Per the chart-API docs: no client-id header here
            # (that belongs to the auth endpoints).
            "access-token": self._tokens.get_access_token(),
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        last_error: str | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            response = None
            transport_error: str | None = None
            rate_limited = False
            retry_after: float | None = None
            try:
                response = self._http.post(
                    url,
                    headers=headers,
                    json=body,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.exceptions.RequestException as exc:
                transport_error = type(exc).__name__

            if transport_error is not None:
                last_error = transport_error
            else:
                status = response.status_code
                if status < 400:
                    decoded = False
                    try:
                        payload = response.json()
                        decoded = True
                    except ValueError:
                        pass
                    if decoded:
                        return payload
                    # Raised OUTSIDE the except block, per dhan_auth._post:
                    # `from None` only sets __suppress_context__, leaving a
                    # requests exception reachable via __context__.
                    raise ProviderError(f"Dhan returned non-JSON while {doing}")
                # 4xx other than rate limiting will not improve on retry.
                if status < 500 and status != 429:
                    hint = ""
                    try:
                        detail = response.json()
                    except ValueError:
                        detail = None
                    if isinstance(detail, dict):
                        code = detail.get("errorCode") or ""
                        text = detail.get("errorMessage") or ""
                        if text:
                            hint = f" Dhan says: {text}"
                        if code == "DH-902" or "Data API" in text:
                            hint += (
                                " Data APIs are a paid Dhan subscription "
                                "(~Rs.499+GST/month) - subscribe on the 'Data "
                                "APIs' tab at web.dhan.co -> Profile -> DhanHQ "
                                "Trading APIs."
                            )
                    if not hint:
                        # No JSON detail from Dhan to relay - fall back to the
                        # generic explanation so the reader isn't left blank.
                        hint = (
                            " This is a bad parameter or an auth problem, "
                            "not a network issue."
                        )
                    raise ProviderError(
                        f"Dhan rejected the request while {doing} (HTTP {status})."
                        + hint
                    )
                last_error = f"HTTP {status}"
                if status == 429:
                    rate_limited = True
                    raw_retry_after = (getattr(response, "headers", None) or {}).get(
                        "Retry-After"
                    )
                    if raw_retry_after is not None:
                        try:
                            retry_after = float(raw_retry_after)
                        except ValueError:
                            retry_after = None

            if attempt < _MAX_ATTEMPTS:
                base = _RATE_LIMIT_BACKOFF_SECONDS if rate_limited else _BACKOFF_BASE_SECONDS
                delay = retry_after or base * 2 ** (attempt - 1)
                self._sleep(min(delay, _MAX_BACKOFF_SECONDS))

        raise ProviderError(
            f"Dhan still failing after {_MAX_ATTEMPTS} attempts while {doing}: "
            f"{last_error}."
        )
