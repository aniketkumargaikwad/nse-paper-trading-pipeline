"""Maps our 'NSE:RELIANCE' notation to Dhan security identifiers.

Dhan publishes a security master as CSV. We parse the handful of columns we
need; the caller caches them in the `instruments` table so a run never
downloads the multi-MB file just to resolve a few symbols.

Pure module: it parses text it is handed. Downloading is the caller's job.

ASSUMPTION: Dhan's master uses the SEM_* column names below. They are checked
explicitly at parse time, so a format change fails loudly and immediately
rather than producing silently wrong security IDs - which would fetch candles
for the WRONG STOCK, the worst failure this module could have.

VERIFIED FACTS (from the live file, 2026-08-08) - do not re-derive these by
guessing, they were confirmed by downloading and inspecting the real CSV:

* There are TWO different master files with DIFFERENT schemas. The
  "-detailed" file (api-scrip-master-detailed.csv) has 33 columns with NO
  `SEM_` prefix. The COMPACT file (api-scrip-master.csv, see
  SECURITY_MASTER_URL below) has 16 columns WITH the `SEM_` prefix. This
  module targets the compact file - do not repoint it at the detailed one.

* Security IDs are unique only WITHIN a segment, not globally. Id '2885' is
  RELIANCE (NSE, segment 'E' - equity) but is ALSO 'EURINR-Aug2025-102.75-CE'
  (NSE, segment 'C' - currency derivative). Filtering by security id alone,
  without also constraining exchange+segment, would resolve a symbol to the
  wrong instrument and silently fetch someone else's candles into its cache.

* Segment 'E' (equity) is not synonymous with "ordinary tradable stock" - it
  also carries government securities (series like 'SG', 'GS' - e.g.
  '757GS2033' is a 2033-maturity government bond, not a stock) and SME-board
  scrips (series 'SM'). The SEM_SERIES column must be filtered per exchange
  (see EQUITY_SERIES_BY_EXCHANGE) or bonds and SME scrips leak into the
  instruments table as if they were equities.

* 'EXCHANGE:TRADINGSYMBOL' is NOT a unique key in Dhan's universe, even after
  all the filtering above. Verified against the live file (2026-08-08, 5,259
  parsed instruments, exactly 4 collisions): on BSE an ETF and an index can
  legitimately share a ticker (METAL, ENERGY, INFRA - e.g. BSE:METAL is both
  the Mirae Asset METAL ETF, id 544268, and the BSE METAL index, id 75), and
  two distinct index rows can carry the identical name (CAPINS, ids 99 and
  846). `instruments.symbol` is UNIQUE and Postgres additionally rejects an
  upsert batch containing the same conflict target twice, so collisions must
  be resolved deterministically before the table is written. See
  `deduplicate_by_symbol`: EQUITY always wins over INDEX (this platform
  trades equities; an index must never shadow a tradable ticker - so
  BSE:METAL resolves to the ETF, and the BSE METAL index is unreachable under
  that name, an accepted, documented limitation), and within the same
  instrument type the lower numeric security id wins, purely so the same
  input file always produces the same table.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

# Dhan publishes the COMPACT security master here: 16 columns, SEM_-prefixed.
# There is also an "-detailed" file at a similar URL with a totally
# different, un-prefixed 33-column schema - do not point at that one.
SECURITY_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# 'EXCHANGE:TRADINGSYMBOL' in capitals. & and - appear in real NSE symbols
# (M&M, BAJAJ-AUTO).
SYMBOL_RE = re.compile(r"^[A-Z]+:[A-Z0-9&\-]+$")

# The columns we depend on, verified at parse time.
COLUMN_SECURITY_ID = "SEM_SMST_SECURITY_ID"
COLUMN_TRADING_SYMBOL = "SEM_TRADING_SYMBOL"
COLUMN_EXCHANGE = "SEM_EXM_EXCH_ID"
COLUMN_SEGMENT = "SEM_SEGMENT"
COLUMN_INSTRUMENT = "SEM_INSTRUMENT_NAME"
COLUMN_LOT = "SEM_LOT_UNITS"
COLUMN_NAME = "SM_SYMBOL_NAME"
COLUMN_SERIES = "SEM_SERIES"

# SEM_CUSTOM_SYMBOL is an optional fallback for COLUMN_NAME - it is not
# required to be present, so it is deliberately left out of REQUIRED_COLUMNS.
COLUMN_CUSTOM_SYMBOL = "SEM_CUSTOM_SYMBOL"

REQUIRED_COLUMNS = (
    COLUMN_SECURITY_ID, COLUMN_TRADING_SYMBOL, COLUMN_EXCHANGE,
    COLUMN_SEGMENT, COLUMN_INSTRUMENT, COLUMN_SERIES,
)

# Dhan's SEM_SEGMENT codes we support. Everything else (D=derivatives,
# C=currency, M=commodity) is skipped: this platform trades cash equity and
# reads indices, nothing else.
SEGMENT_EQUITY = "E"
SEGMENT_INDEX = "I"

# Which series count as ordinary cash equity, PER EXCHANGE. This filter is
# load-bearing: NSE segment E also carries government securities (series SG,
# GS), SME scrips (SM) and others - e.g. '757GS2033' is a bond, not a stock.
# BSE uses entirely different group codes and has NO 'EQ' series at all, so a
# single shared set would silently exclude every BSE listing.
EQUITY_SERIES_BY_EXCHANGE: dict[str, frozenset[str]] = {
    # EQ is the main NSE board; BE is the trade-for-trade surveillance series.
    "NSE": frozenset({"EQ", "BE"}),
    # BSE group codes: A and B are the main equity groups.
    "BSE": frozenset({"A", "B"}),
}

# Dhan's exchangeSegment values, keyed by (exchange, our instrument type).
# Anything not listed here is a segment we do not support and is skipped.
SEGMENT_BY_EXCHANGE: dict[tuple[str, str], str] = {
    ("NSE", "EQUITY"): "NSE_EQ",
    ("BSE", "EQUITY"): "BSE_EQ",
    ("NSE", "INDEX"): "IDX_I",
    ("BSE", "INDEX"): "IDX_I",
}


# Instrument types in precedence order when two rows claim the same symbol.
# EQUITY first: this platform trades equities, and an index must never
# shadow a tradable ticker.
_TYPE_PRECEDENCE = {"EQUITY": 0, "INDEX": 1}


class InstrumentError(ValueError):
    """A symbol or the security master could not be understood."""


@dataclass(frozen=True)
class Instrument:
    """One tradable instrument, as stored in the `instruments` table."""

    symbol: str
    exchange: str
    tradingsymbol: str
    dhan_security_id: str
    dhan_segment: str
    name: str | None
    instrument_type: str
    lot_size: int | None
    refreshed_on: date

    def to_row(self) -> dict[str, Any]:
        """Shape this instrument as an `instruments` row."""
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "dhan_security_id": self.dhan_security_id,
            "dhan_segment": self.dhan_segment,
            "name": self.name,
            "instrument_type": self.instrument_type,
            "lot_size": self.lot_size,
            "is_active": True,
            "refreshed_on": self.refreshed_on.isoformat(),
        }


def _collision_rank(instrument: "Instrument") -> tuple[int, int]:
    """Sort key deciding which row wins a duplicated symbol.

    Lower sorts first and wins. Ties break on the numeric security id so the
    same input file always yields the same table.
    """
    try:
        security_id = int(instrument.dhan_security_id)
    except (TypeError, ValueError):
        security_id = 2**31
    return (_TYPE_PRECEDENCE.get(instrument.instrument_type, 99), security_id)


def deduplicate_by_symbol(instruments: list["Instrument"]) -> list["Instrument"]:
    """Keep exactly one Instrument per symbol, deterministically.

    Dhan's master genuinely contains symbol collisions: on BSE an ETF and an
    index can share a ticker (METAL, ENERGY, INFRA), and two index rows can
    carry the same name (CAPINS, ids 99 and 846). `instruments.symbol` is
    UNIQUE, and Postgres additionally rejects an upsert batch containing the
    same conflict-target twice - so the choice must be made here, and made
    the same way every run.
    """
    best: dict[str, Instrument] = {}
    for instrument in instruments:
        current = best.get(instrument.symbol)
        if current is None or _collision_rank(instrument) < _collision_rank(current):
            best[instrument.symbol] = instrument
    # Preserve first-seen order for stable, reviewable output.
    seen: set[str] = set()
    ordered: list[Instrument] = []
    for instrument in instruments:
        if instrument.symbol in seen:
            continue
        seen.add(instrument.symbol)
        ordered.append(best[instrument.symbol])
    return ordered


def split_symbol(symbol: str) -> tuple[str, str]:
    """'NSE:RELIANCE' -> ('NSE', 'RELIANCE'), validating the shape."""
    if not isinstance(symbol, str) or not SYMBOL_RE.match(symbol):
        raise InstrumentError(
            f"Malformed symbol {symbol!r}. Expected 'EXCHANGE:TRADINGSYMBOL' "
            "in capitals, e.g. NSE:RELIANCE."
        )
    exchange, _, tradingsymbol = symbol.partition(":")
    return exchange, tradingsymbol


def parse_security_master(
    csv_text: str, refreshed_on: date, wanted: Iterable[str] | None = None
) -> list[Instrument]:
    """Parse Dhan's security-master CSV into Instrument records.

    Args:
        csv_text: the raw CSV.
        refreshed_on: IST date of this refresh.
        wanted: if given, keep only these 'EXCHANGE:SYMBOL' values.

    Raises:
        InstrumentError: if an expected column is absent.
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    headers = set(reader.fieldnames or [])
    missing = [c for c in REQUIRED_COLUMNS if c not in headers]
    if missing:
        raise InstrumentError(
            f"Dhan security master is missing expected column(s): "
            f"{', '.join(missing)}. The file format may have changed; check "
            "the DhanHQ docs before trusting any security IDs from it."
        )

    keep = set(wanted) if wanted is not None else None
    out: list[Instrument] = []
    for row in reader:
        exchange = (row.get(COLUMN_EXCHANGE) or "").strip().upper()
        tradingsymbol = (row.get(COLUMN_TRADING_SYMBOL) or "").strip().upper()
        segment = (row.get(COLUMN_SEGMENT) or "").strip().upper()
        instrument_name = (row.get(COLUMN_INSTRUMENT) or "").strip().upper()
        series = (row.get(COLUMN_SERIES) or "").strip().upper()
        if not exchange or not tradingsymbol:
            continue

        if segment == SEGMENT_EQUITY and instrument_name == "EQUITY":
            instrument_type = "EQUITY"
            # Segment E also carries government securities (series SG/GS) and
            # SME scrips (series SM) - only real equity series belong here.
            if series not in EQUITY_SERIES_BY_EXCHANGE.get(exchange, frozenset()):
                continue
        elif segment == SEGMENT_INDEX and instrument_name == "INDEX":
            instrument_type = "INDEX"
            # Real index names contain spaces ('NIFTY MIDCAP 150'); strip
            # them so the symbol is typeable and matches SYMBOL_RE.
            tradingsymbol = "".join(tradingsymbol.split())
        else:
            continue  # derivatives, currency, commodity - not supported

        symbol = f"{exchange}:{tradingsymbol}"
        if keep is not None and symbol not in keep:
            continue
        if not SYMBOL_RE.match(symbol):
            continue  # exotic contract names we do not support

        dhan_segment = SEGMENT_BY_EXCHANGE.get((exchange, instrument_type))
        if dhan_segment is None:
            continue  # an exchange/type combination we do not support

        lot_raw = (row.get(COLUMN_LOT) or "").strip()
        try:
            lot_size = int(float(lot_raw)) if lot_raw else None
        except ValueError:
            lot_size = None

        name = (row.get(COLUMN_NAME) or "").strip()
        if not name:
            name = (row.get(COLUMN_CUSTOM_SYMBOL) or "").strip()

        out.append(Instrument(
            symbol=symbol,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            dhan_security_id=(row.get(COLUMN_SECURITY_ID) or "").strip(),
            dhan_segment=dhan_segment,
            name=name or None,
            instrument_type=instrument_type,
            lot_size=lot_size,
            refreshed_on=refreshed_on,
        ))
    return deduplicate_by_symbol(out)
