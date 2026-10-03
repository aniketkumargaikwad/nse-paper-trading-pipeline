"""India VIX daily, from Yahoo (design §2.1)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import vix  # noqa: E402


def yahoo_like() -> pd.DataFrame:
    """yfinance's shape: a (field, ticker) column MultiIndex, a datetime index."""
    idx = pd.DatetimeIndex(["2026-09-29", "2026-09-30", "2026-10-01"], name="Date")
    cols = pd.MultiIndex.from_tuples(
        [(f, "^INDIAVIX") for f in ("Open", "High", "Low", "Close", "Adj Close", "Volume")],
        names=["Price", "Ticker"])
    data = [[13.0, 13.5, 12.8, 13.2, 13.2, 0],
            [13.2, 14.1, 13.0, 14.0, 14.0, 0],
            [float("nan")] * 5 + [0]]
    return pd.DataFrame(data, index=idx, columns=cols)


def test_normalise_gives_dated_ohlc_and_drops_empty_days():
    frame = vix.normalise(yahoo_like())
    assert list(frame.columns) == ["trade_date", "open", "high", "low", "close"]
    assert list(frame["trade_date"]) == [date(2026, 9, 29), date(2026, 9, 30)]
    assert frame["close"].iloc[-1] == 14.0


def test_stored_and_read_back(tmp_path):
    vix.write(tmp_path, vix.normalise(yahoo_like()))
    back = vix.read(tmp_path)
    assert len(back) == 2 and back["trade_date"].iloc[0] == date(2026, 9, 29)
