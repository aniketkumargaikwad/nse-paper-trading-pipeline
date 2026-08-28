"""Named symbol universes: NIFTY50/100/200/500 and custom groups.

A strategy stores a universe NAME; this module turns that name into symbols.
Resolution is deliberately separate from parsing, so validating a pasted
strategy never needs a network call or a database.

Two honesty rules drive the design, both inherited from the Phase 0 data work:

* An empty universe is never returned. It would produce a zero-trade backtest
  that reads exactly like "no signals found" — indistinguishable from a
  strategy that simply never triggered.
* A partial resolution is never silently narrowed. If NIFTY100 resolves 97
  names, the run says 97 of 100 and names the three, rather than reporting a
  NIFTY100 result computed over 97 stocks.

A third caveat this module makes visible but cannot remove: index membership
is TODAY's, applied to past data. Stocks join an index after they have already
risen, so a backtest over current constituents is flattered by survivorship.
Free point-in-time membership data does not meaningfully exist, so every
resolution carries the list's date and every caller is expected to say so.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "universes"

# NSE publishes constituents with these headers. Only Symbol and Series are
# load-bearing; the rest are carried in the file but unused.
COLUMN_SYMBOL = "Symbol"
COLUMN_SERIES = "Series"

# Only ordinary cash equity. NSE index files are overwhelmingly 'EQ', but the
# filter mirrors instruments.py so a bond or SME scrip can never enter a
# universe as though it were a tradable stock.
EQUITY_SERIES = frozenset({"EQ", "BE"})

# The system universes refreshed by scripts/refresh_universes.py.
NSE_INDEX_URLS: dict[str, str] = {
    "NIFTY50": "https://nsearchives.nseindia.com/content/indices/ind_nifty50list.csv",
    "NIFTY100": "https://nsearchives.nseindia.com/content/indices/ind_nifty100list.csv",
    "NIFTY200": "https://nsearchives.nseindia.com/content/indices/ind_nifty200list.csv",
    "NIFTY500": "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
}

# Browser-like headers. NSE's archive host rejects default client user agents,
# which is exactly why every list is also kept as a committed snapshot.
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
    ),
    "Accept": "text/csv,*/*",
    "Accept-Language": "en-US,en;q=0.9",
}
_FETCH_TIMEOUT_SECONDS = 30

_SNAPSHOT_RE = re.compile(r"^(?P<name>[A-Z0-9_]+)-(?P<date>\d{4}-\d{2}-\d{2})\.csv$")

# Storage arithmetic. 75 five-minute candles per trading session, ~250
# sessions a year.
#
# The per-candle cost depends entirely on WHERE candles are kept, and the two
# answers differ by 5.6x - enough to turn "needs the $25/mo plan" into "fits
# free", so projecting the wrong one is not a rounding error.
#
#   Postgres rows : ~120 bytes, against the 500 MB database quota
#   Parquet+zstd  :  ~22 bytes, against the 1 GB Storage quota
#
# The Parquet figure is measured, not estimated: 8,344,034 five-minute candles
# occupy 175.3 MB on disk today.
CANDLES_PER_SESSION = 75
SESSIONS_PER_YEAR = 250

BYTES_PER_ROW = 120                 # Postgres, including index overhead
BYTES_PER_CANDLE_PARQUET = 22       # measured on the real store

# Storage and the database are SEPARATE free-tier quotas, so which one a
# projection must fit depends on the backend too.
SUPABASE_FREE_TIER_MB = 500         # database rows
SUPABASE_STORAGE_FREE_TIER_MB = 1024  # files


class UniverseError(ValueError):
    """A universe could not be parsed, found, or resolved."""


@dataclass(frozen=True)
class UniverseResolution:
    """The outcome of turning a universe name into tradable symbols."""

    name: str
    as_of: date
    source: str                    # 'nse' | 'snapshot' | 'custom'
    symbols: tuple[str, ...]       # present in the instruments table
    missing: tuple[str, ...]       # listed but not in the instruments table
    listed_count: int

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def summary(self) -> str:
        """One line stating exactly what was resolved. Always logged."""
        head = (
            f"{self.name}: {len(self.symbols)} of {self.listed_count} symbols "
            f"(list dated {self.as_of.isoformat()}, source {self.source})"
        )
        if self.is_complete:
            return head
        return f"{head}; NOT FOUND in instruments: {', '.join(self.missing)}"


@dataclass(frozen=True)
class ConstituentList:
    """A universe's membership, with its provenance stated."""

    name: str
    symbols: tuple[str, ...]
    as_of: date
    source: str                 # 'nse' | 'snapshot'
    raw_csv: str
    warning: str | None = None  # set when the live fetch failed


