"""One real backtest, worked by hand and frozen (design §13): NIFTY weekly short straddle,
Feb-Mar 2023, from the real NSE rows in tests/fixtures/fno_golden/.

The first trade, by hand from daily.parquet and charges.yaml:
  decided 1 Feb 2023 (Budget day) at the close: nearest weekly 2 Feb, which has no
  future, so the forward is put-call parity where call and put are closest - 17,600
  (93.75 vs 75.85): F = 17,600 + e^(0.068/365) x 17.90 = 17,617.9 -> ATM 17,600.
  filled 2 Feb at the open: CE 44.50 - 0.2225 = 44.2775; PE 101.00 - 0.505 = 100.495.
  lot 50; sets 4 = floor(500,000 / margin 101,544); 200 units.
  premium (44.2775 + 100.495) x 200 = 28,954.50.
  settled 2 Feb at NIFTY's close 17,610.40: the CE pays 10.40 x 200 = 2,080; the PE 0.
  charges (2023: STT 0.05% sold premium, exchange 0.05%, SEBI 0.0001%, GST 18%,
  Rs 20 an order): CE 33.2630 + PE 45.5316 = 78.7946.
  P&L = 28,954.50 - 2,080 - 78.79 = 26,795.71.

NSE's file on an expiry day (2023) shows the option's settlement as the index level,
17,610.40; in 2019 it showed 0. The engine reads neither: the pay-out comes from
the expiring future or the index close (MarketData.final_settlement).
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import atlas  # noqa: E402
from fno.contracts import LotSizes  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.events import Events  # noqa: E402
from fno.market import MarketData  # noqa: E402
from fno.strategy import parse  # noqa: E402

HERE = Path(__file__).resolve().parent / "fixtures" / "fno_golden"


def golden():
    market = MarketData.from_frames(
        "NIFTY", daily=pd.read_parquet(HERE / "daily.parquet"),
        facts=pd.read_parquet(HERE / "facts.parquet"), spot=pd.read_parquet(HERE / "spot.parquet"),
        lots=LotSizes({"NIFTY": ((date(2016, 1, 1), 50),)}), events=Events(()))
    strategy = parse(atlas.document("short-straddle", "NIFTY"))
    return run(strategy, market, start=date(2023, 2, 1), end=date(2023, 3, 31))


FROZEN_TRADES = [
    (date(2023, 2, 1), date(2023, 2, 2), date(2023, 2, 2), "expiry"),
    (date(2023, 2, 2), date(2023, 2, 3), date(2023, 2, 9), "expiry"),
    (date(2023, 2, 9), date(2023, 2, 10), date(2023, 2, 16), "expiry"),
    (date(2023, 2, 16), date(2023, 2, 17), date(2023, 2, 23), "stop"),
    (date(2023, 2, 23), date(2023, 2, 24), date(2023, 3, 2), "expiry"),
    (date(2023, 3, 2), date(2023, 3, 3), date(2023, 3, 9), "stop"),
    (date(2023, 3, 9), date(2023, 3, 10), date(2023, 3, 14), "stop"),
    (date(2023, 3, 14), date(2023, 3, 15), date(2023, 3, 16), "expiry"),
    (date(2023, 3, 16), date(2023, 3, 17), date(2023, 3, 23), "expiry"),
    (date(2023, 3, 23), date(2023, 3, 24), date(2023, 3, 29), "expiry"),   # 30 Mar holiday: moved
    (date(2023, 3, 29), date(2023, 3, 31), None, "open at end"),
]
FROZEN_PNL = [26795.71, 10341.53, 20621.7, -89662.61, -294.85, -57068.66, -58941.88,
              21295.47, 48769.72, 41425.23, -25430.93]
FROZEN_END = 437850.44


def test_the_first_trade_matches_the_hand_working():
    t = golden().trades[0]
    assert [leg.entry_price for leg in t.legs] == pytest.approx([44.2775, 100.495])
    assert (t.sets, t.lot_size, [leg.strike for leg in t.legs]) == (4, 50, [17600.0, 17600.0])
    assert t.charges == pytest.approx(78.7946, abs=1e-4)
    assert t.pnl == pytest.approx(28954.50 - 2080.0 - 78.7946, abs=0.01)


def test_the_golden_run_is_unchanged():
    r = golden()
    assert [(t.decided, t.entered, t.exited, t.exit_reason) for t in r.trades] == FROZEN_TRADES
    assert [round(t.pnl, 2) for t in r.trades] == pytest.approx(FROZEN_PNL, abs=0.01)
    assert round(float(r.equity.iloc[-1]), 2) == pytest.approx(FROZEN_END, abs=0.01)
