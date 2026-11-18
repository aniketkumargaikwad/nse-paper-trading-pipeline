"""NSE's official files complete Yahoo's prices for the paper books (research/nse_prices.py)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import nse_prices as nse  # noqa: E402

D1, D2, D3 = date(2026, 11, 13), date(2026, 11, 16), date(2026, 11, 17)


def yahoo(rows: dict) -> pd.DataFrame:
    """{ticker: [close per day]} on D1, D2 as Yahoo would give them (D3 not published yet)."""
    parts = []
    for t, closes in rows.items():
        f = pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": 1.0},
                         index=[D1, D2][:len(closes)])
        f.columns = pd.MultiIndex.from_product([[t], list(f.columns)])
        parts.append(f)
    return pd.concat(parts, axis=1)


def official(prices: dict) -> pd.DataFrame:
    return pd.DataFrame({f: prices for f in nse.FIELDS})


def test_a_day_yahoo_has_not_published_is_filled_from_nse():
    y = yahoo({"AAA.NS": [100.0, 101.0], "^NSEI": [25000.0, 25100.0]})
    files = {D1: official({"AAA.NS": 100.0, "^NSEI": 25000.0}), D2: official({"AAA.NS": 101.0, "^NSEI": 25100.0}),
             D3: official({"AAA.NS": 104.0, "^NSEI": 25300.0})}
    out, check = nse.repair(y, files)
    assert out.loc[D3, ("AAA.NS", "Close")] == 104.0 and out.loc[D3, ("^NSEI", "Close")] == 25300.0
    assert check.added_sessions == [D3] and len(check.filled) == 2 and check.compared == 4
    assert "2 missing from Yahoo were filled from NSE" in check.line()


def test_a_filled_price_keeps_yahoos_dividend_adjustment():
    """Yahoo's past prices sit below NSE's by the dividend; a filled price must sit on Yahoo's scale."""
    y = yahoo({"AAA.NS": [98.0, 99.0]})
    files = {D1: official({"AAA.NS": 100.0}), D2: official({"AAA.NS": 101.0}), D3: official({"AAA.NS": 110.0})}
    out, _ = nse.repair(y, files)
    assert abs(out.loc[D3, ("AAA.NS", "Close")] - 110.0 * 99.0 / 101.0) < 1e-9


def test_a_large_disagreement_is_reported_and_yahoo_is_kept():
    y = yahoo({"AAA.NS": [100.0, 150.0]})
    out, check = nse.repair(y, {D1: official({"AAA.NS": 100.0}), D2: official({"AAA.NS": 101.0})})
    assert out.loc[D2, ("AAA.NS", "Close")] == 150.0
    assert [(t, d) for t, d, _ in check.disagree] == [("AAA.NS", D2)]


def test_no_nse_files_says_the_prices_were_not_checked():
    out, check = nse.repair(yahoo({"AAA.NS": [100.0]}), {})
    assert "could not be read" in check.line() and len(out) == 1


def test_the_index_file_is_read_by_name():
    text = ("Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value\n"
            "Nifty 50,17-11-2026,25277.3,25400,25200,25387.4\nNifty Bank,17-11-2026,1,1,1,1\n")
    frame = nse.parse_indexes(text)
    assert list(frame.index) == ["^NSEI"] and frame.loc["^NSEI", "Close"] == 25387.4