@dataclass(frozen=True)
class StorageProjection:
    """What backfilling a universe will cost, before it is started."""

    symbol_count: int
    years: float
    rows: int
    megabytes: float
    store: str = "parquet"          # 'parquet' | 'supabase'

    @property
    def gigabytes(self) -> float:
        return self.megabytes / 1024

    @property
    def quota_mb(self) -> int:
        """The free-tier limit this projection is actually measured against."""
        return (SUPABASE_STORAGE_FREE_TIER_MB if self.store == "parquet"
                else SUPABASE_FREE_TIER_MB)

    @property
    def exceeds_free_tier(self) -> bool:
        return self.megabytes > self.quota_mb

    def summary(self) -> str:
        where = "Storage" if self.store == "parquet" else "database rows"
        head = (
            f"{self.symbol_count} symbols x {self.years:g} years at 5m "
            f"= ~{self.rows:,} candles (~{self.megabytes:,.0f} MB as {where})"
        )
        if not self.exceeds_free_tier:
            return (
                f"{head}, within the {self.quota_mb} MB free tier "
                f"({self.quota_mb - self.megabytes:,.0f} MB spare). The "
                "backfill will take a while: Dhan serves 90 days per request."
            )
        upgrade = ("Storage is $0.021/GB/mo beyond 1 GB"
                   if self.store == "parquet"
                   else "Pro is $25/mo for 8 GB")
        return (
            f"{head}. That exceeds the {self.quota_mb} MB free tier "
            f"- {upgrade}. The backfill will also take a while: Dhan serves "
            "90 days per request."
        )


def parse_constituent_csv(text: str) -> tuple[str, ...]:
    """Parse an NSE constituent CSV into sorted 'NSE:SYMBOL' notation."""
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [(name or "").strip() for name in (reader.fieldnames or [])]
    for required in (COLUMN_SYMBOL, COLUMN_SERIES):
        if required not in fieldnames:
            raise UniverseError(
                f"constituent CSV is missing the {required!r} column. "
                f"Found: {', '.join(fieldnames) or '(no header)'}. "
                "NSE may have changed the file format."
            )

    symbols: set[str] = set()
    for row in reader:
        series = (row.get(COLUMN_SERIES) or "").strip().upper()
        symbol = (row.get(COLUMN_SYMBOL) or "").strip().upper()
        if not symbol or series not in EQUITY_SERIES:
            continue
        symbols.add(f"NSE:{symbol}")

    if not symbols:
        raise UniverseError(
            "constituent CSV contained no constituents. Refusing to return an "
            "empty universe: it would produce a zero-trade backtest that looks "
            "identical to a strategy that never triggered."
        )
    return tuple(sorted(symbols))


def resolve_universe(
    *,
    name: str,
    listed: tuple[str, ...],
    known: set[str],
    as_of: date,
    source: str,
) -> UniverseResolution:
    """Intersect a constituent list with the symbols we actually have.

    `known` is the set of symbols present in the `instruments` table. Symbols
    listed by the index but absent from it are reported, never dropped.
    """
    present = tuple(s for s in listed if s in known)
    missing = tuple(s for s in listed if s not in known)

    if not present:
        raise UniverseError(
            f"universe {name!r} resolved to zero symbols: none of its "
            f"{len(listed)} constituents are in the instruments table. "
            "Populate it first with:  "
            ".venv\\Scripts\\python.exe backfill.py --refresh-instruments"
        )

    return UniverseResolution(
        name=name,
        as_of=as_of,
        source=source,
        symbols=present,
        missing=missing,
        listed_count=len(listed),
    )


