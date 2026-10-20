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


def main() -> int:
    s = requests.Session()
    s.get(HOME, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
    cols = {}
    for index, name in INDEXES.items():
        rows = []
        for y in range(2005, date.today().year + 1):
            rows += [(datetime.strptime(r["Date"], "%d %b %Y").date(), float(r["TotalReturnsIndex"]))
                     for r in year(s, name, index, y) if r.get("TotalReturnsIndex") not in (None, "-")]
            time.sleep(0.5)
        cols[index] = pd.Series(dict(rows)).sort_index()
        print(f"{index}: {len(cols[index])} sessions, {cols[index].index[0]} -> {cols[index].index[-1]}", flush=True)
    out = Path("data/rotation/nse_tri.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(cols).to_parquet(out)
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
