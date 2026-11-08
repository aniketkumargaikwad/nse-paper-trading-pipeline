"""Update every paper book (research/paper_books.py). NO real money.

    python scripts/paper_books.py                    # download prices, rebuild every book
    python scripts/paper_books.py --skip-download    # use the stored prices
    python scripts/paper_books.py --notify           # ...and message on a new month's first session

Writes research/journal/paper/<book>.json, which the dashboard's Paper books
page reads. Run every weekday evening by .github/workflows/paper-books.yml.
The options book needs the F&O store (data/fno, pulled from R2 by the job).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from config import get_settings, use_utf8_stdout  # noqa: E402
from research import paper_books as pb  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--notify", action="store_true")
    args = parser.parse_args(argv)
    from rotation_prices import closes_of
    ohlcv = _load(args)
    closes = closes_of(ohlcv)
    from costs import HoldingCostModel
    from fno import store
    from nse_index_history import fetch as fetch_nse
    indexes = {b.index for b in pb.BOOKS if b.kind == "index"}
    tri = (fetch_nse(pb.START.year - 1, {i: i for i in [*indexes, "NIFTY 50"]}, log=lambda *a, **k: None)
           if indexes else None)
    books, failed = [], False
    for book in pb.BOOKS:
        try:
            if book.kind == "stock":
                b = pb.stock_book(book, ohlcv, slippage_pct=get_settings(require_supabase=False).slippage_pct, cost_model=HoldingCostModel())
            elif book.kind == "rotation":
                b = pb.rotation_book(book, closes)
            elif book.kind == "index":
                b = pb.index_book(book, tri)
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
    # Once a month: the first run whose prices reach a new month sends the summary, and
    # MONTHLY_MARK remembers it (committed with the books). It replaced a "first session of
    # the month" test that read Yahoo's NIFTY 50, which lags a day - so on 2 Nov 2026 the
    # job saw October and sent nothing.
    latest = max(closes.index)
    month = f"{latest:%Y-%m}"
    mark = pb.OUT / MONTHLY_MARK
    sent_for = mark.read_text(encoding="utf-8").strip() if mark.exists() else ""
    if args.notify and books and month != sent_for:
        from research.blocks import send
        momentum = Path("research/journal/momentum/paper.json")
        if momentum.exists():
            books = [pb.from_momentum(json.loads(momentum.read_text(encoding="utf-8"))), *books]
        if send(f"Paper books {latest}", pb.message(books, latest), warn_token=False):
            mark.write_text(month + "\n", encoding="utf-8")
    return 1 if failed else 0


MONTHLY_MARK = "_monthly_message.txt"


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
