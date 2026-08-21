"""Warm the candle cache so backtests run instantly and offline.

Usage (from the project folder):

    .venv\\Scripts\\python.exe backfill.py --refresh-instruments
    .venv\\Scripts\\python.exe backfill.py --symbols NSE:RELIANCE,NSE:TCS --years 2
    .venv\\Scripts\\python.exe backfill.py --symbols NSE:RELIANCE --timeframe day --years 5

Backfilling is deliberately explicit rather than automatic: Dhan serves 90
days per request, so a five-year pull across many symbols takes a while and
should be something you start knowingly.

Only the STORED timeframes ('5m' and 'day') can be backfilled. Everything else
(15m/25m/30m/60m) is derived by resampling the 5-minute base at read time, so
there is nothing to fetch for them.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timedelta

from config import IST, STORED_TIMEFRAMES, UTC, get_settings, use_utf8_stdout
from instruments import SYMBOL_RE


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill the candle cache from the configured provider."
    )
    parser.add_argument(
        "--symbols",
        help="comma-separated EXCHANGE:SYMBOL list, e.g. NSE:RELIANCE,NSE:TCS",
    )
    parser.add_argument(
        "--years", type=float, default=2.0,
        help="years of history to ensure (default 2)",
    )
    parser.add_argument(
        "--timeframe", default="5m", choices=list(STORED_TIMEFRAMES),
        help="stored timeframe to backfill (default 5m); other timeframes are "
             "derived by resampling and need no backfill",
    )
    parser.add_argument(
        "--refresh-instruments", action="store_true",
        help="download and store the Dhan security master, then exit",
    )
    return parser


def expand_symbols(raw: str) -> list[str]:
    """Parse a comma-separated symbol list into validated, deduped symbols."""
    if not raw or not raw.strip():
        return []
    out: list[str] = []
    for part in raw.split(","):
        symbol = part.strip().upper()
        if not symbol:
            continue
        if not SYMBOL_RE.match(symbol):
            raise ValueError(
                f"Malformed symbol {symbol!r}. Expected EXCHANGE:TRADINGSYMBOL, "
                "e.g. NSE:RELIANCE."
            )
        if symbol not in out:
            out.append(symbol)
    return out


def refresh_instruments(client) -> int:
    """Download the Dhan security master and store it."""
    import requests

    from instruments import SECURITY_MASTER_URL, parse_security_master
    from supabase_candle_backend import SupabaseCandleBackend

    print(f"Downloading the Dhan security master from {SECURITY_MASTER_URL} ...")
    try:
        response = requests.get(SECURITY_MASTER_URL, timeout=120)
    except requests.exceptions.RequestException as exc:
        print(
            f"ERROR: could not download the security master ({type(exc).__name__}).",
            file=sys.stderr,
        )
        return 1
    if response.status_code >= 400:
        print(f"ERROR: download failed with HTTP {response.status_code}", file=sys.stderr)
        return 1

    # IST explicitly, not the machine's local zone: a laptop in another
    # timezone must still stamp the Indian trading date.
    today_ist = datetime.now(tz=UTC).astimezone(IST).date()
    found = parse_security_master(response.text, refreshed_on=today_ist)
    written = SupabaseCandleBackend(client).upsert_instruments(
        [i.to_row() for i in found]
    )
    if written < len(found):
        print(
            f"  note: collapsed {len(found) - written} duplicate symbol(s) "
            "(Dhan's master lets an ETF and an index share a ticker)."
        )
    print(f"Stored {written} instruments.")
    return 0


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    args = build_parser().parse_args(argv)

    # Check "nothing to do" BEFORE connecting: you should not need
    # credentials configured to be told you forgot an argument.
    try:
        symbols = expand_symbols(args.symbols or "")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not symbols and not args.refresh_instruments:
        print(
            "Nothing to do. Pass --symbols NSE:RELIANCE,NSE:TCS to warm the "
            "cache, or --refresh-instruments to load the Dhan security master "
            "(do that first, once).",
            file=sys.stderr,
        )
        return 1

    try:
        settings = get_settings()
        from db import SupabaseStore

        store = SupabaseStore.connect(settings)
    except Exception as exc:
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    if args.refresh_instruments:
        return refresh_instruments(store._client)

    from dhan_factory import create_candle_store

    candle_store = create_candle_store(store._client)
    to_utc = datetime.now(tz=UTC)
    from_utc = to_utc - timedelta(days=math.ceil(args.years * 365.25))

    print(
        f"Backfilling {len(symbols)} symbol(s), {args.years} year(s) of "
        f"{args.timeframe} candles. Dhan serves 90 days per request, so this "
        "pages - it may take a few minutes per symbol."
    )

    failures = 0
    for symbol in symbols:
        print(f"  {symbol:<18} {args.timeframe} ...", end=" ", flush=True)
        try:
            # extend_to_now=True: catching up to the present is exactly this
            # command's job, unlike a read, which leaves the tail alone.
            candle_store.ensure_coverage(
                symbol, args.timeframe, from_utc, to_utc, extend_to_now=True
            )
        except Exception as exc:
            failures += 1
            print(f"FAILED: {exc}")
        else:
            print("ok")

    print(
        f"\nDone: {len(symbols) - failures} of {len(symbols)} symbol(s) covered "
        f"for {args.years} year(s) of {args.timeframe} candles."
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
