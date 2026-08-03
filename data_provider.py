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

    if provider == "kite":
        from kite_client import MarketDataClient

        return MarketDataClient.from_stored_token(settings, store, today_ist)

    raise RuntimeError(
        f"Unknown DATA_PROVIDER {provider!r}. Supported: yfinance, kite."
    )


def describe_provider(settings: Settings) -> str:
    """One-line human summary for logs and audit rows (never includes keys)."""
    if settings.data_provider == "yfinance":
        return "yfinance (free; 15m/30m history limited to ~60 days)"
    return "kite (Kite Connect; requires the daily login token)"
