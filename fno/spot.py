"""Index daily closes from Yahoo, for the underlyings Yahoo covers.

FINNIFTY and MIDCPNIFTY have no usable Yahoo history (checked 4 Oct 2026);
their spot comes from NSE's underlying_price column (from 8 Jul 2024).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from fno.yahoo import fetch, read_whole, write_whole

YAHOO_SYMBOLS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYNXT50": "^NSMIDCP"}


def spot_path(root: Path, underlying: str) -> Path:
    return Path(root) / "spot" / f"{underlying}.parquet"


def refresh(root: Path, underlying: str) -> pd.DataFrame:
    frame = fetch(YAHOO_SYMBOLS[underlying])
    write(root, underlying, frame)
    return frame


def write(root: Path, underlying: str, frame: pd.DataFrame) -> None:
    write_whole(spot_path(root, underlying), frame)


def closes(root: Path, underlying: str) -> dict[date, float]:
    frame = read_whole(spot_path(root, underlying))
    return dict(zip(frame["trade_date"], frame["close"].astype(float)))
