"""Minute prices of expired NIFTY / BANKNIFTY options (design §2.2, §2.6).

WHICH: for each expiry, every strike within BAND_PCT of the index's daily
close on any of the expiry's final WEEKLY_SESSIONS (weekly cycles) or
MONTHLY_SESSIONS (monthly) sessions, for those sessions. Sessions come from
the NSE ledger, closes from fno/spot.py, weekly or monthly from Upstox's own
flag on the contracts.

WHERE: data/fno/minute/<UNDERLYING>/<EXPIRY>.parquet, one file per expiry,
written whole; minute/_expiries.csv records each expiry fetched and what it
held, so a stopped fill carries on and nothing is asked for twice.

CHECKED (written to quality/<YEAR>.csv like the daily checks, never repaired):
NSE's close for an option is the volume-weighted average of the last 30
minutes of trading, so that is what the minutes are checked against - the
average of the minute closes over 15:00-15:30, or 15:10-15:40 from 3 Aug 2026
when NSE moved the F&O close to 15:40 (circular NSE/FAOP/74467). Beyond the
larger of Rs 2 and 3% it is flagged (minute closes only approximate the trades,
so the tolerance is a little wider than the ~1% seen on real Feb 2025 and Oct
2026 minutes). Also flagged: a contract NSE says traded on a window day with
no minutes at all.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from datetime import date, time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fno.quality import write_issues
from fno.upstox import CANDLE_COLUMNS, Contract
from fno.yahoo import write_whole

WEEKLY_SESSIONS = 10
MONTHLY_SESSIONS = 20
BAND_PCT = 5.0
COLUMNS = ("ts", "strike", "option_type", "open", "high", "low", "close", "volume", "oi")
LEDGER = Path("minute") / "_expiries.csv"
LEDGER_COLUMNS = ("underlying", "expiry", "status", "contracts", "rows", "first_day", "fetched_on")
CLOSE_TOLERANCE_RS = 2.0
CLOSE_TOLERANCE_PCT = 3.0
CLOSE_MOVED_ON = date(2026, 8, 3)       # F&O close 15:30 -> 15:40


def window(expiry: date, sessions: list[date], *, weekly: bool) -> list[date]:
    """The expiry's final 10 (weekly) or 20 (monthly) sessions, the expiry day included."""
    before = [d for d in sorted(sessions) if d <= expiry]
    return before[-(WEEKLY_SESSIONS if weekly else MONTHLY_SESSIONS):]


def in_band(contracts: Iterable[Contract], closes: Mapping[date, float], days: list[date]) -> list[Contract]:
    levels = [closes[d] for d in days if d in closes and closes[d] == closes[d]]
    return [c for c in contracts
            if any(abs(c.strike / level - 1) * 100 <= BAND_PCT for level in levels)]


def expiry_path(root: Path, underlying: str, expiry: date) -> Path:
    return Path(root) / "minute" / underlying / f"{expiry.isoformat()}.parquet"


def read_expiry(root: Path, underlying: str, expiry: date) -> pd.DataFrame:
    path = expiry_path(root, underlying, expiry)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=list(COLUMNS))


def load_ledger(root: Path) -> dict[tuple[str, date], str]:
    path = Path(root) / LEDGER
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(r["underlying"], date.fromisoformat(r["expiry"])): r["status"] for r in csv.DictReader(f)}


def record(root: Path, row: Mapping[str, Any]) -> None:
    path = Path(root) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow({k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()})


def due(underlying: str, served: Iterable[date], ledger: Mapping[tuple[str, date], str],
        *, today: date) -> list[date]:
    """Served expiries that have expired and are not in the ledger (whatever they held)."""
    return sorted(e for e in served if e < today and (underlying, e) not in ledger)


def closing_window(day: date) -> tuple[time, time]:
    """The 30 minutes NSE averages for an option's close that day: [start, end)."""
    return (time(15, 10), time(15, 40)) if day >= CLOSE_MOVED_ON else (time(15, 0), time(15, 30))


