"""NIFTY, BANKNIFTY and India VIX minute by minute, from Dhan (design §2.1).

Dhan's ids from its scrip master (checked 9 Oct 2026): NIFTY 13, BANKNIFTY 25,
INDIA VIX 21, all on the IDX_I segment. Dhan's intraday archive starts on
3 Apr 2017 (measured for stocks on 22 Aug 2026; the first index fill states
what it found).

Stored as data/fno/index_minute/<NAME>/<YEAR>.parquet with columns ts (IST),
open, high, low, close, volume. Each 90-day chunk is stored as it arrives, so
a stopped fill carries on; a later fill starts again from the last stored
day, because a run during market hours stores a partial day.

Dhan's timestamps are read as true UTC instants (providers/dhan.py says why
that is unverified); the first fill prints the first and last minute of a day
so 09:15 and 15:29 can be checked by eye.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from config import IST, UTC
from fno.yahoo import write_whole
from providers.base import ProviderError

INDEXES = {"NIFTY": ("13", "IDX_I", "INDEX"), "BANKNIFTY": ("25", "IDX_I", "INDEX"),
           "INDIAVIX": ("21", "IDX_I", "INDEX")}
COLUMNS = ("ts", "open", "high", "low", "close", "volume")
ARCHIVE_BEGINS = date(2017, 4, 3)
CHUNK_DAYS = 90


class IndexResolver:
    def resolve(self, symbol: str) -> tuple[str, str, str]:
        if symbol not in INDEXES:
            raise ProviderError(f"{symbol}: no Dhan index id known (have {', '.join(INDEXES)})")
        return INDEXES[symbol]


def path_for(root: Path, name: str, year: int) -> Path:
    return Path(root) / "index_minute" / name / f"{year}.parquet"


def _years(root: Path, name: str) -> list[int]:
    folder = Path(root) / "index_minute" / name
    return sorted(int(p.stem) for p in folder.glob("*.parquet")) if folder.exists() else []


def last_stored(root: Path, name: str) -> date | None:
    for year in reversed(_years(root, name)):
        frame = pd.read_parquet(path_for(root, name, year), columns=["ts"])
        if len(frame):
            return frame["ts"].max().date()
    return None


def read(root: Path, name: str, start: date, end: date) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for y in range(start.year, end.year + 1)
              if (p := path_for(root, name, y)).exists()]
    if not frames:
        return pd.DataFrame(columns=list(COLUMNS))
    out = pd.concat(frames, ignore_index=True)
    days = out["ts"].dt.date
    return out[(days >= start) & (days <= end)].reset_index(drop=True)


def _to_frame(canonical: pd.DataFrame) -> pd.DataFrame:
    frame = canonical.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "ts"})
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST)
    return frame[list(COLUMNS)]


def _store(root: Path, name: str, frame: pd.DataFrame) -> None:
    for year, part in frame.groupby(frame["ts"].dt.year):
        path = path_for(root, name, int(year))
        old = pd.read_parquet(path) if path.exists() else None
        merged = part if old is None else pd.concat([old, part], ignore_index=True)
        merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        write_whole(path, merged)


def fill(provider: Any, root: Path, name: str, *, today: date,
         log: Callable[..., None] = print) -> int:
    """Fetch from the last stored day (or the archive start) to `today`; rows stored."""
    day = last_stored(root, name) or ARCHIVE_BEGINS
    total = 0
    while day <= today:
        last = min(day + timedelta(days=CHUNK_DAYS - 1), today)
        canonical = provider.fetch_minutes(
            name, datetime.combine(day, time(0, 0), IST).astimezone(UTC),
            datetime.combine(last, time(23, 59), IST).astimezone(UTC))
        if not canonical.empty:
            frame = _to_frame(canonical)
            _store(root, name, frame)
            total += len(frame)
        log(f"  {name} {day} -> {last}: {total:,} minutes so far", flush=True)
        day = last + timedelta(days=1)
    return total


def create_provider() -> Any:
    """A Dhan provider for the indexes, sharing the stock pipeline's token cache."""
    from db import SupabaseStore
    from dhan_auth import DhanCredentials, DhanTokenManager
    from providers.dhan import DhanProvider
    from supabase_candle_backend import SupabaseCandleBackend

    credentials = DhanCredentials.from_env()
    backend = SupabaseCandleBackend(SupabaseStore.from_env()._client)
    return DhanProvider(token_manager=DhanTokenManager(credentials, backend),
                        instrument_resolver=IndexResolver(), client_id=credentials.client_id)
