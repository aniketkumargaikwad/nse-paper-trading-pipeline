"""NSE's own end-of-day files: check and complete Yahoo's daily prices for the paper books.

WHY THIS EXISTS
---------------
Checked on 18 Nov 2026 against NSE's official closing files, every Yahoo price
the paper books used from 21 Oct to 17 Nov matched NSE exactly, once
Yahoo's dividend adjustment is allowed for (31 small steps, each on an
ex-dividend day). What did NOT hold is timing: when the paper-books job runs
in the evening, Yahoo often has not yet published the day - on 17 Nov it
lacked NIFTY 50 and 33 of the 200 stocks. The stock and rotation books then
ran a day behind without saying so, and a month-end decision made on such a
day would rank a stock with no price as if it did not exist.

So after Yahoo's download, the paper books read NSE's files for every session
since the books began:

  cash market   nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_<YYYYMMDD>_F_0000.csv.zip
  indexes       nsearchives.nseindia.com/content/indices/ind_close_all_<DDMMYYYY>.csv

and fill every price Yahoo is missing from them. Yahoo's prices are adjusted
for dividends and NSE's are not, so a filled price is scaled by the two
feeds' ratio on the stock's nearest earlier day that both have (1.0 for any
recent day without a dividend since). A price both feeds have and that
disagrees by more than DISAGREE_PCT is reported, not changed: on an
ex-dividend day Yahoo's older prices step by the dividend, which is not an
error.
"""

from __future__ import annotations

import io
import time
import zipfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

