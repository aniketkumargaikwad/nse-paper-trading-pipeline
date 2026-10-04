"""The local F&O store: one Parquet file per underlying per year, and a ledger.

Layout under FNO_ROOT (default data/fno), mirroring the R2 bucket of step 3
so publishing is a copy (design §2.2):

    daily/<UNDERLYING>/<YEAR>.parquet
    daily/_sessions.csv            every day fetched, and what NSE said

Each write replaces one day whole, through a temporary file and an atomic
rename, so a run stopped halfway leaves every file either old or new.
"""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

import pandas as pd

from fno.nse_files import COLUMNS

DEFAULT_ROOT = "data/fno"
LEDGER = Path("daily") / "_sessions.csv"
SORT = ["trade_date", "instrument", "expiry", "option_type", "strike"]


def root_dir() -> Path:
    return Path(os.environ.get("FNO_ROOT", DEFAULT_ROOT))


def daily_path(root: Path, underlying: str, year: int) -> Path:
    return Path(root) / "daily" / underlying / f"{year}.parquet"


def replace_days(path: Path, frame: pd.DataFrame, days: set[date], *, sort: list[str]) -> None:
    """Swap the rows of `days` in the file at `path` for `frame`, atomically."""
    old = pd.read_parquet(path) if path.exists() else None
    if old is not None:
        old = old[~old["trade_date"].isin(days)]
    merged = frame if old is None or old.empty else pd.concat([old, frame], ignore_index=True)
    merged = merged.sort_values(sort, na_position="first", kind="stable").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    merged.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def replace_day(path: Path, frame: pd.DataFrame, day: date, *, sort: list[str]) -> None:
    replace_days(path, frame, {day}, sort=sort)


def write_days(root: Path, items: list[tuple[date, pd.DataFrame]]) -> None:
    """Store several days at once: one rewrite per underlying-year file.

    Each rewrite reads and writes the whole year, so a backfill that saved
    day by day spent most of its time rewriting files (measured 4 Oct 2026:
    ~1 s per underlying per day by year end). Batching is the same result.
    """
    for day, frame in items:
        if not frame.empty and (frame["trade_date"] != day).any():
            raise ValueError(f"rows for another day handed in as {day}")
    if not items:
        return
    days = {day for day, _ in items}
    rows = pd.concat([f for _, f in items], ignore_index=True)
    rows["_year"] = [d.year for d in rows["trade_date"]]
    for (underlying, year), part in rows.groupby(["underlying", "_year"]):
        replace_days(daily_path(root, underlying, year), part[list(COLUMNS)],
                     {d for d in days if d.year == year}, sort=SORT)


def write_day(root: Path, frame: pd.DataFrame, day: date) -> None:
    """Store one day's normalised rows, one file per underlying."""
    write_days(root, [(day, frame)])


def read_daily(root: Path, underlying: str, start: date, end: date) -> pd.DataFrame:
    """Every stored row for `underlying` with start <= trade_date <= end."""
    frames = [pd.read_parquet(p) for year in range(start.year, end.year + 1)
              if (p := daily_path(root, underlying, year)).exists()]
    if not frames:
        return pd.DataFrame(columns=list(COLUMNS))
    out = pd.concat(frames, ignore_index=True)
    keep = (out["trade_date"] >= start) & (out["trade_date"] <= end)
    return out[keep].reset_index(drop=True)[list(COLUMNS)]


def load_ledger(root: Path) -> dict[date, str]:
    path = Path(root) / LEDGER
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {date.fromisoformat(r["date"]): r["status"] for r in csv.DictReader(f)}


def record_session(root: Path, day: date, status: str, rows: int) -> None:
    path = Path(root) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["date", "status", "rows"])
        w.writerow([day.isoformat(), status, rows])