def check_expiry(underlying: str, expiry: date, minutes: pd.DataFrame, daily: pd.DataFrame,
                 days: list[date], fetched: set[tuple[float, str]]) -> list[dict[str, Any]]:
    nse = daily[(daily["instrument"] == "OPT") & (daily["expiry"] == expiry)
                & daily["trade_date"].isin(days) & (daily["volume_contracts"] > 0)]
    # Boolean ARRAYS: an empty Python list would select columns, not rows.
    nse = nse.loc[np.array([(s, t) in fetched for s, t in zip(nse["strike"], nse["option_type"])],
                           dtype=bool)]
    if minutes.empty:
        days_seen = pd.DataFrame(columns=["day", "strike", "option_type"])
        closing = pd.DataFrame(columns=["day", "strike", "option_type", "close"])
    else:
        m = minutes.assign(day=minutes["ts"].dt.date, clock=minutes["ts"].dt.time)
        days_seen = m[["day", "strike", "option_type"]].drop_duplicates()
        in_window = np.array([closing_window(d)[0] <= c < closing_window(d)[1]
                              for d, c in zip(m["day"], m["clock"])], dtype=bool)
        w = m.loc[in_window & (m["volume"] > 0)]
        w = w.assign(value=w["close"] * w["volume"])
        sums = w.groupby(["day", "strike", "option_type"], as_index=False)[["value", "volume"]].sum()
        closing = sums.assign(close=sums["value"] / sums["volume"])[["day", "strike", "option_type", "close"]]
    # Typed even when empty, or the merge below refuses an object column against a float one.
    closing = closing.astype({"strike": "float64", "close": "float64"})
    issues: list[dict[str, Any]] = []

    merged = closing.merge(nse, left_on=["day", "strike", "option_type"],
                           right_on=["trade_date", "strike", "option_type"], suffixes=("_min", "_nse"))
    limit = np.maximum(CLOSE_TOLERANCE_RS, merged["close_nse"] * CLOSE_TOLERANCE_PCT / 100)         if len(merged) else pd.Series(dtype=float)
    bad = merged[(merged["close_min"] - merged["close_nse"]).abs() > limit] if len(merged) else merged
    for day, rows in bad.groupby("day"):
        r = rows.iloc[0]
        issues.append({"trade_date": day, "underlying": underlying, "check": "minute_close_mismatch",
                       "count": len(rows), "example": f"OPT {expiry} {r['strike']} {r['option_type']} "
                                                      f"last-30-min average {r['close_min']:.2f} "
                                                      f"vs NSE {r['close_nse']}"})

    have = set(zip(days_seen["day"], days_seen["strike"], days_seen["option_type"]))
    missing = nse.loc[np.array([(d, s, t) not in have for d, s, t in
                                zip(nse["trade_date"], nse["strike"], nse["option_type"])], dtype=bool)]
    for day, rows in missing.groupby("trade_date"):
        r = rows.iloc[0]
        issues.append({"trade_date": day, "underlying": underlying, "check": "minute_day_missing",
                       "count": len(rows), "example": f"OPT {expiry} {r['strike']} {r['option_type']}"})
    return sorted(issues, key=lambda i: (i["trade_date"], i["check"]))


def fetch_expiry(client: Any, root: Path, underlying: str, expiry: date, sessions: list[date],
                 closes: Mapping[date, float], *, daily: pd.DataFrame, today: date) -> dict[str, Any]:
    """Fetch, store and check one expiry; the ledger row to record (not yet recorded)."""
    row: dict[str, Any] = {"underlying": underlying, "expiry": expiry, "status": "empty",
                           "contracts": 0, "rows": 0, "first_day": "", "fetched_on": today}
    contracts = client.option_contracts(underlying, expiry)
    if not contracts:
        row["status"] = "no contracts"
        return row
    days = window(expiry, sessions, weekly=any(c.weekly for c in contracts))
    if not days:
        row["status"] = "no sessions"
        return row
    chosen = in_band(contracts, closes, days)
    frames = []
    for c in chosen:
        candles = client.minute_candles(c.key, days[0], expiry)
        if not candles.empty:
            frames.append(candles.assign(strike=c.strike, option_type=c.option_type))
    frame = (pd.concat(frames, ignore_index=True)[list(COLUMNS)] if frames
             else pd.DataFrame(columns=list(COLUMNS)))
    row.update(contracts=len(chosen), rows=len(frame), first_day=days[0])
    if not frame.empty:
        write_whole(expiry_path(root, underlying, expiry),
                    frame.sort_values(["strike", "option_type", "ts"]).reset_index(drop=True))
        row["status"] = "ok"
    write_issues(root, check_expiry(underlying, expiry, frame, daily, days,
                                    {(c.strike, c.option_type) for c in chosen}))
    return row
