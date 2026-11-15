"""Download NSE total-return index history (dividends included) from niftyindices.com.

    python scripts/nse_index_history.py

Writes data/rotation/nse_tri.parquet: one column per index, one row per session,
2005 to today. Public data, read-only. These indexes use each date's real
constituents, so - unlike a backtest on today's stock list - they carry no
hindsight (survivorship) bias.
"""

from __future__ import annotations

import json
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import requests

URL = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
HOME = "https://www.niftyindices.com/reports/historical-data"
INDEXES = {"NIFTY200 MOMENTUM 30": "NIFTY200MOMENTM30", "NIFTY 200": "NIFTY 200", "NIFTY 50": "NIFTY 50"}
HEADERS = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json; charset=UTF-8",
           "X-Requested-With": "XMLHttpRequest", "Referer": HOME, "Origin": "https://www.niftyindices.com"}


def year(session: requests.Session, name: str, index: str, y: int) -> list[dict]:
    cinfo = f"{{'name':'{name}','startDate':'01-Jan-{y}','endDate':'31-Dec-{y}','indexName':'{index}'}}"
    r = session.post(URL, headers=HEADERS, data=json.dumps({"cinfo": cinfo}), timeout=60)
    try:
        return r.json()
    except ValueError as exc:
        raise RuntimeError(f"{index} {y}: not JSON ({r.status_code}): {r.text[:120]}") from exc


def fetch(first_year: int = 2005, indexes: dict[str, str] = INDEXES, log=print) -> pd.DataFrame:
    """Total-return levels, one column per index, `first_year` to today."""
    s = requests.Session()
    s.get(HOME, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    cols = {}
    for index, name in indexes.items():
        rows = []
        for y in range(first_year, date.today().year + 1):
            rows += [(datetime.strptime(r["Date"], "%d %b %Y").date(), float(r["TotalReturnsIndex"]))
                     for r in year(s, name, index, y) if r.get("TotalReturnsIndex") not in (None, "-")]
            time.sleep(0.5)
        cols[index] = pd.Series(dict(rows)).sort_index()
        log(f"{index}: {len(cols[index])} sessions, {cols[index].index[0]} -> {cols[index].index[-1]}", flush=True)
    return pd.DataFrame(cols)


def fetch_whole(indexes: list[str], first_year: int = 2005, log=print, pause: float = 1.0) -> pd.DataFrame:
    """Total-return levels for many indexes, ONE request each (the endpoint serves 20+ years at once).

    An index the site does not know, or that fails twice, is left out and named in
    the log - the caller decides whether what is left is enough.
    """
    s = requests.Session()
    s.get(HOME, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    cols, missing = {}, []
    for index in indexes:
        cinfo = (f"{{'name':'{index}','startDate':'01-Jan-{first_year}',"
                 f"'endDate':'31-Dec-{date.today().year}','indexName':'{index}'}}")
        rows: list[tuple[date, float]] = []
        for attempt in range(2):
            try:
                r = s.post(URL, headers=HEADERS, data=json.dumps({"cinfo": cinfo}), timeout=120)
                got = r.json()
                got = json.loads(got) if isinstance(got, str) else got
                rows = [(datetime.strptime(x["Date"], "%d %b %Y").date(), float(x["TotalReturnsIndex"]))
                        for x in got if x.get("TotalReturnsIndex") not in (None, "-")]
                if rows:
                    break
            except (ValueError, KeyError, requests.RequestException):
                rows = []
            time.sleep(5 * (attempt + 1))
        if rows:
            cols[index] = pd.Series(dict(rows)).sort_index()
        else:
            missing.append(index)
        time.sleep(pause)
    frame = pd.DataFrame(cols).sort_index()
    log(f"{len(cols)} NSE indexes, {frame.index.min()} -> {frame.index.max()}"
        + (f"; missing {', '.join(missing)}" if missing else ""), flush=True)
    return frame


def main() -> int:
    out = Path("data/rotation/nse_tri.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    fetch().to_parquet(out)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
