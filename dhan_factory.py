"""Assembles the Dhan stack: credentials -> token manager -> provider -> store.

Kept out of data_provider.py so that module stays a thin selector, and out of
providers/dhan.py so the adapter has no knowledge of Supabase. This is the
only place that knows how the pieces fit together.
"""

from __future__ import annotations

from typing import Any

from instruments import InstrumentError, split_symbol


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
        backend=SupabaseCandleBackend(client),
        provider=create_dhan_provider(client),
    )
