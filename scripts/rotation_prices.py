"""Download adjusted daily closes for today's NIFTY 200 and NIFTY 50 from Yahoo.

    python scripts/rotation_prices.py

Writes data/rotation/closes.parquet: one column per symbol (".NS" suffix),
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


def download(log=print) -> pd.DataFrame:
    import yfinance as yf
    tickers = [s + ".NS" for s in pd.read_csv(UNIVERSE)["Symbol"]] + INDEXES
    out = {}
    for i in range(0, len(tickers), 40):
        chunk = tickers[i:i + 40]
        df = yf.download(chunk, start="2008-01-01", auto_adjust=True, progress=False,
                         group_by="ticker", threads=True)
        for t in chunk:
            try:
                c = df[t]["Close"].dropna()
            except KeyError:
                continue
            if len(c):
                out[t] = c
        time.sleep(2)
    closes = pd.DataFrame(out)
    closes.index = pd.to_datetime(closes.index).date
    missing = sorted(set(tickers) - set(out))
    log(f"{closes.shape[1]} series, {closes.index.min()} -> {closes.index.max()}"
        + (f"; missing {', '.join(missing)}" if missing else ""))
    return closes


def main() -> int:
    closes = download()
    if "^NSEI" not in closes:
        print("NIFTY 50 (^NSEI) did not download; nothing written", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    closes.to_parquet(OUT)
    print(f"written: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
