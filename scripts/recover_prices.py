"""Recover the sessions the candle store is missing, from Yahoo, before they go.

THE SITUATION THIS EXISTS FOR
-----------------------------
The store stops on 31 July 2026. Dhan access lapsed on 11 September and is
not coming back, so every session since is simply absent - and the research
loop has been running all of them against a frozen wall.

Yahoo still serves 5-minute candles for roughly the last 58 days. That is
enough to recover the gap today. It is a ROLLING window, so this is not a
task that keeps: each missing session ages out 58 days after it happened,
oldest first. `--window` prints exactly what is left and when each part goes.

WHAT IT DOES
------------
For every symbol, for 5m and day:

  1. reads what the store already holds around the join,
  2. fetches the same span plus the gap from Yahoo,
  3. measures the two feeds against each other on the sessions they SHARE,
     and rescales the incoming candles onto the stored price basis,
  4. checks the result for gaps, duplicates, thin sessions, impossible
     candles and a step at the join,
  5. writes only what survives, only after the last stored candle.

Step 3 is the one that matters and the one that expires first. Yahoo is
split- and dividend-adjusted to today; Dhan's intraday feed is raw. Without
the overlap there is no way to tell how far apart the two bases are, and a
few percent of unremoved dividend adjustment at the seam is exactly the
artefact `price_adjust.py` exists to remove.

A symbol whose basis cannot be verified is REFUSED and reported, not
written. A visible gap is recoverable later; a silent step at the join gets
backtested on.

USAGE
-----
    python scripts/recover_prices.py --window          # what is still reachable
    python scripts/recover_prices.py --dry-run         # fetch, check, write nothing
    python scripts/recover_prices.py                   # write to the local store
    python scripts/recover_prices.py --symbols NSE:RELIANCE,NSE:TCS

Nothing here uploads. Recovered candles land in the LOCAL parquet store
(CANDLE_ROOT), and putting them in the bucket is a separate, deliberate step:

    python scripts/backup_candles_to_storage.py

Exit code is 1 if any symbol was refused or reported a problem, so a run that
half-worked cannot look like a clean one.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import (  # noqa: E402
    IST,
    MARKET_CLOSE_IST,
    UTC,
    get_settings,
    use_utf8_stdout,
)
from data_quality import check_ohlc_sanity  # noqa: E402
from market_calendar import covered_years, load_holidays  # noqa: E402
from price_recovery import (  # noqa: E402
    JOIN_BREAK_RATIO,
    RecoveryReport,
    earliest_recoverable,
    expires_on,
    recover,
    trading_sessions,
)

# The stored timeframes worth recovering. '1m' is held for two symbols and
# read by nothing, and Yahoo's 1-minute window is 7 days anyway - it was gone
# before anyone noticed the gap.
RECOVER_TIMEFRAMES: tuple[str, ...] = ("5m", "day")

# The date the store is believed to end, mirroring the DATA_END GitHub
# variable and docs/DEPLOYING.md. Used ONLY for the --window report, which
# has to answer "how long have I got" before any credential is available.
# Every real decision reads the symbol's own last candle, because the freeze
# is ragged - the September audit found 16 of 200 stocks holding complete
# 5-minute sessions past 6 August while the rest stop in July.
DATA_END = date(2026, 7, 31)

# How much of the join to read back from the store. The rebasing factor is
# measured on the sessions both feeds hold, and Yahoo reaches ~58 days back,
# so 90 days of stored context covers the whole possible overlap with room to
# spare while staying one cheap parquet read.
CONTEXT_DAYS = 90


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recover missing candles from Yahoo onto the stored basis.",
    )
    parser.add_argument(
        "--symbols",
        help="comma-separated EXCHANGE:SYMBOL list (default: the NIFTY200 snapshot)",
    )
    parser.add_argument(
        "--universe", default="NIFTY200",
        help="universe to recover when --symbols is not given (default NIFTY200)",
    )
    parser.add_argument(
        "--timeframes", default=",".join(RECOVER_TIMEFRAMES),
        help=f"which stored timeframes to recover (default {','.join(RECOVER_TIMEFRAMES)})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="fetch and check everything, write nothing",
    )
    parser.add_argument(
        "--window", action="store_true",
        help="report what is still reachable and when each part expires, then exit",
    )
    parser.add_argument(
        "--store-ends", default=DATA_END.isoformat(),
        help=(
            f"the last date the store is believed to hold, for --window only "
            f"(default {DATA_END.isoformat()}, the DATA_END variable). The "
            "real run reads each symbol's own tail instead, because the "
            "freeze is not uniform: some stocks hold sessions into August"
        ),
    )
    parser.add_argument(
        "--limit", type=int,
        help="stop after this many symbols (for a quick look before the full run)",
    )
    return parser


# ---------------------------------------------------------------------------
# The window report - the part that is time-critical
# ---------------------------------------------------------------------------


def window_report(
    store_ends: date, now_utc: datetime, holidays: frozenset[date], max_days: int
) -> str:
    """What can still be recovered, and the date each part stops being free."""
    today = now_utc.astimezone(IST).date()
    earliest = earliest_recoverable(now_utc, max_days)
    missing = trading_sessions(store_ends + timedelta(days=1), today, holidays)
    reachable = [d for d in missing if d >= earliest]
    lost = [d for d in missing if d < earliest]

    lines = [
        f"Store ends {store_ends.isoformat()}; today is {today.isoformat()}.",
        f"Yahoo serves 5-minute candles back to {earliest.isoformat()} "
        f"({max_days} days).",
        "",
        f"  missing sessions        {len(missing)}",
        f"  still recoverable       {len(reachable)}"
        + (f"  ({reachable[0]} -> {reachable[-1]})" if reachable else ""),
        f"  already unrecoverable   {len(lost)}"
        + (f"  ({lost[0]} -> {lost[-1]})" if lost else ""),
    ]

    if reachable:
        # The overlap is what makes any of it trustworthy, and it expires
        # FIRST - it is made of sessions the store already holds, which are
        # older than every session being recovered.
        overlap = [
            d for d in trading_sessions(earliest, store_ends, holidays)
        ]
        lines += [
            "",
            f"  sessions both feeds hold (used to verify the price basis): "
            f"{len(overlap)}",
        ]
        if overlap:
            last_overlap_day = expires_on(overlap[-1], max_days)
            lines.append(
                f"  the last of them leaves the window on "
                f"{last_overlap_day.isoformat()} "
                f"({(last_overlap_day - today).days} days from today)"
            )
            lines.append(
                "  AFTER THAT the candles are still fetchable but nothing is "
                "left to check them against."
            )
        else:
            lines.append(
                "  NONE LEFT - the basis can no longer be verified directly "
                "against the store."
            )
        lines += [
            "",
            f"  oldest missing session {reachable[0].isoformat()} expires "
            f"{expires_on(reachable[0], max_days).isoformat()} "
            f"({(expires_on(reachable[0], max_days) - today).days} days from today)",
            f"  newest missing session {reachable[-1].isoformat()} expires "
            f"{expires_on(reachable[-1], max_days).isoformat()}",
        ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------


def resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        from instruments import SYMBOL_RE

        out: list[str] = []
        for part in args.symbols.split(","):
            symbol = part.strip().upper()
            if not symbol:
                continue
            if not SYMBOL_RE.match(symbol):
                raise ValueError(
                    f"Malformed symbol {symbol!r}. Expected EXCHANGE:TRADINGSYMBOL."
                )
            if symbol not in out:
                out.append(symbol)
        return out

    from universes import load_constituents

    constituents = load_constituents(args.universe)
    if constituents.warning:
        print(f"  note: {constituents.warning}")
    return [f"NSE:{s}" for s in constituents.symbols]


def last_expected_session(timeframe: str, now_utc: datetime) -> date:
    """The most recent session that could legitimately have candles yet.

    A daily candle does not exist until the session closes, so listing today
    as an expected daily session reports "absent from the feed" every single
    morning about a candle nobody could have. Intraday is the opposite case:
    a part-finished session is real data, and its shortness is worth saying.
    """
    today = now_utc.astimezone(IST).date()
    if timeframe != "day":
        return today
    closed = now_utc.astimezone(IST).time() >= MARKET_CLOSE_IST
    return today if closed else today - timedelta(days=1)


def recover_one(
    symbol: str,
    timeframe: str,
    backend: Any,
    yahoo: Any,
    yahoo_symbol: str,
    holidays: frozenset[date],
    now_utc: datetime,
) -> tuple[pd.DataFrame, RecoveryReport]:
    """One symbol, one timeframe: read, fetch, rebase, check."""
    instrument_id = backend.instrument_id(symbol)

    max_days = yahoo.max_history_days(timeframe)
    earliest = earliest_recoverable(now_utc, max_days)
    # A FIXED context window, never the provider's own reach. Yahoo serves
    # daily candles for 10,000 days, and asking for all of them would fetch
    # twenty-seven years per symbol to measure an overlap that ninety days
    # covers many times over. The 5-minute client clamps a wider request to
    # its own 58-day wall by itself, so one window serves both.
    context_from = now_utc - timedelta(days=CONTEXT_DAYS)

    stored = backend.read_candles(instrument_id, timeframe, context_from, now_utc)
    if stored is None:
        stored = pd.DataFrame()

    incoming = yahoo.fetch_historical_candles(
        yahoo_symbol, timeframe, context_from, now_utc,
        closed_only=True, now_utc=now_utc,
    )

    store_ends = (
        stored.index[-1].astimezone(IST).date() if not stored.empty
        else earliest - timedelta(days=1)
    )
    expected = trading_sessions(
        store_ends + timedelta(days=1), last_expected_session(timeframe, now_utc),
        holidays,
    )

    invalid = [f.ts for f in check_ohlc_sanity(incoming)]
    return recover(
        symbol, timeframe, stored, incoming,
        expected_sessions=expected,
        earliest_available=earliest,
        invalid_timestamps=invalid,
    )


def main(argv: Sequence[str] | None = None) -> int:
    use_utf8_stdout()
    args = build_parser().parse_args(argv)

    now_utc = datetime.now(tz=UTC)
    today_ist = now_utc.astimezone(IST).date()

    holidays = load_holidays()
    known = covered_years()
    if today_ist.year not in known:
        print(
            f"ERROR: nse_holidays.yaml has no {today_ist.year} block, so every "
            "weekday would be treated as a trading day and the gap report "
            "would be wrong. Add it from NSE's circular first.",
            file=sys.stderr,
        )
        return 1

    from yfinance_client import YFinanceMarketDataClient

    yahoo = YFinanceMarketDataClient()

    try:
        store_ends = date.fromisoformat(args.store_ends)
    except ValueError:
        print(f"ERROR: --store-ends {args.store_ends!r} is not an ISO date.",
              file=sys.stderr)
        return 1

    if args.window:
        # Asked before any credential is needed: "how long have I got" should
        # be answerable on a laptop with nothing configured.
        print(window_report(store_ends, now_utc, holidays, yahoo.max_history_days("5m")))
        return 0

    try:
        symbols = resolve_symbols(args)
    except Exception as exc:                        # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.limit:
        symbols = symbols[: args.limit]

    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    unknown = [t for t in timeframes if t not in RECOVER_TIMEFRAMES]
    if unknown:
        print(
            f"ERROR: {', '.join(unknown)} is not recoverable here. "
            f"Only {', '.join(RECOVER_TIMEFRAMES)} are stored; every other "
            "intraday timeframe is resampled from 5m at read time.",
            file=sys.stderr,
        )
        return 1

    try:
        settings = get_settings()
        from db import SupabaseStore

        store = SupabaseStore.connect(settings)
        from dhan_factory import create_candle_backend

        backend = create_candle_backend(store._client)
    except Exception as exc:                        # noqa: BLE001
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    print(
        f"Recovering {len(symbols)} symbol(s) x {len(timeframes)} timeframe(s) "
        f"from Yahoo"
        + (" (DRY RUN - nothing will be written)" if args.dry_run else "")
    )
    print(window_report(store_ends, now_utc, holidays, yahoo.max_history_days("5m")))
    print()

    reports: list[RecoveryReport] = []
    written = 0
    for symbol in symbols:
        try:
            yahoo_symbol = yahoo.resolve_instrument_tokens([symbol], today_ist)[symbol]
        except Exception as exc:                    # noqa: BLE001
            print(f"{symbol:<18} SKIPPED: {exc}")
            continue

        for timeframe in timeframes:
            try:
                fresh, report = recover_one(
                    symbol, timeframe, backend, yahoo, yahoo_symbol,
                    holidays, now_utc,
                )
            except Exception as exc:                # noqa: BLE001
                report = RecoveryReport(symbol=symbol, timeframe=timeframe)
                report.problems.append(f"{type(exc).__name__}: {exc}")
                fresh = pd.DataFrame()

            reports.append(report)
            print(report.render())

            if not args.dry_run and not fresh.empty and report.ok:
                instrument_id = backend.instrument_id(symbol)
                backend.write_candles(instrument_id, timeframe, fresh)
                written += len(fresh)

    return summarise(reports, written, dry_run=args.dry_run)


def summarise(
    reports: Sequence[RecoveryReport], written: int, *, dry_run: bool
) -> int:
    """Print the closing tally and decide the exit code."""
    recovered = [r for r in reports if r.recovered_anything and r.ok]
    refused = [r for r in reports if not r.ok]
    candles = sum(r.candles for r in recovered)
    warned = [
        r for r in recovered
        if r.join_ratio is not None
        and abs(r.join_ratio - 1.0) > JOIN_BREAK_RATIO
    ]

    print()
    print("=" * 70)
    print(f"  recovered   {len(recovered)} of {len(reports)} symbol/timeframe pair(s), "
          f"{candles:,} candles")
    print(f"  refused     {len(refused)}")
    if dry_run:
        print("  written     nothing (dry run)")
    else:
        print(f"  written     {written:,} candles to the local store")
        if written:
            print(
                "\n  These are on this disk only. Putting them in the bucket "
                "is a separate step:\n"
                "      python scripts/backup_candles_to_storage.py"
            )

    if warned:
        # Written, because the window to fetch them again closes and a
        # flagged candle is worth more than an absent one - but named here
        # so nobody has to find them in the per-symbol output.
        print(
            f"\n  Written WITH a step at the join, most likely a corporate "
            f"action inside the gap. Check these {len(warned)} before "
            "trusting a backtest across them:"
        )
        for report in warned:
            print(
                f"    {report.symbol:<18} {report.timeframe:<4} "
                f"{(report.join_ratio - 1.0) * 100:+.1f}% across the seam"
            )

    if refused:
        print("\n  Refused - these were NOT written, and why:")
        for report in refused:
            reason = report.problems[0] if report.problems else "unknown"
            print(f"    {report.symbol:<18} {report.timeframe:<4} {reason}")

    unreachable = {
        day for r in reports for day in r.sessions_unreachable
    }
    if unreachable:
        print(
            f"\n  {len(unreachable)} session(s) are already past Yahoo's window "
            "and cannot be recovered from this source at any price: "
            f"{min(unreachable)} -> {max(unreachable)}"
        )
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
