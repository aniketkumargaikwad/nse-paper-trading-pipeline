"""Scan ALREADY-STORED candles for splits and missing sessions.

WHY THIS EXISTS
---------------
`data_quality.py` has shipped three detectors since the data foundation
landed. Only one of them — OHLC sanity — was ever called by production code.
`detect_suspected_splits` and `detect_session_gaps` were fully written and
fully tested, and then called by nothing but their own tests. That is why
`data_quality_flags` held zero rows after 3.1 million candles: not because
the data was perfect, but because nobody had looked.

Split detection now runs at ingestion (see `candle_store._split_flags`), which
protects every candle fetched from today onward. It does nothing for the two
years of history already in the database — and those are exactly the candles
every backtest is being run against.

An unadjusted 1:5 split appears as an 80% overnight collapse. A breakout or
momentum strategy reads that as the strongest signal in the sample and
"discovers" an edge that is a data artefact. That failure is invisible in the
results: the equity curve looks superb.

So this command re-reads stored candles and records what it finds. It writes
flags for review and CHANGES NO CANDLE. A silently corrected price is
indistinguishable from a real one, which is the same problem one layer down.

USAGE
-----
    python scripts/audit_data_quality.py                 # every stored symbol
    python scripts/audit_data_quality.py --symbols NSE:RELIANCE,NSE:TCS
    python scripts/audit_data_quality.py --dry-run       # report, write nothing

Exit code is 1 when anything was flagged, so CI can fail on dirty data.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import IST, UTC, use_utf8_stdout  # noqa: E402
from data_quality import (  # noqa: E402
    QualityFlag,
    detect_session_gaps,
    detect_suspected_splits,
)
from market_calendar import covered_years, is_trading_day, load_holidays  # noqa: E402


# ---------------------------------------------------------------------------
# Pure core
# ---------------------------------------------------------------------------


def expected_trading_days(
    from_date: date,
    to_date: date,
    holidays: frozenset[date],
    known_years: frozenset[int],
) -> list[date]:
    """Trading days in [from_date, to_date], for covered years only.

    Restricting to `known_years` is load-bearing. The holiday file currently
    declares 2026 alone, while stored candles reach back to 2024. Scanning an
    uncovered year would treat every real holiday in it as a missing session
    and emit hundreds of false gaps — which buries the true findings and
    trains you to ignore the report. A year we cannot judge is skipped and
    said out loud, never guessed at.
    """
    days: list[date] = []
    day = from_date
    while day <= to_date:
        if day.year in known_years and is_trading_day(day, holidays):
            days.append(day)
        day += timedelta(days=1)
    return days


def correction_covers(
    adjustments: Sequence[Any], previous_day: date, day: date
) -> bool:
    """Whether a stored correction spans this break, so read-time fixes it.

    A split in the RAW candles is a true finding and stays reported - nothing
    rewrites the stored feed. But whether it still reaches a backtest is the
    thing you actually need to know, and those are different questions.

    "Covers" means the two sides of the break fall in different correction
    periods, or one side is inside a period and the other is not. Both sides
    inside the SAME period means the correction rescales them together and
    the break survives - which would be a correction that does not work.
    """
    before = next(
        (a for a in adjustments if a.effective_from <= previous_day <= a.effective_to),
        None,
    )
    after = next(
        (a for a in adjustments if a.effective_from <= day <= a.effective_to), None
    )
    return before is not after


def scan_instrument(
    intraday: pd.DataFrame,
    adjusted_daily: pd.DataFrame,
    expected_days: Sequence[date],
) -> list[QualityFlag]:
    """Every finding for one instrument, in one list."""
    flags: list[QualityFlag] = []
    if not intraday.empty:
        flags.extend(detect_suspected_splits(intraday, adjusted_daily))
    flags.extend(detect_session_gaps(intraday, expected_days))
    return flags


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit stored candles for splits and missing sessions.",
    )
    parser.add_argument(
        "--symbols",
        help="comma-separated symbols to audit (default: every symbol with "
             "stored coverage)",
    )
    parser.add_argument(
        "--timeframe", default="5m",
        help="stored timeframe to audit (default 5m)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="report findings without writing flags",
    )
    parser.add_argument(
        "--limit", type=int,
        help="audit at most this many symbols (useful for a first look)",
    )
    return parser


def _covered_instrument_ids(client: Any, timeframe: str) -> list[int]:
    """Instrument ids that have a coverage row for `timeframe`.

    Coverage always lives in Supabase even when candles are stored as
    Parquet, so this single query is valid for either backend.
    """
    resp = (
        client.table("candle_coverage")
        .select("instrument_id")
        .eq("timeframe", timeframe)
        .execute()
    )
    return [int(row["instrument_id"]) for row in (resp.data or [])]


def _span(coverage: Any) -> tuple[datetime, datetime] | None:
    """The stored window for an instrument, or None if nothing is cached."""
    if coverage is None:
        return None
    return coverage.first_ts, coverage.last_ts


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    args = build_parser().parse_args(argv)

    try:
        from config import get_settings
        from db import SupabaseStore
        from dhan_factory import create_candle_backend
        from supabase_candle_backend import SupabaseCandleBackend

        settings = get_settings()
        store = SupabaseStore.connect(settings)
        # Candles come from whichever backend is configured (Supabase or
        # Parquet); the symbol master is only ever in Supabase, and the
        # Parquet backend implements the candle protocol alone, so the
        # lookup is asked of Supabase directly rather than through it.
        backend = create_candle_backend(store._client)
        symbol_index = SupabaseCandleBackend(store._client)
    except Exception as exc:
        print(f"SETUP PROBLEM: {exc}", file=sys.stderr)
        return 1

    holidays = load_holidays()
    known_years = covered_years()
    if not known_years:
        print(
            "WARNING: no holiday calendar found, so missing sessions cannot "
            "be judged. Split detection still runs.",
            file=sys.stderr,
        )

    known = symbol_index.known_symbols()
    if args.symbols:
        wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        missing = [s for s in wanted if s not in known]
        if missing:
            print(
                f"ERROR: not in the instruments table: {', '.join(missing)}",
                file=sys.stderr,
            )
            return 1
        symbols = wanted
    else:
        # Only symbols that actually have candles stored. The instruments
        # table holds ~5,200 rows of which 50 are backfilled; asking the
        # coverage table which ones costs a single query, while probing each
        # symbol in turn costs 5,200 round trips to learn the same thing.
        by_id = {v: k for k, v in known.items()}
        symbols = sorted(
            by_id[i]
            for i in _covered_instrument_ids(store._client, args.timeframe)
            if i in by_id
        )
        if not symbols:
            print(
                f"No instrument has stored {args.timeframe} candles yet. "
                "Run backfill.py first.",
                file=sys.stderr,
            )
            return 1

    print(
        f"Auditing {args.timeframe} candles for {len(symbols)} symbol(s)"
        + (f"; holiday calendar covers {sorted(known_years)}"
           if known_years else "")
        + ".\n"
    )

    audited = 0
    split_count = 0
    gap_count = 0
    per_symbol: list[tuple[str, int, int]] = []
    gap_dates: set[str] = set()
    confirmed = 0
    covered = 0
    uncovered = 0
    unchecked = 0

    for symbol in symbols:
        if args.limit is not None and audited >= args.limit:
            break
        instrument_id = known[symbol]
        span = _span(backend.read_coverage(instrument_id, args.timeframe))
        if span is None:
            continue  # nothing stored for this symbol: not a finding
        audited += 1
        from_utc, to_utc = span

        intraday = backend.read_candles(
            instrument_id, args.timeframe, from_utc, to_utc
        )
        if intraday is None:
            intraday = pd.DataFrame()

        # A daily candle is stamped at the session close, after the last
        # intraday candle of the same day, so the window is widened a day at
        # each end or the final session could never be corroborated.
        margin = timedelta(days=1)
        adjusted_daily = backend.read_candles(
            instrument_id, "day", from_utc - margin, to_utc + margin
        )
        if adjusted_daily is None:
            adjusted_daily = pd.DataFrame()

        expected = expected_trading_days(
            from_utc.astimezone(IST).date(),
            to_utc.astimezone(IST).date(),
            holidays,
            known_years,
        )
        # Read-time corrections (sql/010). The raw candles below still carry
        # every unadjusted split - that is deliberate - so each finding is
        # cross-referenced against these to say whether it still reaches a
        # backtest.
        reader = getattr(backend, "read_price_adjustments", None)
        adjustments = reader(instrument_id, args.timeframe) if reader else []

        flags = scan_instrument(intraday, adjusted_daily, expected)
        if not flags:
            continue

        splits = [f for f in flags if f.flag_type == "suspected_split"]
        gaps = [f for f in flags if f.flag_type == "session_gap"]
        split_count += len(splits)
        gap_count += len(gaps)
        per_symbol.append((symbol, len(splits), len(gaps)))

        print(f"  {symbol:<18} splits={len(splits):<4} gaps={len(gaps)}")
        for flag in splits:
            detail = flag.detail
            print(
                f"      SPLIT  {detail.get('previous_date')} -> "
                f"{flag.ts.astimezone(IST).date()}  "
                f"ratio={detail.get('ratio')}  "
                f"{detail.get('previous_close')} -> {detail.get('close')}"
            )
            # Whether the adjusted feed was consulted decides what this
            # finding actually means, so it is said rather than implied.
            if detail.get("adjusted_daily_checked"):
                confirmed += 1
                print(
                    f"             adjusted daily moved only "
                    f"{detail.get('adjusted_ratio')}x over the same two "
                    "sessions -> CONFIRMED unadjusted corporate action"
                )
                previous_day = date.fromisoformat(str(detail.get("previous_date")))
                if correction_covers(
                    adjustments, previous_day, flag.ts.astimezone(IST).date()
                ):
                    covered += 1
                    print(
                        "             CORRECTED ON READ - a stored price "
                        "adjustment spans this break, so backtests do not "
                        "see it. The raw candles are left as the feed sent "
                        "them."
                    )
                else:
                    uncovered += 1
                    print(
                        "             NOT CORRECTED - backtests spanning "
                        "this date see the fake move. Run "
                        "scripts/detect_adjustments.py."
                    )
            else:
                unchecked += 1
                print(
                    "             no adjusted daily candles for these dates "
                    "-> UNCHECKED, could be a real move. Backfill "
                    "--timeframe day for this symbol to decide."
                )
        if gaps:
            missing = sorted(f.detail["missing_date"] for f in gaps)
            shown = ", ".join(missing[:8])
            more = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
            print(f"      GAPS   {shown}{more}")
            gap_dates.update(missing)

        if not args.dry_run:
            backend.write_quality_flags(
                [f.to_row(instrument_id, args.timeframe) for f in flags]
            )

    print(f"\nAudited {audited} symbol(s) with stored {args.timeframe} candles.")
    if not per_symbol:
        print(
            "No splits and no missing sessions found"
            + (f" in {sorted(known_years)}" if known_years else "")
            + ". The stored history is clean by these checks."
        )
        return 0

    print(
        f"FOUND: {split_count} suspected split(s), {gap_count} missing "
        f"session(s), across {len(per_symbol)} symbol(s)."
    )
    if split_count:
        print(
            f"       of those splits: {confirmed} CONFIRMED against the "
            f"adjusted daily feed, {unchecked} UNCHECKED (no daily candles)."
        )
        print(
            f"       of those confirmed: {covered} CORRECTED ON READ, "
            f"{uncovered} still reaching backtests."
        )
    if gap_dates and len(gap_dates) * len(per_symbol) >= gap_count:
        # The same dates missing for EVERY symbol is not 50 data problems.
        # It is one: either those sessions were never fetched, or they were
        # NSE holidays the calendar does not list.
        print(
            f"\nEvery affected symbol is missing the same "
            f"{len(gap_dates)} date(s): {', '.join(sorted(gap_dates))}\n"
            "A gap common to all symbols is one systematic cause, not fifty "
            "per-stock ones — check these against the NSE holiday circular "
            "before re-fetching: an unlisted holiday belongs in "
            "nse_holidays.yaml, not in a backfill."
        )
    if args.dry_run:
        print("Dry run: nothing was written.")
    else:
        print("Flags written to data_quality_flags for review.")
    # The raw candles are never rewritten, so a split found here is not
    # itself a problem - a split found here with no correction over it is.
    if uncovered or unchecked:
        print(
            "\nNothing was changed. Review each UNCORRECTED split before "
            "trusting a backtest that spans it: an unadjusted split is a fake "
            "signal, and a strategy will happily 'discover' it. "
            "scripts/detect_adjustments.py measures and stores the fix."
        )
        return 1

    print(
        "\nEvery confirmed split is corrected on read, so backtests do not "
        "see them. The raw candles still contain the breaks by design - the "
        "correction lives in price_adjustments, where it can be inspected "
        "and undone."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
