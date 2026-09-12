"""Download index price history from Yahoo into the parquet store, once.

WHY
---
Dhan's data subscription has lapsed, and research tests nine indexes as well
as the NIFTY200 stocks. Yahoo serves index history free: daily back to
2007-2011, and 60-minute for a rolling 730 days. Intraday index history older
than that is not available free at all, so indexes are tested on 60m and day
only (research/universe.py).

Candles after --data-end are dropped, so indexes stop on the same day as the
frozen stock history. Re-running is safe: writes merge on timestamp.

    python scripts/download_index_history.py --data-end 2026-08-27

Afterwards, back the new files up:

    python scripts/backup_candles_to_storage.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings, use_utf8_stdout  # noqa: E402

DOWNLOADS = (("day", "1d", "max"), ("60m", "60m", "730d"))


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-end", required=True, type=date.fromisoformat,
                        help="last date to keep, YYYY-MM-DD (the frozen DATA_END)")
    parser.add_argument("--root", default="data/candles", help="parquet store root")
    args = parser.parse_args(argv)

    import yfinance as yf

    from db import SupabaseStore
    from parquet_candle_backend import ParquetCandleBackend
    from research.prices import load_instrument_ids
    from research.universe import INDEXES
    from research.windows import trim_to_data_end
    from yfinance_client import normalize_yf_frame

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    store = SupabaseStore.connect(get_settings(require_supabase=True))
    ids = load_instrument_ids(store._client, INDEXES)
    backend = ParquetCandleBackend(None, args.root)

    failures = 0
    for symbol, yahoo_symbol in INDEXES.items():
        for timeframe, interval, period in DOWNLOADS:
            try:
                raw = yf.download(yahoo_symbol, interval=interval, period=period,
                                  progress=False, auto_adjust=False)
                frame = trim_to_data_end(normalize_yf_frame(raw), args.data_end)
            except Exception as exc:        # noqa: BLE001 - report and continue
                print(f"FAIL  {symbol:22s} {timeframe:4s} {exc!r}")
                failures += 1
                continue
            if frame.empty:
                print(f"FAIL  {symbol:22s} {timeframe:4s} Yahoo returned nothing")
                failures += 1
                continue
            frame["volume"] = frame["volume"].fillna(0.0)
            backend.write_candles(ids[symbol], timeframe, frame)
            print(f"OK    {symbol:22s} {timeframe:4s} {len(frame):6d} candles  "
                  f"{frame.index.min().date()} -> {frame.index.max().date()}")

    print("\nNext: python scripts/backup_candles_to_storage.py")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
