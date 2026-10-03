"""India VIX, daily, from Yahoo's ^INDIAVIX (from 2008; design §2.1).

The whole history is small (~4,500 rows), so each refresh rewrites the file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

SYMBOL = "^INDIAVIX"
COLUMNS = ["trade_date", "open", "high", "low", "close"]


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename(columns=str.lower)[["open", "high", "low", "close"]]
    frame = frame.dropna(subset=["close"])
    frame.insert(0, "trade_date", pd.DatetimeIndex(frame.index).date)
    return frame.reset_index(drop=True)[COLUMNS]


def fetch_daily() -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(SYMBOL, period="max", interval="1d", auto_adjust=False, progress=False)
    return normalise(raw)


def vix_path(root: Path) -> Path:
    return Path(root) / "vix" / "daily.parquet"


def write(root: Path, frame: pd.DataFrame) -> None:
    path = vix_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read(root: Path) -> pd.DataFrame:
    path = vix_path(root)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
