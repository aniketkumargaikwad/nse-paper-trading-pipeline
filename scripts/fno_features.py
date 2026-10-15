"""Build data/fno/features/<UNDERLYING>.parquet from the local F&O store.

    python scripts/fno_features.py                 # every underlying, all history
    python scripts/fno_features.py --only NIFTY
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from config import use_utf8_stdout  # noqa: E402
from fno import chain, features, participants, spot, store, vix  # noqa: E402
from fno.contracts import INDEX_UNDERLYINGS  # noqa: E402
from fno.events import Events  # noqa: E402
from fno.rates import risk_free  # noqa: E402
from fno.yahoo import write_whole  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=INDEX_UNDERLYINGS)
    args = parser.parse_args(argv)
    root = store.root_dir()
    vix_close = dict(zip(*(vix.read(root)[c] for c in ("trade_date", "close"))))
    fii = participants.read(root, date(2016, 1, 1), date.today())
    fii = fii[fii["client_type"] == "FII"]
    fii_net = dict(zip(fii["trade_date"], fii["future_index_long"] - fii["future_index_short"]))
    events = Events.load()

    for underlying in [args.only] if args.only else INDEX_UNDERLYINGS:
        if underlying in spot.YAHOO_SYMBOLS:
            spot.refresh(root, underlying)
        closes = spot.closes(root, underlying)
        facts = []
        for year in range(2016, date.today().year + 1):
            rows = store.read_daily(root, underlying, date(year, 1, 1), date(year, 12, 31))
            for day, day_rows in rows.groupby("trade_date"):
                later = [e for e in chain.expiries(day_rows, day) if e > day]
                if not later:
                    continue
                s = closes.get(day, day_rows["underlying_price"].dropna().iloc[0]
                               if day_rows["underlying_price"].notna().any() else float("nan"))
                snap = chain.snapshot(day_rows, underlying, day, later[0], spot=s, rate=risk_free(day))
                row = features.from_snapshot(snap)
                row["spot_source"] = ("yahoo" if day in closes
                                      else "nse" if s == s else "none")
                facts.append(row)
        table = features.daily_table(facts, vix=vix_close, events_on=events.on, fii_net=fii_net)
        write_whole(root / "features" / f"{underlying}.parquet", table)
        print(f"{underlying}: {len(table)} days, {table['trade_date'].min()} -> {table['trade_date'].max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
