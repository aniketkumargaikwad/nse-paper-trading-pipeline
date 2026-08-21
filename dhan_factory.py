"""Assembles the Dhan stack: credentials -> token manager -> provider -> store.

Kept out of data_provider.py so that module stays a thin selector, and out of
providers/dhan.py so the adapter has no knowledge of Supabase. This is the
only place that knows how the pieces fit together.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Sequence

import pandas as pd

from config import UTC
from instruments import InstrumentError, split_symbol
from kite_client import drop_forming_candle


class SupabaseInstrumentResolver:
    """Looks up Dhan identifiers from the `instruments` table, with a memo.

    Memoised because a multi-year backfill pages 90 days at a time and would
    otherwise re-resolve the same symbol on every window.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._cache: dict[str, tuple[str, str, str]] = {}

    def resolve(self, symbol: str) -> tuple[str, str, str]:
        """Return (security_id, exchange_segment, instrument_type)."""
        if symbol in self._cache:
            return self._cache[symbol]

        split_symbol(symbol)  # validate the shape before spending a round trip

        resp = (
            self._client.table("instruments")
            .select("dhan_security_id,dhan_segment,instrument_type")
            .eq("symbol", symbol)
            .limit(1)
            .execute()
        )
        if not resp.data:
            raise InstrumentError(
                f"{symbol!r} is not in the instruments table. Run "
                "`python backfill.py --refresh-instruments` to load the Dhan "
                "security master, then check the symbol spelling."
            )
        row = resp.data[0]
        resolved = (
            str(row["dhan_security_id"]),
            str(row["dhan_segment"]),
            str(row["instrument_type"]),
        )
        self._cache[symbol] = resolved
        return resolved


def create_dhan_provider(client: Any):
    """Build a ready-to-use DhanProvider from a Supabase client."""
    from dhan_auth import DhanCredentials, DhanTokenManager
    from providers.dhan import DhanProvider
    from supabase_candle_backend import SupabaseCandleBackend

    credentials = DhanCredentials.from_env()
    # The backend doubles as the TokenStore, so concurrent runs share one
    # cached token rather than each minting a new one.
    backend = SupabaseCandleBackend(client)
    return DhanProvider(
        token_manager=DhanTokenManager(credentials, backend),
        instrument_resolver=SupabaseInstrumentResolver(client),
        client_id=credentials.client_id,
    )


def create_candle_store(client: Any):
    """Build a CandleStore backed by Supabase and fed by Dhan."""
    from candle_store import CandleStore
    from supabase_candle_backend import SupabaseCandleBackend

    return CandleStore(
        backend=create_candle_backend(client),
        provider=create_dhan_provider(client),
    )


def create_candle_backend(client: Any):
    """The configured candle backend.

    Defaults to Supabase so nothing changes without a deliberate choice. With
    CANDLE_STORE=parquet, candles move to columnar files while instrument
    lookup, coverage and quality flags still go to Supabase - the Parquet
    backend wraps rather than replaces it.
    """
    from config import get_settings
    from supabase_candle_backend import SupabaseCandleBackend

    settings = get_settings()
    inner = SupabaseCandleBackend(client)
    if settings.candle_store != "parquet":
        return inner

    from parquet_candle_backend import ParquetCandleBackend

    _assert_candle_root_usable(settings.candle_root)
    return ParquetCandleBackend(inner, settings.candle_root)


def _assert_candle_root_usable(root: str) -> None:
    """Refuse to start in parquet mode with nothing to read.

    The failure this prevents is quiet and expensive. A local candle_root does
    not survive a deploy - Railway wipes the filesystem, and the files are
    gitignored because they are regenerable data - so a hosted app configured
    for parquet finds an empty directory, reports no cached candles, and
    refetches millions of bars from the provider on EVERY deploy.

    Nothing about that looks like an error: coverage still says the data
    exists, the run just takes hours and hammers the API. Better to refuse at
    startup and say why.
    """
    import os

    if "://" in root:
        return          # object storage: existence is the store's problem
    if os.path.isdir(root) and any(os.scandir(root)):
        return

    raise RuntimeError(
        f"CANDLE_STORE=parquet but {root!r} is missing or empty. "
        "Nothing would be read from it, so every backtest would refetch "
        "its history from the provider. "
        "\n\n"
        "Locally: run scripts/migrate_candles_to_parquet.py first. "
        "\n"
        "On a host: a local path does not survive a deploy. Either set "
        "CANDLE_ROOT to object storage (s3://... for Cloudflare R2), or "
        "set CANDLE_STORE=supabase, which still holds the candles."
    )


def create_dhan_data_client(client: Any) -> CandleStoreDataClient:
    """Build the engine-facing client: a CandleStore behind the
    MarketDataClient interface the engines already speak."""
    return CandleStoreDataClient(create_candle_store(client))


class CandleStoreDataClient:
    """Presents the MarketDataClient interface on top of a CandleStore.

    The engines (backtest.py, paper_engine.py) were written against the
    fetch_historical_candles / resolve_instrument_tokens interface. The candle
    store deliberately has a different, smaller one. Rather than change either
    - the store stays clean, the engines stay untouched - this adapter sits
    between them.

    The 'instrument token' for a store-backed client is just the symbol: the
    store resolves real Dhan security IDs internally, so callers never see them.
    """

    name = "dhan"
    requires_daily_login = False

    def __init__(self, store: Any) -> None:
        self._store = store

    def resolve_instrument_tokens(
        self, instruments: Sequence[str], today_ist: date
    ) -> dict[str, str]:
        """Symbols are their own handles here; no lookup is needed."""
        return {symbol: symbol for symbol in dict.fromkeys(instruments)}

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
        """Read from the store, dropping the still-forming candle by default.

        closed_only enforces the platform's hard rule that decisions are made
        on CLOSED candles only - never the one still forming.
        """
        df = self._store.get_candles(instrument_token, timeframe, from_utc, to_utc)
        if closed_only:
            df = drop_forming_candle(df, timeframe, now_utc or datetime.now(tz=UTC))
        return df

    def max_history_days(self, timeframe: str) -> int:
        return self._store._provider.max_history_days(timeframe)
