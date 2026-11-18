"""Update every paper book (research/paper_books.py). NO real money.

    python scripts/paper_books.py                    # download prices, rebuild every book
    python scripts/paper_books.py --skip-download    # use the stored prices
    python scripts/paper_books.py --notify           # ...and send the weekly message on Fridays
    python scripts/paper_books.py --weekly-now       # ...send the weekly message now, any day

Writes research/journal/paper/<book>.json, which the dashboard's Paper books
page reads. Run every weekday evening by .github/workflows/paper-books.yml.
The options book needs the F&O store (data/fno, pulled from R2 by the job).

Yahoo's prices are checked against NSE's official end-of-day files for every
session since the books began, and any price Yahoo has not published yet is
filled from NSE (research/nse_prices.py) - so a book is complete through the
day it runs, not a day behind.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from config import IST, get_settings, use_utf8_stdout  # noqa: E402
from research import paper_books as pb  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--notify", action="store_true")
    parser.add_argument("--weekly-now", action="store_true", help="send the weekly message whatever the day")
    parser.add_argument("--today", default="", help=argparse.SUPPRESS)        # tests only
    parser.add_argument("--no-nse-check", action="store_true", help="skip NSE's official price files")
    args = parser.parse_args(argv)
    from rotation_prices import closes_of
    ohlcv = _load(args)
    # A run started by hand while the market is open gets today's unfinished candle from
    # Yahoo (18 Nov 2026, 12:56 IST). Only finished sessions count: before 16:00 IST, today
    # is left out.
    now = datetime.now(IST)
    if now.time() < time(16, 0) and not args.today:
        ohlcv = ohlcv[[d < now.date() for d in ohlcv.index]]
    check = None
    if not args.no_nse_check:
        ohlcv, check = _check_with_nse(ohlcv)
    closes = closes_of(ohlcv)
    from costs import HoldingCostModel
    from fno import store
    from nse_index_history import fetch_whole
    all_books = pb.all_books()
    # Two years before the first book: an allocation plan ranks on up to 12 months and
    # reads 250-session averages, and both must exist on its first day.
    tri = (fetch_whole(pb.indexes_needed(all_books), pb.START.year - 2, log=lambda *a, **k: None)
           if any(b.kind in ("index", "alloc") for b in all_books) else None)
    books, failed = [], False
    for book in all_books:
        try:
            if book.kind == "stock":
                b = pb.stock_book(book, ohlcv, slippage_pct=get_settings(require_supabase=False).slippage_pct, cost_model=HoldingCostModel())
            elif book.kind == "rotation":
                b = pb.rotation_book(book, closes)
            elif book.kind == "index":
                b = pb.index_book(book, tri)
            elif book.kind == "alloc":
                b = pb.alloc_book(book, tri)
            else:
                b = pb.options_book(book, store.root_dir())
        except Exception as exc:        # noqa: BLE001 - one book's failure must not stop the rest
            print(f"{book.key}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = True
            continue
        path = pb.OUT / f"{book.key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((json.dumps(b, indent=1, default=str) + "\n").encode("utf-8"))
        books.append(b)
        print(pb.summary_line(b))
    if check is not None:
        (pb.OUT / PRICE_CHECK).write_bytes((json.dumps(check.as_dict(), indent=1) + "\n").encode("utf-8"))
    # Once a week, on Friday's run (the owner's choice, 18 Nov 2026; it replaced a monthly
    # summary). WEEKLY_MARK remembers the week it went out, committed with the books, so a
    # second run on the same Friday does not send it twice - and a failed delivery is not
    # remembered, so a re-run sends it.
    from research import paper_weekly as pw
    today = date.fromisoformat(args.today) if args.today else datetime.now(IST).date()
    mark = pb.OUT / WEEKLY_MARK
    sent_for = mark.read_text(encoding="utf-8").strip() if mark.exists() else ""
    if books and (args.weekly_now or (args.notify and pw.due(today, sent_for))):
        from research.blocks import send
        momentum = Path("research/journal/momentum/paper.json")
        if momentum.exists():
            books = [pb.from_momentum(json.loads(momentum.read_text(encoding="utf-8"))), *books]
        latest = max(closes.index)
        blocks = pw.message(books, latest, price_check=check.line() if check is not None else "")
        if send(f"Paper trading, week to {latest}", blocks, warn_token=False, split=True):
            mark.write_text(pw.week_of(today) + "\n", encoding="utf-8")
    return 1 if failed else 0


WEEKLY_MARK = "_weekly_message.txt"
PRICE_CHECK = "_price_check.json"


def _check_with_nse(ohlcv: pd.DataFrame) -> tuple[pd.DataFrame, object]:
    """Yahoo's prices completed from NSE's official files for every session since the books began."""
    from research import nse_prices
    first = min(b.start for b in pb.all_books()) - timedelta(days=7)
    found, missing = nse_prices.fetch(nse_prices.weekdays(first, date.today()))
    repaired, check = nse_prices.repair(ohlcv, found, no_file=missing)
    print(check.line())
    for t, d, apart in check.disagree:
        print(f"  {t} {d}: Yahoo and NSE are {apart:+.2f}% apart (kept Yahoo's)")
    if check.filled:
        print(f"  filled from NSE: {', '.join(f'{t} {d}' for t, d in check.filled[:20])}"
              + (" ..." if len(check.filled) > 20 else ""))
    return repaired, check


def _load(args: argparse.Namespace) -> pd.DataFrame:
    """Daily prices: the stored file with --skip-download, else a fresh Yahoo download (stored)."""
    from rotation_prices import OHLCV, OUT as CLOSES, closes_of, download_ohlcv
    if args.skip_download and OHLCV.exists():
        return pd.read_parquet(OHLCV)
    ohlcv = download_ohlcv()
    OHLCV.parent.mkdir(parents=True, exist_ok=True)
    ohlcv.to_parquet(OHLCV)
    closes_of(ohlcv).to_parquet(CLOSES)
    return ohlcv


if __name__ == "__main__":
    raise SystemExit(main())
