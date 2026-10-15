"""Daily OHLC from Yahoo, normalised to trade_date/open/high/low/close."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

COLUMNS = ["trade_date", "open", "high", "low", "close"]


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename(columns=str.lower)[["open", "high", "low", "close"]]
    frame = frame.dropna(subset=["close"])
    frame.insert(0, "trade_date", pd.DatetimeIndex(frame.index).date)
    return frame.reset_index(drop=True)[COLUMNS]


def fetch(symbol: str) -> pd.DataFrame:
    import yfinance as yf

    return normalise(yf.download(symbol, period="max", interval="1d",
                                 auto_adjust=False, progress=False))


def write_whole(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read_whole(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
