"""FII / DII / Pro / Client open interest in equity derivatives, daily (design §2.1).

NSE's file: a title line, then a header, then one row per participant type
plus TOTAL. Counts are in CONTRACTS. Some header names carry trailing spaces.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from fno.nse_files import HEADERS, TIMEOUT_SECONDS
from fno.store import replace_day

URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv"
CLIENT_TYPES = ("Client", "DII", "FII", "Pro", "TOTAL")
NUMBERS = (
    "future_index_long", "future_index_short", "future_stock_long", "future_stock_short",
    "option_index_call_long", "option_index_put_long", "option_index_call_short",
    "option_index_put_short", "option_stock_call_long", "option_stock_put_long",
    "option_stock_call_short", "option_stock_put_short",
    "total_long_contracts", "total_short_contracts",
)


class ParticipantFileError(RuntimeError):
    """A participant-OI file that cannot be read as one."""


def download(day: date, session: Any) -> str | None:
    """`day`'s file as text, or None when NSE has none."""
    r = session.get(URL.format(ddmmyyyy=day.strftime("%d%m%Y")),
                    headers=HEADERS, timeout=TIMEOUT_SECONDS)
    if r.status_code == 404:
        return None
    text = r.content.decode("utf-8", "replace")
    if r.status_code != 200 or "Client Type" not in text:
        raise ParticipantFileError(f"{day}: HTTP {r.status_code}, not a participant-OI file")
    return text


def _snake(name: str) -> str:
    return "_".join(name.strip().lower().split())


def parse(text: str, day: date) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), skiprows=1, dtype=str, keep_default_na=False)
    raw.columns = [_snake(c) for c in raw.columns]
    missing = [c for c in ("client_type", *NUMBERS) if c not in raw.columns]
    if missing:
        raise ParticipantFileError(f"{day}: missing column(s) {', '.join(missing)}")
    raw["client_type"] = raw["client_type"].str.strip()
    rows = raw[raw["client_type"].isin(CLIENT_TYPES)].reset_index(drop=True)
    out = pd.DataFrame({"trade_date": [day] * len(rows), "client_type": rows["client_type"]})
    for col in NUMBERS:
        out[col] = pd.to_numeric(rows[col].str.strip(), errors="raise").astype("int64")
    return out


def path_for(root: Path, year: int) -> Path:
    return Path(root) / "participants" / f"{year}.parquet"


def write_day(root: Path, frame: pd.DataFrame, day: date) -> None:
    replace_day(path_for(root, day.year), frame, day, sort=["trade_date", "client_type"])


def read(root: Path, start: date, end: date) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for year in range(start.year, end.year + 1)
              if (p := path_for(root, year)).exists()]
    if not frames:
        return pd.DataFrame(columns=["trade_date", "client_type", *NUMBERS])
    out = pd.concat(frames, ignore_index=True)
    return out[(out["trade_date"] >= start) & (out["trade_date"] <= end)].reset_index(drop=True)
