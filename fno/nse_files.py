"""NSE's daily F&O files ("bhavcopy"), both formats, normalised to one schema.

Two formats, switched on 8 Jul 2024 (design §2.1). Both are read into
COLUMNS so nothing downstream knows which one a day came from.

Units, verified against the real files on 2 Oct 2026: volume is in
CONTRACTS, open interest in UNITS (shares of the index), turnover in rupees.
An untraded contract still carries a close - yesterday's - so whether a
contract traded is read from volume, never from its close.
"""

from __future__ import annotations

from datetime import date

UDIFF_FROM = date(2024, 7, 8)

_OLD_URL = ("https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
            "{year}/{mon}/fo{dd}{mon}{year}bhav.csv.zip")
_UDIFF_URL = ("https://nsearchives.nseindia.com/content/fo/"
              "BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip")


def file_url(day: date) -> str:
    """Where NSE publishes `day`'s F&O file."""
    if day >= UDIFF_FROM:
        return _UDIFF_URL.format(ymd=day.strftime("%Y%m%d"))
    mon = day.strftime("%b").upper()
    return _OLD_URL.format(year=day.year, mon=mon, dd=day.strftime("%d"))
