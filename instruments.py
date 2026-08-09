"""Maps our 'NSE:RELIANCE' notation to Dhan security identifiers.

Dhan publishes a security master as CSV. We parse the handful of columns we
need; the caller caches them in the `instruments` table so a run never
downloads the multi-MB file just to resolve a few symbols.

Pure module: it parses text it is handed. Downloading is the caller's job.

ASSUMPTION: Dhan's master uses the SEM_* column names below. They are checked
explicitly at parse time, so a format change fails loudly and immediately
rather than producing silently wrong security IDs - which would fetch candles
for the WRONG STOCK, the worst failure this module could have.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

# Dhan publishes the detailed security master here.
SECURITY_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master-detailed.csv"

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

REQUIRED_COLUMNS = (
    COLUMN_SECURITY_ID, COLUMN_TRADING_SYMBOL, COLUMN_EXCHANGE,
    COLUMN_SEGMENT, COLUMN_INSTRUMENT,
)

# Dhan's exchangeSegment values, keyed by (exchange, our instrument type).
# Anything not listed here is a segment we do not support and is skipped.
SEGMENT_BY_EXCHANGE: dict[tuple[str, str], str] = {
    ("NSE", "EQUITY"): "NSE_EQ",
    ("BSE", "EQUITY"): "BSE_EQ",
    ("NSE", "INDEX"): "IDX_I",
    ("BSE", "INDEX"): "IDX_I",
}


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
        if not exchange or not tradingsymbol:
            continue

        symbol = f"{exchange}:{tradingsymbol}"
        if keep is not None and symbol not in keep:
            continue
        if not SYMBOL_RE.match(symbol):
            continue  # exotic contract names we do not support

        instrument_name = (row.get(COLUMN_INSTRUMENT) or "").strip().upper()
        instrument_type = "INDEX" if "INDEX" in instrument_name else "EQUITY"
        segment = SEGMENT_BY_EXCHANGE.get((exchange, instrument_type))
        if segment is None:
            continue  # a segment we do not support (e.g. F&O)

        lot_raw = (row.get(COLUMN_LOT) or "").strip()
        try:
            lot_size = int(float(lot_raw)) if lot_raw else None
        except ValueError:
            lot_size = None

        out.append(Instrument(
            symbol=symbol,
            exchange=exchange,
            tradingsymbol=tradingsymbol,
            dhan_security_id=(row.get(COLUMN_SECURITY_ID) or "").strip(),
            dhan_segment=segment,
            name=(row.get(COLUMN_NAME) or "").strip() or None,
            instrument_type=instrument_type,
            lot_size=lot_size,
            refreshed_on=refreshed_on,
        ))
    return out