CM_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{dmy}.csv"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "*/*", "Referer": "https://www.nseindia.com/"}
# Yahoo's index tickers and the names NSE's index file uses for them.
INDEX_NAMES = {"^NSEI": "NIFTY 50", "^CRSLDX": "NIFTY 500"}
SERIES = ("EQ", "BE")
FIELDS = ("Open", "High", "Low", "Close")
DISAGREE_PCT = 5.0


@dataclass
class Check:
    """What the comparison found, for the job's log and the weekly message."""
    sessions: list[date] = field(default_factory=list)          # sessions NSE's files were read for
    no_file: list[date] = field(default_factory=list)           # weekdays with no NSE file yet
    added_sessions: list[date] = field(default_factory=list)    # sessions Yahoo did not have at all
    filled: list[tuple[str, date]] = field(default_factory=list)       # (ticker, day) taken from NSE
    disagree: list[tuple[str, date, float]] = field(default_factory=list)  # (ticker, day, % apart)
    compared: int = 0                                            # prices both feeds had

    def as_dict(self) -> dict[str, Any]:
        return {"sessions": len(self.sessions),
                "first": self.sessions[0].isoformat() if self.sessions else None,
                "last": self.sessions[-1].isoformat() if self.sessions else None,
                "no_file": [d.isoformat() for d in self.no_file],
                "added_sessions": [d.isoformat() for d in self.added_sessions],
                "filled": len(self.filled), "filled_examples": [f"{t} {d}" for t, d in self.filled[:10]],
                "compared": self.compared,
                "disagree": [{"ticker": t, "day": d.isoformat(), "pct": round(p, 2)} for t, d, p in self.disagree]}

    def line(self) -> str:
        if not self.sessions:
            return "NSE's official price files could not be read; Yahoo's prices were used unchecked."
        text = (f"Checked against NSE's official closing prices, {self.sessions[0]:%d %b} to "
                f"{self.sessions[-1]:%d %b}: {self.compared:,} prices agreed (allowing for dividends)")
        if self.filled:
            text += f", {len(self.filled)} missing from Yahoo were filled from NSE"
        if self.disagree:
            text += f", {len(self.disagree)} disagree by over {DISAGREE_PCT:g}% (kept, listed in the job log)"
        return text + "."


def _session() -> Any:
    import requests
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def parse_cash(text: bytes) -> pd.DataFrame:
    """The cash-market bhavcopy zip as rows: ticker (Yahoo style, .NS), Open, High, Low, Close."""
    with zipfile.ZipFile(io.BytesIO(text)) as z:
        raw = pd.read_csv(z.open(z.namelist()[0]))
    raw = raw[raw["SctySrs"].isin(SERIES)]
    out = pd.DataFrame({"ticker": raw["TckrSymb"].astype(str) + ".NS",
                        "Open": raw["OpnPric"], "High": raw["HghPric"], "Low": raw["LwPric"],
                        "Close": raw["ClsPric"]})
    return out.drop_duplicates("ticker").set_index("ticker").astype(float)


def parse_indexes(text: str) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text))
    raw["name"] = raw["Index Name"].astype(str).str.strip().str.upper()
    rows = {}
    for ticker, name in INDEX_NAMES.items():
        hit = raw[raw["name"] == name]
        if not hit.empty:
            r = hit.iloc[0]
            rows[ticker] = {"Open": r["Open Index Value"], "High": r["High Index Value"],
                            "Low": r["Low Index Value"], "Close": r["Closing Index Value"]}
    return pd.DataFrame.from_dict(rows, orient="index").apply(pd.to_numeric, errors="coerce")


def fetch_day(day: date, session: Any) -> pd.DataFrame | None:
    """`day`'s prices from NSE (stocks and the two indexes), or None when NSE has no file for it."""
    r = session.get(CM_URL.format(ymd=f"{day:%Y%m%d}"), timeout=60)
    if r.status_code != 200 or r.content[:2] != b"PK":
        return None
    frame = parse_cash(r.content)
    i = session.get(INDEX_URL.format(dmy=f"{day:%d%m%Y}"), timeout=60)
    if i.status_code == 200 and i.text.lstrip().startswith("Index Name"):
        frame = pd.concat([frame, parse_indexes(i.text)])
    return frame


def fetch(days: Iterable[date], *, session: Any = None, pause: float = 0.3,
          log: Callable[..., None] = print) -> tuple[dict[date, pd.DataFrame], list[date]]:
    """({session: prices}, weekdays with no file). Weekends are skipped; a weekday without a
    file is a holiday or a day NSE has not published yet - the caller cannot tell which."""
    session = session or _session()
    found, missing = {}, []
    for d in days:
        if d.weekday() >= 5:
            continue
        try:
            frame = fetch_day(d, session)
        except Exception as exc:        # noqa: BLE001 - a check, not the books
            log(f"NSE file for {d}: {type(exc).__name__}: {exc}")
            frame = None
        if frame is None:
            missing.append(d)
        else:
            found[d] = frame
        time.sleep(pause)
    return found, missing


def weekdays(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)
            if (first + timedelta(days=i)).weekday() < 5]


def repair(ohlcv: pd.DataFrame, nse: dict[date, pd.DataFrame], *, no_file: Iterable[date] = ()
           ) -> tuple[pd.DataFrame, Check]:
    """Yahoo's prices (columns (ticker, field), rows are sessions as dates) with every missing
    price on an NSE session filled from NSE, and what was found."""
    check = Check(sessions=sorted(nse), no_file=sorted(no_file))
    out = ohlcv.copy()
    for d in check.sessions:
        if d not in out.index:
            out.loc[d] = float("nan")
            check.added_sessions.append(d)
    out = out.sort_index()
    tickers = sorted({t for t, _ in out.columns})
    for t in tickers:
        close = out[(t, "Close")]
        for d in check.sessions:
            frame = nse[d]
            if t not in frame.index or not frame.loc[t, "Close"] > 0:
                continue
            official = frame.loc[t]
            # The feeds' ratio on the nearest earlier day both have: Yahoo's dividend adjustment.
            ratio = 1.0
            for e in reversed([e for e in check.sessions if e < d]):
                y = close.get(e)
                n = nse[e]["Close"].get(t) if t in nse[e].index else None
                if y is not None and n and pd.notna(y) and n > 0:
                    ratio = float(y) / float(n)
                    break
            mine = close.get(d)
            if mine is None or pd.isna(mine):
                for f in FIELDS:
                    out.loc[d, (t, f)] = float(official[f]) * ratio
                if (t, "Volume") in out.columns and pd.isna(out.loc[d, (t, "Volume")]):
                    out.loc[d, (t, "Volume")] = 0.0
                check.filled.append((t, d))
                continue
            check.compared += 1
            apart = 100 * (float(mine) / (float(official["Close"]) * ratio) - 1)
            if abs(apart) > DISAGREE_PCT:
                check.disagree.append((t, d, apart))
    return out, check
