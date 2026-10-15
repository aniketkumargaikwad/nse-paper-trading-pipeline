"""Checks on one day's normalised F&O rows (design §2.6).

Issues are reported, never repaired: nothing is filled in or invented. They
go to data/fno/quality/<YEAR>.csv rather than data_quality_flags, which
needs a stock or index instrument_id (design §2.6 note).
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from fno.contracts import ALWAYS_LISTED

REPORT_COLUMNS = ("trade_date", "underlying", "check", "count", "example")
CONTRACT_KEY = ["underlying", "instrument", "expiry", "strike", "option_type"]


def _label(row: pd.Series) -> str:
    return f"{row['instrument']} {row['expiry']} {row['strike']} {row['option_type']}"


def check_day(frame: pd.DataFrame, day: date) -> list[dict[str, Any]]:
    """Everything suspicious about `day`'s rows, one entry per check and underlying."""
    issues: list[dict[str, Any]] = []

    def add(check: str, underlying: str, rows: pd.DataFrame | None) -> None:
        issues.append({
            "trade_date": day, "underlying": underlying, "check": check,
            "count": 1 if rows is None else len(rows),
            "example": "" if rows is None else _label(rows.iloc[0]),
        })

    for underlying in ALWAYS_LISTED:
        if not (frame["underlying"] == underlying).any():
            add("missing_underlying", underlying, None)

    traded = frame[frame["volume_contracts"] > 0]
    body_low = traded[["open", "close"]].min(axis=1)
    body_high = traded[["open", "close"]].max(axis=1)
    bad = traded[(traded["low"] > body_low) | (traded["high"] < body_high) | (traded["low"] <= 0)]
    for underlying, rows in bad.groupby("underlying"):
        add("ohlc_invalid", underlying, rows)

    unsettled = frame[(frame["oi_units"] > 0) & ~(frame["settle"] > 0)]
    for underlying, rows in unsettled.groupby("underlying"):
        add("settle_missing", underlying, rows)

    dup = frame[frame.duplicated(CONTRACT_KEY, keep=False)]
    for underlying, rows in dup.groupby("underlying"):
        add("duplicate_contract", underlying, rows)
    return issues


def write_issues(root: Path, issues: list[dict[str, Any]]) -> None:
    """Append `issues` to the report for their year."""
    for issue in issues:
        path = Path(root) / "quality" / f"{issue['trade_date'].year}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, lineterminator="\n")
            if new:
                w.writeheader()
            w.writerow({**issue, "trade_date": issue["trade_date"].isoformat()})
