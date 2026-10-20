"""Download adjusted daily closes for today's NIFTY 200 and NIFTY 50 from Yahoo.

    python scripts/rotation_prices.py

Writes data/rotation/closes.parquet: one column per symbol (".NS" suffix), and
data/rotation/ohlcv.parquet with every field (columns: ticker, field);
`^NSEI` for NIFTY 50, `^CRSLDX` for NIFTY 500, sessions from 2008. Prices are
split- and dividend-adjusted (auto_adjust). Free, no key.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pandas as pd

UNIVERSE = Path("data/universes/NIFTY200-2026-08-28.csv")
OUT = Path("data/rotation/closes.parquet")
INDEXES = ["^NSEI", "^CRSLDX"]


OHLCV = Path("data/rotation/ohlcv.parquet")
FIELDS = ("Open", "High", "Low", "Close", "Volume")


def download_ohlcv(start: str = "2008-01-01", log=print) -> pd.DataFrame:
    """Daily open/high/low/close/volume, adjusted; columns (ticker, field), rows are sessions."""
    import yfinance as yf
    tickers = [s + ".NS" for s in pd.read_csv(UNIVERSE)["Symbol"]] + INDEXES
    parts: dict[str, pd.DataFrame] = {}

    def fetch(chunk: list[str]) -> None:
        df = yf.download(chunk, start=start, auto_adjust=True, progress=False, group_by="ticker", threads=True)
        for t in chunk:
            try:
                frame = df[t][list(FIELDS)].dropna(how="all")
            except KeyError:
                continue
            if len(frame):
                frame.columns = pd.MultiIndex.from_product([[t], list(FIELDS)])
                parts[t] = frame

    for i in range(0, len(tickers), 40):
        fetch(tickers[i:i + 40])
        time.sleep(2)
    # Yahoo drops some tickers from a busy batch (20 Oct 2026: 13 of 202); ask again, smaller.
    for attempt in range(3):
        missing = [t for t in tickers if t not in parts]
        if not missing:
            break
        time.sleep(10 * (attempt + 1))
        for i in range(0, len(missing), 5):
            fetch(missing[i:i + 5])
            time.sleep(2)
    out = pd.concat(parts.values(), axis=1)
    out.index = pd.to_datetime(out.index).date
    got = {t for t, _ in out.columns}
    missing = sorted(set(tickers) - got)
    log(f"{len(got)} series, {out.index.min()} -> {out.index.max()}"
        + (f"; missing {', '.join(missing)}" if missing else ""))
    return out


def download(log=print) -> pd.DataFrame:
    """Adjusted closes only: one column per ticker."""
    ohlcv = download_ohlcv(log=log)
    return closes_of(ohlcv)


def closes_of(ohlcv: pd.DataFrame) -> pd.DataFrame:
    return ohlcv.xs("Close", axis=1, level=1)


def main() -> int:
    ohlcv = download_ohlcv()
    closes = closes_of(ohlcv)
    if "^NSEI" not in closes:
        print("NIFTY 50 (^NSEI) did not download; nothing written", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    closes.to_parquet(OUT)
    ohlcv.to_parquet(OHLCV)
    print(f"written: {OUT}, {OHLCV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
