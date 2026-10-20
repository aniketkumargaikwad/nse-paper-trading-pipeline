"""Measure NSE's factor indexes the way momentum was measured (docs/research/2026-10-21-factor-indexes.md).

    python scripts/factor_indexes.py

NSE total-return levels (dividends in, each date's real constituents), held,
and held under the NIFTY 50 200-day cash rule (cash at 6%, 0.2% a switch).
Training Jan 2009 - 17 Oct 2025; exam the year after. Nothing is tuned.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from nse_index_history import fetch  # noqa: E402
from research.rotation import index_switch, stats  # noqa: E402

INDEXES = ["NIFTY MIDCAP150 MOMENTUM 50", "NIFTY500 MOMENTUM 50", "NIFTY200 MOMENTUM 30", "NIFTY200 ALPHA 30",
           "NIFTY200 QUALITY 30", "NIFTY100 LOW VOLATILITY 30", "NIFTY200 VALUE 30", "NIFTY ALPHA LOW-VOLATILITY 30",
           "NIFTY 200", "NIFTY 50"]
WINDOWS = {"2009-Oct 2025": (date(2009, 1, 1), date(2025, 10, 17)),
           "2021-Oct 2025": (date(2021, 1, 1), date(2025, 10, 17)),
           "exam year": (date(2025, 10, 20), date(2026, 10, 19))}


def main() -> int:
    t = fetch(2005, {i: i for i in INDEXES}, log=lambda *a, **k: None)
    n50 = t["NIFTY 50"]
    print("| index | rule | " + " | ".join(f"{w} a year (worst fall)" for w in WINDOWS) + " | luck t (2009-2025) |")
    print("|---|---|" + "---:|" * (len(WINDOWS) + 1))
    for col in INDEXES:
        s = t[col].dropna()
        for rule in ("hold", "cash rule"):
            cells, luck = [], None
            for w, (a, b) in WINDOWS.items():
                sig = s * 0 + 1 if rule == "hold" else n50
                r = index_switch(s, sig, start=max(a, s.index[0]), end=b, cash_yield=0.0 if rule == "hold" else 0.06,
                                 cost_pct=0.0 if rule == "hold" else 0.2)
                st = stats(r, n50)
                cells.append(f"{st['cagr_pct']:+.1f}% ({st['worst_fall_pct']:.0f}%)")
                if w == "2009-Oct 2025":
                    luck = st["luck_t"]
            print(f"| {col} | {rule} | " + " | ".join(cells) + f" | {luck} |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
