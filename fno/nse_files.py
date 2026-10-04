"""NSE's daily F&O files ("bhavcopy"), both formats, normalised to one schema.

Two formats, switched on 8 Jul 2024 (design §2.1). Both are read into
COLUMNS so nothing downstream knows which one a day came from.

Units, verified against the real files on 2 Oct 2026: volume is in
CONTRACTS, open interest in UNITS (shares of the index), turnover in rupees.
An untraded contract still carries a close - yesterday's - so whether a
contract traded is read from volume, never from its close.
"""

from __future__ import annotations

import io
import zipfile
from datetime import date
from typing import Any

import pandas as pd

from fno.contracts import INDEX_UNDERLYINGS

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


COLUMNS: tuple[str, ...] = (
    "trade_date", "underlying", "instrument", "expiry", "strike", "option_type",
    "open", "high", "low", "close", "settle",
    "volume_contracts", "oi_units", "oi_change_units", "turnover_inr",
    "lot_size", "underlying_price",
)


# One dtype per column whichever format a day came from: a UDiFF lot size
# parses as an integer and an old-format one is NaN, so without this a
# year's file would change type depending on which days were saved together.
DTYPES = {
    "strike": "float64", "open": "float64", "high": "float64", "low": "float64",
    "close": "float64", "settle": "float64", "turnover_inr": "float64",
    "lot_size": "float64", "underlying_price": "float64",
    "volume_contracts": "int64", "oi_units": "int64", "oi_change_units": "int64",
}


class NseFileError(RuntimeError):
    """A file NSE served that cannot be trusted as the day's F&O data."""


def _num(col: pd.Series) -> pd.Series:
    """Text to numbers: blanks become NaN, anything else unparsable is an error."""
    col = col.str.strip()
    return pd.to_numeric(col.mask(col == ""), errors="raise")


def _dates(col: pd.Series, fmt: str) -> pd.Series:
    return pd.to_datetime(col.str.strip(), format=fmt).dt.date


def _parse_old(raw: pd.DataFrame) -> pd.DataFrame:
    kind = raw["INSTRUMENT"].str.strip()
    r = raw[kind.isin(("OPTIDX", "FUTIDX"))]
    is_opt = r["INSTRUMENT"].str.strip() == "OPTIDX"
    nan = float("nan")
    return pd.DataFrame({
        "trade_date": _dates(r["TIMESTAMP"], "%d-%b-%Y"),
        "underlying": r["SYMBOL"].str.strip(),
        "instrument": is_opt.map({True: "OPT", False: "FUT"}),
        "expiry": _dates(r["EXPIRY_DT"], "%d-%b-%Y"),
        "strike": _num(r["STRIKE_PR"]).where(is_opt),
        "option_type": r["OPTION_TYP"].str.strip().where(is_opt),
        "open": _num(r["OPEN"]),
        "high": _num(r["HIGH"]),
        "low": _num(r["LOW"]),
        "close": _num(r["CLOSE"]),
        "settle": _num(r["SETTLE_PR"]),
        "volume_contracts": _num(r["CONTRACTS"]).astype("int64"),
        "oi_units": _num(r["OPEN_INT"]).astype("int64"),
        "oi_change_units": _num(r["CHG_IN_OI"]).astype("int64"),
        "turnover_inr": _num(r["VAL_INLAKH"]) * 100_000,
        "lot_size": nan,
        "underlying_price": nan,
    })


def _parse_udiff(raw: pd.DataFrame) -> pd.DataFrame:
    kind = raw["FinInstrmTp"].str.strip()
    r = raw[kind.isin(("IDO", "IDF"))]
    is_opt = r["FinInstrmTp"].str.strip() == "IDO"
    # The ACTUAL expiry, which moves when the scheduled day is a holiday.
    actual = r["FininstrmActlXpryDt"].str.strip()
    expiry = actual.mask(actual == "", r["XpryDt"].str.strip())
    return pd.DataFrame({
        "trade_date": _dates(r["TradDt"], "%Y-%m-%d"),
        "underlying": r["TckrSymb"].str.strip(),
        "instrument": is_opt.map({True: "OPT", False: "FUT"}),
        "expiry": _dates(expiry, "%Y-%m-%d"),
        "strike": _num(r["StrkPric"]).where(is_opt),
        "option_type": r["OptnTp"].str.strip().where(is_opt),
        "open": _num(r["OpnPric"]),
        "high": _num(r["HghPric"]),
        "low": _num(r["LwPric"]),
        "close": _num(r["ClsPric"]),
        "settle": _num(r["SttlmPric"]),
        "volume_contracts": _num(r["TtlTradgVol"]).astype("int64"),
        "oi_units": _num(r["OpnIntrst"]).astype("int64"),
        "oi_change_units": _num(r["ChngInOpnIntrst"]).astype("int64"),
        "turnover_inr": _num(r["TtlTrfVal"]),
        "lot_size": _num(r["NewBrdLotQty"]),
        "underlying_price": _num(r["UndrlygPric"]),
    })


def parse(text: str, day: date) -> pd.DataFrame:
    """`day`'s file as COLUMNS: index futures and options of INDEX_UNDERLYINGS only."""
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    if "INSTRUMENT" in raw.columns:
        out = _parse_old(raw)
    elif "TckrSymb" in raw.columns:
        out = _parse_udiff(raw)
    else:
        raise NseFileError(f"{day}: unrecognised header {list(raw.columns)[:5]}")
    out = out[out["underlying"].isin(INDEX_UNDERLYINGS)].reset_index(drop=True)
    wrong = out[out["trade_date"] != day]
    if not wrong.empty:
        raise NseFileError(
            f"asked for {day}, the file is for {wrong['trade_date'].iloc[0]}: "
            "refusing to store one day's prices under another")
    return out[list(COLUMNS)].astype(DTYPES)


# Browser-like headers: NSE's archive host rejects default client user agents
# (the same finding as universes.py).
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
TIMEOUT_SECONDS = 60


def download(day: date, session: Any) -> str | None:
    """`day`'s file as CSV text, or None when NSE has none (no session that day).

    Only a 404 means "no session". A blocked client is answered with 200 and
    an HTML page, and that must not be recorded as a holiday.
    """
    r = session.get(file_url(day), headers=HEADERS, timeout=TIMEOUT_SECONDS)
    if r.status_code == 404:
        return None
    if r.status_code != 200 or r.content[:2] != b"PK":
        raise NseFileError(f"{day}: HTTP {r.status_code}, {len(r.content)} bytes, not a zip")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return z.read(z.namelist()[0]).decode("utf-8")
