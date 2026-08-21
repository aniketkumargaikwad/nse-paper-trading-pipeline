"""Chooses the market-data provider. The ONE place providers are wired up.

Both providers expose the identical read-only interface:

    resolve_instrument_tokens(instruments, today_ist) -> dict[str, handle]
    fetch_historical_candles(handle, timeframe, from_utc, to_utc, ...) -> DataFrame
    fetch_recent_candles(handle, timeframe, num_candles, ...)          -> DataFrame
    max_history_days(timeframe)                                        -> int

`handle` is opaque to callers (an int instrument_token for Kite, a Yahoo
symbol string for yfinance) — engines just pass it straight back. Because
both return the same canonical DataFrame, nothing downstream knows or cares
which provider is active.

Selected with DATA_PROVIDER in .env / GitHub Secrets:
    yfinance (default) - free, no keys, no daily login
    kite               - paid Kite Connect plan + daily login.py
    dhan               - free, 5y intraday history, cached in Supabase

`dhan` is backed by a `CandleStore` (get_candles/ensure_coverage) internally,
but is presented to callers through an adapter (CandleStoreDataClient in
dhan_factory.py) so every provider satisfies the same MarketDataClient
interface above.
"""

from __future__ import annotations

from datetime import date

from config import Settings
from db import SupabaseStore


def create_data_client(settings: Settings, store: SupabaseStore, today_ist: date):
    """Build the configured provider, ready to fetch candles.

    Raises:
        TokenExpiredError: (kite only) when today's login has not been done.
        ConfigError-equivalent RuntimeError: for an unknown provider name.
    """
    provider = settings.data_provider

    if provider == "yfinance":
        # Imported lazily so a Kite-only user never needs yfinance installed
        # (and vice versa) — keeps failures scoped to the provider in use.
        from yfinance_client import YFinanceMarketDataClient

        return YFinanceMarketDataClient()

    if provider == "dhan":
        # Dhan reads through the candle store, so backtests hit Supabase and
        # work with the market closed. That requires a Supabase connection.
        from dhan_factory import create_dhan_data_client

        if store is None:
            raise RuntimeError(
                "The dhan provider needs a Supabase connection (it caches "
                "candles there). Remove --no-db, or set DATA_PROVIDER=yfinance."
            )
        return create_dhan_data_client(store._client)

    if provider == "kite":
        from kite_client import MarketDataClient

        return MarketDataClient.from_stored_token(settings, store, today_ist)

    raise RuntimeError(
        f"Unknown DATA_PROVIDER {provider!r}. Supported: yfinance, kite, dhan."
    )


def describe_provider(settings: Settings) -> str:
    """One-line human summary for logs and audit rows (never includes keys)."""
    if settings.data_provider == "yfinance":
        return "yfinance (free; 15m/30m history limited to ~60 days)"
    if settings.data_provider == "dhan":
        # Names the actual candle store: saying "cached in Supabase" while
        # running on parquet is a small lie that makes a slow run look
        # inexplicable.
        where = getattr(settings, "candle_store", "supabase")
        return f"dhan (5 years of 5-minute history, cached in {where})"
    return "kite (Kite Connect; requires the daily login token)"