def snapshot_filename(name: str, as_of: date) -> str:
    """Dated snapshot filename; the ISO date makes them sort chronologically."""
    return f"{name}-{as_of.isoformat()}.csv"


def fetch_constituent_csv(url: str) -> str:
    """Download one NSE constituent CSV. Raises on any non-200."""
    import requests  # imported lazily so the pure paths stay import-light

    response = requests.get(url, headers=_FETCH_HEADERS, timeout=_FETCH_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.text


def newest_snapshot(name: str, *, snapshot_dir: Path | None = None) -> tuple[Path, date]:
    """Return the most recent committed snapshot for `name`, and its date."""
    directory = snapshot_dir or SNAPSHOT_DIR
    candidates: list[tuple[date, Path]] = []
    if directory.is_dir():
        for path in directory.iterdir():
            match = _SNAPSHOT_RE.match(path.name)
            if match and match.group("name") == name:
                candidates.append((date.fromisoformat(match.group("date")), path))

    if not candidates:
        raise UniverseError(
            f"no committed snapshot for universe {name!r} in {directory}. "
            "Create one with:  "
            ".venv\\Scripts\\python.exe scripts/refresh_universes.py"
        )
    as_of, path = max(candidates)
    return path, as_of


def load_constituents(
    name: str,
    *,
    snapshot_dir: Path | None = None,
    fetcher: Callable[[str], str] | None = None,
    today: date | None = None,
) -> ConstituentList:
    """Get a universe's membership: live from NSE, or the newest snapshot.

    NSE's endpoints are unofficial and bot-hostile, so a failure here is
    expected rather than exceptional. Falling back to the snapshot keeps
    backtests runnable; the warning makes the staleness impossible to miss.
    """
    if name not in NSE_INDEX_URLS:
        raise UniverseError(
            f"unknown system universe {name!r}. "
            f"Known: {', '.join(sorted(NSE_INDEX_URLS))}. "
            "Custom universes are read from the database, not from NSE."
        )

    fetch = fetcher or fetch_constituent_csv
    try:
        text = fetch(NSE_INDEX_URLS[name])
        symbols = parse_constituent_csv(text)
    except Exception as exc:            # noqa: BLE001 - any failure falls back
        try:
            path, as_of = newest_snapshot(name, snapshot_dir=snapshot_dir)
        except UniverseError as snapshot_exc:
            raise UniverseError(
                f"could not fetch {name} from NSE ({exc}) and there is no "
                f"committed snapshot to fall back on. {snapshot_exc}"
            ) from exc
        text = path.read_text(encoding="utf-8")
        return ConstituentList(
            name=name,
            symbols=parse_constituent_csv(text),
            as_of=as_of,
            source="snapshot",
            raw_csv=text,
            warning=(
                f"NSE fetch for {name} failed ({exc}); using the committed "
                f"snapshot dated {as_of.isoformat()}. Membership may be stale — "
                "re-run scripts/refresh_universes.py when NSE is reachable."
            ),
        )

    return ConstituentList(
        name=name,
        symbols=symbols,
        as_of=today or date.today(),
        source="nse",
        raw_csv=text,
    )


def project_storage(
    *, symbol_count: int, years: float, store: str = "parquet"
) -> StorageProjection:
    """Projected candles and size for backfilling `symbol_count` at the 5m base.

    `store` decides both the per-candle cost and which free-tier quota the
    answer is compared against. Defaulting to parquet matches what the
    platform actually runs; passing 'supabase' gives the row-storage figure.
    """
    rows = int(symbol_count * years * SESSIONS_PER_YEAR * CANDLES_PER_SESSION)
    per_candle = (BYTES_PER_CANDLE_PARQUET if store == "parquet"
                  else BYTES_PER_ROW)
    megabytes = rows * per_candle / (1024 * 1024)
    return StorageProjection(
        symbol_count=symbol_count, years=years, rows=rows,
        megabytes=megabytes, store=store,
    )
