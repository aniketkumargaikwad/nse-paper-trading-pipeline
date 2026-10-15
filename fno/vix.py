"""India VIX, daily, from Yahoo's ^INDIAVIX (from 2008; design §2.1).

The whole history is small (~4,500 rows), so each refresh rewrites the file.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from fno.yahoo import COLUMNS, fetch, normalise, read_whole, write_whole  # noqa: F401 - re-exported

SYMBOL = "^INDIAVIX"


def fetch_daily() -> pd.DataFrame:
    return fetch(SYMBOL)


def vix_path(root: Path) -> Path:
    return Path(root) / "vix" / "daily.parquet"


def write(root: Path, frame: pd.DataFrame) -> None:
    write_whole(vix_path(root), frame)


def read(root: Path) -> pd.DataFrame:
    return read_whole(vix_path(root))
