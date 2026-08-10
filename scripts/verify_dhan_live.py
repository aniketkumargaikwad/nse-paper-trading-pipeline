"""Live end-to-end check of the Phase 0 data foundation.

Run once, from the project folder, with Dhan credentials in .env:

    .venv\\Scripts\\python.exe scripts\\verify_dhan_live.py

Proves, against the real API and database:
  1. a token is obtained unattended (no manual login),
  2. the timestamp interpretation is right (candles land in the NSE session),
  3. candles are fetched and stored,
  4. a second read makes NO network call (cache hit),
  5. resampling produces sane higher timeframes,
  6. reading works with the market closed.

Check 2 matters most: Dhan's epoch timestamps are read as true UTC instants,
and that assumption has never been verified against a live response. If it is
wrong, every candle is shifted by 5h30m and every backtest is silently
corrupted. This script is where that gets settled.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from config import IST, UTC, get_settings
from db import SupabaseStore

load_dotenv()

SYMBOL = "NSE:RELIANCE"


def main() -> int:
    settings = get_settings()
    if settings.data_provider != "dhan":
        print(f"NOTE: DATA_PROVIDER is {settings.data_provider!r}, not 'dhan'.")
        print("Set DATA_PROVIDER=dhan in .env to verify the Dhan path.")
        return 1

    store = SupabaseStore.connect(settings)
    client = store._client

    from candle_store import CandleStore
    from dhan_factory import create_candle_store, create_dhan_provider
    from supabase_candle_backend import SupabaseCandleBackend

    print("1. Obtaining an access token unattended ...")
    provider = create_dhan_provider(client)
    token = provider._tokens.get_access_token()
    print(f"   OK - token obtained (length {len(token)}; value not shown)")

    candle_store = create_candle_store(client)
    to_utc = datetime.now(tz=UTC)
    from_utc = to_utc - timedelta(days=30)

    print(f"2/3. Fetching 30 days of 5-minute candles for {SYMBOL} ...")
    candle_store.ensure_coverage(SYMBOL, "5m", from_utc, to_utc, extend_to_now=True)
    base = candle_store.get_candles(SYMBOL, "5m", from_utc, to_utc)
    if base.empty:
        print("   FAILED - no candles returned. Check the symbol is in the")
        print("   instruments table (run: python backfill.py --refresh-instruments).")
        return 1
    print(f"   OK - {len(base)} candles, {base.index[0]} .. {base.index[-1]}")

    # THE critical check: are Dhan's epoch timestamps UTC instants, or already
    # IST-shifted? The discriminator is the FIRST candle of a normal session:
    # read correctly it lands at exactly 09:15 IST; misread by 5h30m it would
    # land at 14:45. Nothing else separates the two readings as cleanly.
    ist = base.index.tz_convert(IST)
    first = ist[0]
    print(f"   First candle in IST: {first:%Y-%m-%d %H:%M} (session opens 09:15)")
    if (first.hour, first.minute) != (9, 15):
        print("   FAILED - the first candle is not at 09:15 IST.")
        print("   If it reads 14:45, Dhan's timestamps are IST-shifted rather")
        print("   than UTC epochs: fix parse_candle_payload in providers/dhan.py.")
        return 1
    print("   OK - first candle lands exactly on the open (timestamps correct)")

    # A few candles legitimately sit outside 09:15-15:30 and are NOT errors:
    #   * Muhurat trading - NSE's ceremonial Diwali evening session (~18:00)
    #   * occasional post-close prints just after 15:30
    # These are real market data, so they are reported, not treated as failures.
    minute_of_day = ist.hour * 60 + ist.minute
    outside = base.index[(minute_of_day < 9 * 60 + 15) | (minute_of_day > 15 * 60 + 30)]
    if len(outside):
        share = 100.0 * len(outside) / len(base)
        dates = sorted({d.astimezone(IST).date().isoformat() for d in outside})
        print(f"   note: {len(outside)} candle(s) ({share:.1f}%) outside regular "
              f"hours on {', '.join(dates[:5])}")
        print("         - expected for muhurat sessions and post-close prints.")
        if share > 5.0:
            print("   FAILED - too many candles outside session hours to be")
            print("   explained by special sessions; investigate before trusting.")
            return 1

    print("4. Re-reading (must make NO network call) ...")

    class ExplodingProvider:
        name = "exploding"

        def max_history_days(self, timeframe: str) -> int:
            return 5 * 365

        def fetch(self, *a, **kw):
            raise AssertionError("cache miss: it hit the network")

    offline = CandleStore(SupabaseCandleBackend(client), ExplodingProvider())
    cached = offline.get_candles(SYMBOL, "5m", from_utc, to_utc)
    print(f"   OK - {len(cached)} candles served entirely from cache")

    print("5. Resampling to higher timeframes ...")
    for timeframe in ("15m", "30m", "60m"):
        frame = offline.get_candles(SYMBOL, timeframe, from_utc, to_utc)
        first_bucket = frame.index[0].astimezone(IST)
        print(f"   {timeframe:>4}: {len(frame):>5} candles, "
              f"first bucket {first_bucket:%Y-%m-%d %H:%M} IST")

    print("6. Sanity-checking the aggregation ...")
    day = base.index[-1].astimezone(IST).date()
    same_day_base = base[base.index.tz_convert(IST).date == day]
    fifteen = offline.get_candles(SYMBOL, "15m", from_utc, to_utc)
    same_day_15 = fifteen[fifteen.index.tz_convert(IST).date == day]
    drift = abs(same_day_base["volume"].sum() - same_day_15["volume"].sum())
    if drift >= 1:
        print(f"   FAILED - resampled volume differs by {drift}")
        return 1
    print("   OK - resampled volume matches the 5-minute base exactly")

    print("\nAll checks passed. The data foundation is live.")
    print("Re-run this outside market hours to confirm the offline property.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
