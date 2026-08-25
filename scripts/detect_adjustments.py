"""Measure the gap between Dhan's two feeds and store it as corrections.

WHAT THIS FIXES
---------------
Dhan's daily feed is adjusted for corporate actions; its intraday feed is raw.
EICHERMOT's 5-minute candles say 21,780 on 2020-08-21 and 2,178 the next
morning - a 90% collapse that never happened, it was a 1:10 split. Eighteen of
the fifty NIFTY 50 symbols carry at least one of these breaks.

A backtest spanning one does not error and does not look wrong. A breakout
rule simply finds the strongest signal in its entire sample and builds a
result on it.

HOW IT MEASURES
---------------
Not by looking up split announcements. The daily feed is adjusted to TODAY's
basis, so the gap between the two feeds on any past day IS the cumulative
adjustment still owed to that day. Measure the gap and every event the daily
feed accounts for is handled - splits, bonuses, demergers alike - including
the ones no split detector would flag.

The gap is noisy day to day (the intraday close is the last 5-minute candle,
the daily close includes the closing auction), so it is treated as constant
between corporate actions and taken as a median. See price_adjust.py.

WHAT IT WRITES
--------------
Rows in `price_adjustments`, applied when candles are READ. Stored candles are
never modified: a corrected price is indistinguishable from a real one, so the
correction has to be somewhere you can look at it and argue with it.

Usage
-----
    python scripts/detect_adjustments.py --dry-run      # show, store nothing
    python scripts/detect_adjustments.py                # measure and store
    python scripts/detect_adjustments.py --symbol NSE:TCS
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, UTC, use_utf8_stdout  # noqa: E402
from price_adjust import (                     # noqa: E402
    daily_ratio,
    find_segments,
    material_adjustments,
)

STORED_TIMEFRAME = "5m"


def _last_close_per_day(candles: pd.DataFrame) -> pd.Series:
    """The final close of each trading day, indexed by IST date."""
    if candles.empty:
        return pd.Series(dtype="float64")
    days = candles.index.tz_convert(IST).date
    return candles["close"].groupby(days).last()


def _daily_close(candles: pd.DataFrame) -> pd.Series:
    if candles.empty:
        return pd.Series(dtype="float64")
    days = candles.index.tz_convert(IST).date
    return candles["close"].groupby(days).last()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", action="append", dest="symbols")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print what would be stored and change nothing",
    )
    args = parser.parse_args()

    use_utf8_stdout()

    from config import get_settings
    from db import SupabaseStore
    from dhan_factory import create_candle_store
    from universes import load_constituents

    try:
        client = SupabaseStore.connect(get_settings())._client
        store = create_candle_store(client)
        # The Parquet backend delegates everything except candles to
        # Supabase, so `store._backend` reaches the adjustment table either
        # way. A maintenance script, not a caller: reaching in is the point.
        backend = store._backend  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    symbols = args.symbols or list(load_constituents("NIFTY50").symbols)

    to_utc = datetime.now(tz=UTC)
    from_utc = to_utc - timedelta(days=int(9.5 * 365))

    total = 0
    for symbol in symbols:
        try:
            intraday = store.get_candles(symbol, STORED_TIMEFRAME, from_utc, to_utc)
            daily = store.get_candles(symbol, "day", from_utc, to_utc)
        except Exception as exc:  # noqa: BLE001 - one bad symbol must not stop the sweep
            print(f"{symbol:16} SKIP  {exc}")
            continue

        if intraday.empty or daily.empty:
            print(f"{symbol:16} SKIP  no overlapping history")
            continue

        # NOTE: get_candles already applies any corrections stored by a
        # PREVIOUS run, so the ratio measured here is what remains AFTER them.
        # That is what makes re-running safe and convergent: a correct set of
        # rows measures back to 1.0 and produces nothing new.
        ratio = daily_ratio(_last_close_per_day(intraday), _daily_close(daily))
        if ratio.empty:
            print(f"{symbol:16} SKIP  no common trading days")
            continue

        volume = daily_ratio(
            _last_close_per_day(intraday.assign(close=intraday["volume"])),
            _daily_close(daily.assign(close=daily["volume"])),
        )
        found = material_adjustments(find_segments(ratio, volume))

        if not found:
            print(f"{symbol:16} clean")
            if not args.dry_run:
                # Still write: an emptied set must clear stale rows from an
                # earlier, worse detection run.
                backend.replace_price_adjustments(
                    backend.instrument_id(symbol), STORED_TIMEFRAME, []
                )
            continue

        for a in found:
            print(
                f"{symbol:16} {a.effective_from} .. {a.effective_to} "
                f"x{a.price_factor:.5f}  ({a.sample_days} days)"
            )
        total += len(found)

        if not args.dry_run:
            backend.replace_price_adjustments(
                backend.instrument_id(symbol), STORED_TIMEFRAME, found
            )

    verb = "would store" if args.dry_run else "stored"
    print(f"\n{verb} {total} corrections across {len(symbols)} symbols")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
