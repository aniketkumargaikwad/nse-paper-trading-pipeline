# F&O Step 3b — Intraday Options Backtests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Backtest intraday NIFTY / BANKNIFTY option strategies minute by minute on the real Upstox option minutes (from Oct 2024) and the Dhan index / India VIX minutes, judge them by the options pass rule (12-month minimum for intraday), and measure the intraday atlas.

**Architecture:** `fno/intraday_market.py` wraps the daily `MarketData` and adds the minutes, with the chain at any minute. `fno/intraday.py` runs a strategy minute by minute and returns the same `BacktestResult` the daily engine does, so `fno/report.py` reads it unchanged. `fno/rules.py` is the pass rule. The atlas and its script grow an intraday half.

**Tech Stack:** Python 3.11, pandas, numpy, pytest. Reuses `fno.engine` (Trade, TradeLeg, Skip, BacktestResult, `_within_limits`), `fno.fills`, `fno.costs`, `fno.margin`, `fno.account`, `fno.strikes.pick_strikes`, `expr_eval.evaluate`.

**Spec:** `docs/superpowers/specs/2026-10-19-fno-intraday-design.md`

**Conventions:** `.venv/Scripts/python.exe -m pytest ...`; LF line endings; helpers in `tests/` imported by module name.

**Decisions made while planning:**
- The minute clock is the index's own minutes for the day (09:15-15:29). Option candles are looked up at those stamps.
- Timestamps are compared as pandas tz-aware Timestamps; dictionaries key on `Timestamp.value` (UTC nanoseconds) so two zoneinfo objects for IST cannot split a key.
- `decided_time`, `entry_time` and `exit_time` (HH:MM) go in `Trade.facts`, beside the split keys (`vix`, `iv_rank`, `event_today`, `dte`, `trend`).
- Daily facts with non-numeric values (`expiry`, `spot_source`) are not offered to expressions.
- The opening range is 09:15-09:44 inclusive; `or_high` / `or_low` exist from the 09:45 minute on.
- The intraday atlas starts on the first day with option minutes (from the minute ledger), not 2016, so empty months do not lengthen its history.

---

## File structure

| File | Responsibility |
|---|---|
| `fno/strategy.py` | + `day_open`, `or_high`, `or_low` as fact names |
| `fno/intraday_market.py` | Daily market + option / index / VIX minutes; previous session; chain at a minute |
| `fno/intraday.py` | The intraday engine |
| `fno/rules.py` | The options pass rule, minimum months by mode |
| `fno/atlas.py` | + the intraday atlas |
| `scripts/fno_atlas.py` | `--intraday`; a "passes?" column for both modes |
| `tests/fno_intraday_synthetic.py` | A 2-day hand-checkable minute market |
| `tests/test_fno_intraday_market.py`, `test_fno_intraday.py`, `test_fno_rules.py` | Tests |

---

### Task 1: Opening-range names in the strategy format

- [ ] **Step 1:** append to `tests/test_fno_strategy.py`:

```python
def test_the_day_so_far_can_be_named_in_an_intraday_strategy():
    s = parse(SPEC_EXAMPLE.replace("dte <= 4", "close > or_high and or_low > 0 and day_open > 0"))
    assert s.entry_when is not None
```

- [ ] **Step 2:** run — fails with `unknown name 'or_high'`.
- [ ] **Step 3:** in `fno/strategy.py`, extend `FACT_NAMES` with `"day_open", "or_high", "or_low"` and add to the comment above it: "and three the intraday engine makes from the day so far (fno.intraday): the index's 09:15 open and its 09:15-09:44 high and low, known from 09:45."
- [ ] **Step 4:** run — passes. **Commit** `feat(fno): name the day's open and opening range in a strategy`.

---

### Task 2: The intraday market

**Files:** Create `fno/intraday_market.py`, `tests/fno_intraday_synthetic.py`, `tests/test_fno_intraday_market.py`

`tests/fno_intraday_synthetic.py`:

```python
"""A small, hand-checkable intraday option market for the intraday engine's tests.

NIFTY on Wed 1 Jan 2025 (P) and Thu 2 Jan 2025 (D). D is an expiry day: the
nearest weekly (E1) expires on D, so a "weekly, which 0" strategy trades a
0 DTE contract; the next weekly (E2) is 9 Jan. Strikes 23,300-23,700 every 100,
plus 23,000 and 24,000 for wings. Every option minute is Black-76 at 15% on a
flat forward of 23,500, time measured to 15:30 on the expiry; every candle is
flat (open = high = low = close) and trades 100 contracts (7,500 units at lot
75) - unless overridden. The index sits at 23,500 all day; India VIX at 14.
"""

from __future__ import annotations

from datetime import date, datetime, time

import numpy as np
import pandas as pd

from config import IST
from fno import pricing
from fno.contracts import LotSizes
from fno.events import Events
from fno.intraday_market import IntradayMarket
from fno.market import MarketData
from fno.minute import COLUMNS as MINUTE_COLUMNS
from fno.nse_files import COLUMNS, DTYPES

P, D = date(2025, 1, 1), date(2025, 1, 2)
E1, E2 = D, date(2025, 1, 9)
STRIKES = (23000.0, 23300.0, 23400.0, 23500.0, 23600.0, 23700.0, 24000.0)
FORWARD, SIGMA, RATE, LOT = 23500.0, 0.15, 0.058, 75
UNITS = 7500                                   # 100 contracts a minute


def stamp(day: date, hhmm: str) -> pd.Timestamp:
    h, m = map(int, hhmm.split(":"))
    return pd.Timestamp(datetime.combine(day, time(h, m))).tz_localize(IST)


def session(day: date) -> list[pd.Timestamp]:
    return [stamp(day, "09:15") + pd.Timedelta(minutes=i) for i in range(375)]


def _years(t: pd.Timestamp, expiry: date) -> float:
    close = pd.Timestamp(datetime.combine(expiry, time(15, 30))).tz_localize(IST)
    return max((close - t).total_seconds(), 60.0) / (365 * 86400)


def model(t: pd.Timestamp, expiry: date, strike: float, typ: str) -> float:
    return round(float(pricing.black76(FORWARD, strike, _years(t, expiry), SIGMA, RATE, typ == "CE")), 2)


def options(expiry: date, overrides: dict | None = None) -> pd.DataFrame:
    """Every minute of every contract; `overrides` maps (day, "HH:MM", strike, type) to values."""
    stamps, strikes, types, years = [], [], [], []
    for day in (P, D):
        if day > expiry:
            continue
        for t in session(day):
            for k in STRIKES:
                for typ in ("CE", "PE"):
                    stamps.append(t)
                    strikes.append(k)
                    types.append(typ)
                    years.append(_years(t, expiry))
    price = np.round(pricing.black76(FORWARD, np.array(strikes), np.array(years), SIGMA, RATE,
                                     np.array(types) == "CE"), 2)
    frame = pd.DataFrame({"ts": stamps, "strike": strikes, "option_type": types, "open": price,
                          "high": price, "low": price, "close": price, "volume": UNITS, "oi": 1_000_000})
    frame = frame[list(MINUTE_COLUMNS)]
    for (day, hhmm, k, typ), values in (overrides or {}).items():
        at = (frame["ts"] == stamp(day, hhmm)) & (frame["strike"] == k) & (frame["option_type"] == typ)
        assert at.sum() == 1, (day, hhmm, k, typ)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame


def index(overrides: dict | None = None) -> pd.DataFrame:
    """The index's minutes, flat at 23,500; `overrides` maps (day, "HH:MM") to values."""
    stamps = session(P) + session(D)
    frame = pd.DataFrame({"ts": stamps, "open": FORWARD, "high": FORWARD, "low": FORWARD,
                          "close": FORWARD, "volume": 0.0})
    for (day, hhmm), values in (overrides or {}).items():
        at = frame["ts"] == stamp(day, hhmm)
        for col, value in values.items():
            frame.loc[at, col] = value
    return frame


def vix() -> pd.DataFrame:
    stamps = session(P) + session(D)
    return pd.DataFrame({"ts": stamps, "open": 14.0, "high": 14.0, "low": 14.0, "close": 14.0, "volume": 0.0})


def daily_rows() -> pd.DataFrame:
    """NSE's file for P and D: the options of both expiries, at the day's last minute."""
    rows = []
    for day in (P, D):
        last = stamp(day, "15:29")
        for expiry in (E1, E2):
            if expiry < day:
                continue
            for k in STRIKES:
                for typ in ("CE", "PE"):
                    p = model(last, expiry, k, typ)
                    rows.append({"trade_date": day, "underlying": "NIFTY", "instrument": "OPT",
                                 "expiry": expiry, "strike": k, "option_type": typ, "open": p,
                                 "high": p, "low": p, "close": p, "settle": p, "volume_contracts": 1000,
                                 "oi_units": 100_000, "oi_change_units": 0, "turnover_inr": 0.0,
                                 "lot_size": float(LOT), "underlying_price": FORWARD})
    return pd.DataFrame(rows, columns=list(COLUMNS)).astype(DTYPES)


def facts(**columns: list) -> pd.DataFrame:
    base = {"trade_date": [P, D], "vix": [14.0, 14.0], "iv_rank": [50.0, 50.0],
            "event_today": [False, False]}
    base.update(columns)
    return pd.DataFrame(base)


def spot() -> pd.DataFrame:
    return pd.DataFrame({"trade_date": [P, D], "open": FORWARD, "high": FORWARD, "low": FORWARD,
                         "close": FORWARD})


def market(*, option_overrides: dict | None = None, index_overrides: dict | None = None,
           facts_frame: pd.DataFrame | None = None, with_options: bool = True) -> IntradayMarket:
    daily = MarketData.from_frames("NIFTY", daily=daily_rows(),
                                   facts=facts() if facts_frame is None else facts_frame, spot=spot(),
                                   lots=LotSizes({"NIFTY": ((date(2016, 1, 1), LOT),)}), events=Events(()))
    books = {E1: options(E1, option_overrides), E2: options(E2)} if with_options else {}
    return IntradayMarket.from_frames(daily, options=books, index=index(index_overrides), vix=vix())
```

`tests/test_fno_intraday_market.py`:

```python
"""The intraday market: minutes, the previous session, the chain at a minute (design 2026-10-19)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno_intraday_synthetic import D, E1, LOT, P, RATE, market, stamp  # noqa: E402


def test_the_previous_session_and_the_first_minute_day():
    m = market()
    assert m.previous_session(D) == P and m.previous_session(P) is None
    assert m.first_day == P


def test_index_bars_carry_the_warm_up_sessions_before_the_day():
    bars = market().index_bars(D)
    assert len(bars) == 750 and bars.index[0] == stamp(P, "09:15") and bars.index[-1] == stamp(D, "15:29")


def test_the_chain_at_a_minute_has_deltas_even_on_expiry_day():
    snap = market().chain(D, E1, stamp(D, "09:20"), rate=RATE, lot=LOT)
    atm = snap.rows[snap.rows["strike"] == 23500.0].set_index("option_type")
    assert snap.dte == 0 and abs(snap.forward - 23500.0) < 1.0
    assert 0.4 < atm.loc["CE", "delta"] < 0.6 and -0.6 < atm.loc["PE", "delta"] < -0.4
    assert (snap.rows["settle"] > 0).all()


def test_the_chain_reads_nothing_after_its_minute():
    planted = {(D, "09:21", 23500.0, "CE"): {"close": 999.0}}
    snap = market(option_overrides=planted).chain(D, E1, stamp(D, "09:20"), rate=RATE, lot=LOT)
    ce = snap.rows[(snap.rows["strike"] == 23500.0) & (snap.rows["option_type"] == "CE")]
    assert float(ce["settle"].iloc[0]) < 100.0
```

`fno/intraday_market.py`:

```python
"""What the intraday engine reads about one underlying (design 2026-10-19 §2, §4).

The daily MarketData supplies the sessions, the previous session's NSE file
(which expiries and strikes exist, and their lot) and facts, the calendar facts
and the index closes. On top of it: the expired options' minutes
(fno.minute) and the index's and India VIX's minutes (fno.index_minutes).
`until` cuts everything after a date out at load, as MarketData does.

The chain at a minute is built from the minutes up to and including it: each
contract's latest close, its volume so far, its open interest, and IV and
delta by Black-76 with time measured to 15:30 on the expiry day - so a 0 DTE
contract still has a delta. The forward is put-call parity where the call and
put are closest, else the index minute.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time
from pathlib import Path

import numpy as np
import pandas as pd

from config import IST
from fno import index_minutes, minute, pricing
from fno.chain import ChainSnapshot
from fno.market import MarketData

WARMUP_SESSIONS = 5
EXPIRY_CLOSE = time(15, 30)
MINUTE_COLUMNS = ["open", "high", "low", "close", "volume"]
YEAR_SECONDS = 365 * 86400
CHAIN_COLUMNS = ["instrument", "expiry", "strike", "option_type", "settle", "oi_units",
                 "volume_contracts"]


def _by_day(frame: pd.DataFrame) -> dict[date, pd.DataFrame]:
    if frame.empty:
        return {}
    indexed = frame.set_index("ts").sort_index()
    return {d: g for d, g in indexed.groupby(indexed.index.date)}


def _parity_forward(rows: pd.DataFrame, years: float, rate: float) -> float:
    calls = rows[rows["option_type"] == "CE"].set_index("strike")["settle"]
    puts = rows[rows["option_type"] == "PE"].set_index("strike")["settle"]
    both = pd.concat([calls.rename("c"), puts.rename("p")], axis=1).dropna()
    both = both[(both["c"] > 0) & (both["p"] > 0)]
    if both.empty:
        return float("nan")
    k = (both["c"] - both["p"]).abs().idxmin()
    return float(k + np.exp(rate * years) * (both.loc[k, "c"] - both.loc[k, "p"]))


class IntradayMarket:
    def __init__(self, daily: MarketData, *, read_options: Callable[[date], pd.DataFrame],
                 index: pd.DataFrame, vix: pd.DataFrame, first_day: date | None,
                 until: date | None = None) -> None:
        self.daily = daily
        self.underlying = daily.underlying
        self.until = until if until is not None else daily.until
        self.first_day = first_day
        self._read_options = read_options
        self._books: dict[date, dict[date, dict[tuple[float, str], pd.DataFrame]]] = {}
        self._index = _by_day(self._cut(index))
        vix = self._cut(vix)
        self._vix = (vix.set_index("ts")["close"].astype(float).sort_index() if len(vix)
                     else pd.Series(dtype=float))

    def _cut(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.until is None or frame.empty:
            return frame
        return frame[frame["ts"].dt.date <= self.until]

    @classmethod
    def from_frames(cls, daily: MarketData, *, options: dict[date, pd.DataFrame], index: pd.DataFrame,
                    vix: pd.DataFrame, until: date | None = None) -> "IntradayMarket":
        first = min((f["ts"].min().date() for f in options.values() if len(f)), default=None)
        empty = pd.DataFrame(columns=list(minute.COLUMNS))
        return cls(daily, read_options=lambda e: options.get(e, empty), index=index, vix=vix,
                   first_day=first, until=until)

    @classmethod
    def load(cls, root: Path, underlying: str, *, until: date | None = None) -> "IntradayMarket":
        root = Path(root)
        daily = MarketData.load(root, underlying, until=until)
        end = until or date.today()
        first = None
        ledger = root / minute.LEDGER
        if ledger.exists():
            rows = pd.read_csv(ledger)
            ok = rows[(rows["underlying"] == underlying) & (rows["status"] == "ok")]
            if len(ok):
                first = min(date.fromisoformat(str(d)) for d in ok["first_day"])
        return cls(daily, read_options=lambda e: minute.read_expiry(root, underlying, e),
                   index=index_minutes.read(root, underlying, index_minutes.ARCHIVE_BEGINS, end),
                   vix=index_minutes.read(root, "INDIAVIX", index_minutes.ARCHIVE_BEGINS, end),
                   first_day=first, until=until)

    # -- the calendar -------------------------------------------------------

    def sessions(self, start: date, end: date) -> list[date]:
        return self.daily.sessions(start, end)

    def previous_session(self, day: date) -> date | None:
        i = self.daily._position.get(day)
        return self.daily.sessions_all[i - 1] if i else None

    # -- minutes --------------------------------------------------------------

    def day_options(self, expiry: date, day: date) -> dict[tuple[float, str], pd.DataFrame]:
        """Each contract's minutes on `day`, indexed by timestamp: {(strike, type): frame}."""
        if expiry not in self._books:
            if len(self._books) >= 2:
                self._books.pop(next(iter(self._books)))
            frame = self._cut(self._read_options(expiry))
            books: dict[date, dict[tuple[float, str], pd.DataFrame]] = {}
            if len(frame):
                frame = frame.sort_values("ts")
                for (d, k, typ), g in frame.groupby([frame["ts"].dt.date, "strike", "option_type"]):
                    books.setdefault(d, {})[(float(k), typ)] = g.set_index("ts")[MINUTE_COLUMNS + ["oi"]]
            self._books[expiry] = books
        return self._books[expiry].get(day, {})

    def index_bars(self, day: date) -> pd.DataFrame:
        """The index's minutes for `day` and the WARMUP_SESSIONS sessions before it."""
        i = self.daily._position.get(day)
        if i is None:
            return pd.DataFrame(columns=MINUTE_COLUMNS)
        days = self.daily.sessions_all[max(0, i - WARMUP_SESSIONS): i + 1]
        frames = [self._index[d] for d in days if d in self._index]
        if not frames:
            return pd.DataFrame(columns=MINUTE_COLUMNS)
        return pd.concat(frames)[MINUTE_COLUMNS].astype(float)

    def vix_on(self, stamps: pd.DatetimeIndex) -> pd.Series:
        """India VIX at each stamp: the last minute's close at or before it."""
        if self._vix.empty:
            return pd.Series(np.nan, index=stamps)
        joined = self._vix.reindex(self._vix.index.union(stamps)).ffill()
        return joined.reindex(stamps)

    def chain(self, day: date, expiry: date, t: pd.Timestamp, *, rate: float, lot: int) -> ChainSnapshot:
        """Every contract of `expiry` that has traded on `day` by minute `t`, as it stood at `t`."""
        rows = []
        for (k, typ), bars in self.day_options(expiry, day).items():
            seen = bars.loc[:t]
            if seen.empty:
                continue
            last = seen.iloc[-1]
            rows.append({"instrument": "OPT", "expiry": expiry, "strike": k, "option_type": typ,
                         "settle": float(last["close"]), "oi_units": int(last["oi"]),
                         "volume_contracts": int(seen["volume"].sum()) // lot})
        frame = pd.DataFrame(rows, columns=CHAIN_COLUMNS)
        bars = self._index.get(day)
        upto = bars.loc[:t] if bars is not None else None
        spot = float(upto["close"].iloc[-1]) if upto is not None and len(upto) else float("nan")
        close = pd.Timestamp(datetime.combine(expiry, EXPIRY_CLOSE)).tz_localize(IST)
        years = max((close - t).total_seconds(), 60.0) / YEAR_SECONDS
        forward = _parity_forward(frame, years, rate)
        if forward != forward:
            forward = spot
        if len(frame):
            K = frame["strike"].to_numpy(dtype=float)
            calls = (frame["option_type"] == "CE").to_numpy()
            F = np.full(len(frame), forward)
            T = np.full(len(frame), years)
            iv = pricing.implied_vol(frame["settle"].to_numpy(dtype=float), F, K, T, rate, calls)
            with np.errstate(divide="ignore", invalid="ignore"):
                delta = pricing.greeks(F, K, T, iv, rate, calls)["delta"]
            frame["iv"] = iv
            frame["delta"] = np.where(np.isfinite(iv), delta, np.nan)
        else:
            frame["iv"] = pd.Series(dtype=float)
            frame["delta"] = pd.Series(dtype=float)
        frame = frame.sort_values(["strike", "option_type"]).reset_index(drop=True)
        return ChainSnapshot(self.underlying, day, expiry, (expiry - day).days, forward, spot, rate, frame)
```

- [ ] **Steps:** write the synthetic market and the tests; run (fail: no module); write the module; run (pass); commit `feat(fno): the intraday market - minutes, the previous session, the chain at a minute`.

---

### Task 3: The intraday engine

**Files:** Create `fno/intraday.py`, `tests/test_fno_intraday.py`

`tests/test_fno_intraday.py`:

```python
"""The intraday options backtester (design 2026-10-19): hand-worked cases on a 2-day minute market."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.costs import Fill, FnoCharges  # noqa: E402
from fno.engine import CAPITAL, NO_TRADE  # noqa: E402
from fno.intraday import NO_MINUTES, run  # noqa: E402
from fno.strategy import StrategyError, parse  # noqa: E402
from fno_intraday_synthetic import D, E1, LOT, P, STRIKES, facts, market  # noqa: E402

CH = FnoCharges.load()
ATM = 23500.0
# Staged round numbers: in at 09:21's open, out at 15:15's open.
FILL = {(D, "09:21", ATM, "CE"): {"open": 40.0, "high": 40.0},
        (D, "09:21", ATM, "PE"): {"open": 38.0, "high": 38.0},
        (D, "15:15", ATM, "CE"): {"open": 10.0},
        (D, "15:15", ATM, "PE"): {"open": 8.0}}
PREMIUM = 39.80 + 37.81


def straddle(window=("09:20", "09:21"), exit_="", when=None, per_day=1):
    condition = f'\n  when: "{when}"' if when else ""
    return parse(f"""
version: options/1
name: test-intraday-straddle
underlying: NIFTY
mode: intraday
expiry: {{cycle: weekly, which: 0}}
entry:
  window: {{from: "{window[0]}", to: "{window[1]}"}}{condition}
legs:
  - {{side: sell, type: CE, strike: {{atm: 0}}, lots: 1}}
  - {{side: sell, type: PE, strike: {{atm: 0}}, lots: 1}}
exit: {{time: "15:15"{exit_}}}
reentry: {{max_per_day: {per_day}}}
""")


def orb():
    return parse("""
version: options/1
name: test-orb
underlying: NIFTY
mode: intraday
expiry: {cycle: weekly, which: 0}
entry: {window: {from: "09:15", to: "13:00"}, when: "close > or_high"}
legs: [{side: buy, type: CE, strike: {atm: 0}, lots: 1}]
exit: {time: "15:15", stop_pct: 30, target_pct: 60}
""")


def test_the_0920_straddle_worked_by_hand():
    """Decided on the finished 09:20 minute: the nearest weekly expires today (0 DTE),
    ATM 23,500. Filled at 09:21's open: CE 40 - 0.20 = 39.80, PE 38 - 0.19 = 37.81.
    Closed at 15:15's open: CE 10 + 0.05 = 10.05, PE 8 + 0.05 = 8.05."""
    r = run(straddle(), market(option_overrides=FILL), start=P, end=D)
    [t] = r.trades
    assert (t.expiry, t.facts["decided_time"], t.facts["entry_time"], t.facts["exit_time"], t.exit_reason) == (
        E1, "09:20", "09:21", "15:15", "exit time")
    assert [leg.strike for leg in t.legs] == [ATM, ATM]
    assert t.sets == int(CAPITAL // t.margin_per_set) > 0
    units = t.sets * LOT
    assert [leg.entry_price for leg in t.legs] == pytest.approx([39.80, 37.81])
    assert [leg.exit_price for leg in t.legs] == pytest.approx([10.05, 8.05])
    fees = sum(CH.fill(Fill(D, "OPT", side, price, units))
               for side, price in (("sell", 39.80), ("sell", 37.81), ("buy", 10.05), ("buy", 8.05)))
    assert t.charges == pytest.approx(fees)
    assert t.pnl == pytest.approx((39.80 + 37.81 - 10.05 - 8.05) * units - fees)
    assert r.equity[D] == pytest.approx(CAPITAL + t.pnl)


def test_a_stop_inside_a_minute_exits_at_the_stop_level():
    jump = {(D, "10:00", ATM, "CE"): {"high": 120.0}}
    [t] = run(straddle(exit_=", stop_pct: 50"), market(option_overrides={**FILL, **jump}), start=P, end=D).trades
    assert (t.exit_reason, t.facts["exit_time"]) == ("stop", "10:00")
    before_costs = (t.pnl + t.charges) / (t.sets * LOT)
    assert -0.5 * PREMIUM - 1.0 < before_costs < -0.5 * PREMIUM       # only the exit's half-spreads below it


def test_a_minute_that_opens_past_the_stop_pays_the_gap():
    gap = {(D, "10:00", ATM, "CE"): {"open": 150.0, "high": 150.0}}
    [t] = run(straddle(exit_=", stop_pct: 50"), market(option_overrides={**FILL, **gap}), start=P, end=D).trades
    assert t.exit_reason == "stop"
    assert t.legs[0].exit_price == pytest.approx(150.0 + 0.75)


def test_a_minute_that_touches_both_is_a_stop():
    both = {(D, "10:00", ATM, "CE"): {"high": 120.0, "low": 0.05}, (D, "10:00", ATM, "PE"): {"low": 0.05}}
    [t] = run(straddle(exit_=", stop_pct: 50, target_pct: 50"),
              market(option_overrides={**FILL, **both}), start=P, end=D).trades
    assert t.exit_reason == "stop"


def test_a_target_inside_a_minute_exits_at_the_target_level():
    cheap = {(D, "11:00", ATM, "CE"): {"low": 5.0}, (D, "11:00", ATM, "PE"): {"low": 5.0}}
    [t] = run(straddle(exit_=", target_pct: 50"), market(option_overrides={**FILL, **cheap}), start=P, end=D).trades
    assert (t.exit_reason, t.facts["exit_time"]) == ("target", "11:00")
    before_costs = (t.pnl + t.charges) / (t.sets * LOT)
    assert 0.5 * PREMIUM - 1.0 < before_costs < 0.5 * PREMIUM


def test_a_leg_that_has_not_traded_in_five_minutes_skips_the_entry():
    quiet = {(D, f"09:{m}", ATM, "CE"): {"volume": 0} for m in range(16, 21)}
    r = run(straddle(window=("09:20", "09:25")), market(option_overrides=quiet), start=P, end=D)
    assert [(s.day, s.kind) for s in r.skips] == [(D, NO_TRADE)]
    [t] = r.trades
    assert (t.facts["decided_time"], t.facts["entry_time"]) == ("09:21", "09:22")


def test_decisions_read_the_previous_session_s_facts_not_the_day_s_own():
    s = straddle(when="iv_rank > 60")
    assert len(run(s, market(facts_frame=facts(iv_rank=[70.0, 10.0])), start=P, end=D).trades) == 1
    assert run(s, market(facts_frame=facts(iv_rank=[10.0, 70.0])), start=P, end=D).trades == []


def test_the_opening_range_is_known_only_from_09_45():
    moves = {(D, "09:30"): {"high": 23560.0}, (D, "10:00"): {"close": 23600.0, "high": 23600.0}}
    [t] = run(orb(), market(index_overrides=moves), start=P, end=D).trades
    assert t.facts["decided_time"] == "10:00"


def test_a_decision_never_sees_a_later_price():
    later = {(D, hhmm, k, typ): {"open": 999.0, "high": 999.0, "low": 999.0, "close": 999.0}
             for hhmm in ("09:25", "09:40", "10:00") for k in STRIKES for typ in ("CE", "PE")}
    base = run(straddle(), market(option_overrides=FILL), start=P, end=D).trades[0]
    seen = run(straddle(), market(option_overrides={**FILL, **later}), start=P, end=D).trades[0]
    assert ([x.strike for x in seen.legs], seen.margin_per_set, seen.sets, seen.facts["decided_time"]) == (
        [x.strike for x in base.legs], base.margin_per_set, base.sets, base.facts["decided_time"])


def test_re_entry_up_to_the_day_s_limit():
    r = run(straddle(window=("09:20", "12:00"), exit_=", target_pct: 10", per_day=2), market(), start=P, end=D)
    assert len(r.trades) == 2 and all(t.exit_reason == "target" for t in r.trades)


def test_a_day_without_option_minutes_is_skipped_and_said():
    r = run(straddle(), market(with_options=False), start=P, end=D)
    assert r.trades == [] and [(s.day, s.kind) for s in r.skips] == [(D, NO_MINUTES)]


def test_positional_strategies_run_on_the_daily_engine():
    s = parse("""
version: options/1
name: positional
underlying: NIFTY
mode: positional
expiry: {cycle: weekly, which: 0}
legs: [{side: sell, type: CE, strike: {atm: 0}, lots: 1}]
""")
    with pytest.raises(StrategyError, match="fno.engine"):
        run(s, market(), start=P, end=D)
```

`fno/intraday.py`:

```python
"""The intraday options backtester (design 2026-10-19): minute by minute, out by the exit time.

ONE SESSION, on the index's own minute clock:
  1. Fill at this minute's open what was decided on the last minute - an entry,
     or the strategy's own exit condition.
  2. A position still open at the exit time closes at this minute's opens.
  3. Otherwise stop, then target, against this minute's prices, worst case
     first: sold legs at their high, bought legs at their low. A minute that
     OPENS past the stop pays the opens (the gap); otherwise the exit is at the
     stop level, each leg priced between its open and its worst in the
     proportion that puts the position exactly there. The target the same way
     with the best prices. A minute that touches both is a stop.
  4. When the minute has finished, and another minute comes before the exit
     time: the strategy's exit condition; or - flat, inside the entry window,
     under the day's entry limit - the entry condition: expiry and strikes from
     the chain at this minute, sized by margin, filled at the next open.

POINT IN TIME (design §2): the previous session's daily facts, never the day's
own; India VIX and the index from their minutes; calendar facts for the day.

NO PHANTOM FILLS. A leg that has not traded in the LOOKBACK_MINUTES up to the
decision, or has no candle in the fill minute, skips the entry with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from typing import Any

import numpy as np
import pandas as pd

from expr_eval import evaluate
from fno import account, fills, margin
from fno.costs import Fill, FnoCharges
from fno.engine import (CAPITAL, LIMIT_NOTE, NO_CAPITAL, NO_EXPIRY, NO_STRIKE, NO_TRADE,
                        BacktestResult, Skip, Trade, TradeLeg, _within_limits)
from fno.intraday_market import IntradayMarket
from fno.rates import risk_free
from fno.strategy import FACT_NAMES, OptionsStrategy, StrategyError
from fno.strikes import NoStrike, expiries_of, pick_strikes

NO_MINUTES = "no minute data"
LOOKBACK_MINUTES = 5
OPENING_RANGE = (time(9, 15), time(9, 45))
ONE_MINUTE = pd.Timedelta(minutes=1)

NOTES = (
    "Minute data: decisions on a finished minute, fills at the next minute's open. Stops and "
    "targets are checked on every minute's high and low, worst case first, a gap at the open paid.",
    "Decisions see the previous session's daily facts, and live India VIX and index minutes.",
    "Margin is a SPAN-style estimate, not yet calibrated against a broker's calculator.",
    "Real option minutes begin in Oct 2024 (Upstox): about 12 months of training, the minimum "
    "the owner set for intraday strategies on 19 Oct 2026.",
)


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def _candle(bars: pd.DataFrame, t: pd.Timestamp) -> pd.Series | None:
    try:
        row = bars.loc[t]
    except KeyError:
        return None
    return row if isinstance(row, pd.Series) else row.iloc[-1]


def _contracts(bars: pd.DataFrame, upto: pd.Timestamp, lot: int) -> int:
    """Contracts traded in the LOOKBACK_MINUTES minutes ending with `upto`."""
    start = upto - (LOOKBACK_MINUTES - 1) * ONE_MINUTE
    return int(bars.loc[start:upto, "volume"].sum()) // lot


@dataclass(frozen=True)
class _Plan:
    expiry: date
    strikes: tuple[float, ...]
    sets: int
    lot: int
    margin_per_set: float
    equity: float
    facts: dict[str, Any]


@dataclass
class _Held:
    trade: Trade
    bars: list[pd.DataFrame]
    last: list[float]

    def weights(self) -> list[int]:
        return [leg.sign * leg.units for leg in self.trade.legs]

    def types(self) -> list[str]:
        return [leg.type for leg in self.trade.legs]

    def strikes(self) -> list[float]:
        return [leg.strike for leg in self.trade.legs]

    def minute(self, t: pd.Timestamp) -> tuple[list[float], list[float], list[float], list[float], list[int]]:
        """Each leg's open, high, low, close and volume in minute t; no candle repeats the last price."""
        o, h, l, c, v = [], [], [], [], []
        for i, bars in enumerate(self.bars):
            row = _candle(bars, t)
            if row is None:
                p = self.last[i]
                o.append(p), h.append(p), l.append(p), c.append(p), v.append(0)
            else:
                o.append(float(row["open"])), h.append(float(row["high"])), l.append(float(row["low"]))
                c.append(float(row["close"])), v.append(int(row["volume"]))
        return o, h, l, c, v

    def held_to_limits(self, prices: list[float], volumes: list[int]) -> list[float]:
        moved, _ = _within_limits(self.weights(), self.types(), self.strikes(), prices, volumes)
        return moved

    def pnl(self, prices: list[float]) -> float:
        return self.trade.premium + sum(w * p for w, p in zip(self.weights(), prices))


def _between(a: list[float], b: list[float], frac: float) -> list[float]:
    return [x + frac * (y - x) for x, y in zip(a, b)]


def _variables(market: IntradayMarket, day: date, prev: date, expiry: date, bars: pd.DataFrame,
               stev: dict[date, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    facts = market.daily.facts
    row = facts[facts["trade_date"] == prev]
    if len(row):
        r = row.iloc[0]
        for name in FACT_NAMES:
            value = r.get(name)
            if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
                out[name] = float(value)
    out["event_today"] = bool(market.daily.events.on(day))
    out["weekday"] = float(day.weekday())
    out["dte"] = out["expiry_dte"] = float((expiry - day).days)
    out["sessions_to_event"] = float(stev.get(day, np.nan))
    vix = market.vix_on(bars.index)
    out["vix"] = vix
    prev_vix = float(row.iloc[0]["vix"]) if len(row) and "vix" in row.columns else np.nan
    out["vix_chg_pct"] = 100 * (vix / prev_vix - 1)
    today = np.array([ts.date() == day for ts in bars.index], dtype=bool)
    clock = np.array([ts.time() for ts in bars.index])
    first = bars.loc[today]
    out["day_open"] = float(first["open"].iloc[0]) if len(first) else np.nan
    in_range = today & (clock >= OPENING_RANGE[0]) & (clock < OPENING_RANGE[1])
    after = today & (clock >= OPENING_RANGE[1])
    hi = float(bars.loc[in_range, "high"].max()) if in_range.any() else np.nan
    lo = float(bars.loc[in_range, "low"].min()) if in_range.any() else np.nan
    out["or_high"] = pd.Series(np.where(after, hi, np.nan), index=bars.index)
    out["or_low"] = pd.Series(np.where(after, lo, np.nan), index=bars.index)
    return out


def _signal(node: Any, bars: pd.DataFrame, variables: dict[str, Any],
            minutes: list[pd.Timestamp]) -> dict[int, bool]:
    if node is None:
        return {t.value: True for t in minutes}
    out = evaluate(node, bars, variables).reindex(pd.DatetimeIndex(minutes))
    return {t.value: (bool(v) if v == v else False) for t, v in out.items()}


def _plan(strategy: OptionsStrategy, market: IntradayMarket, day: date, prev: date, t: pd.Timestamp,
          expiry: date, rate: float, lot: int, equity: float, variables: dict[str, Any]) -> _Plan | Skip:
    snap = market.chain(day, expiry, t, rate=rate, lot=lot)
    try:
        strikes = pick_strikes(snap, strategy.legs)
    except NoStrike as exc:
        return Skip(day, NO_STRIKE, f"{t:%H:%M} {exc}")
    book = market.day_options(expiry, day)
    for leg, k in zip(strategy.legs, strikes):
        if _contracts(book[(k, leg.type)], t, lot) <= 0:
            return Skip(day, NO_TRADE, f"{leg.type} {k:g} expiring {expiry} has not traded in the "
                                       f"{LOOKBACK_MINUTES} minutes to {t:%H:%M}")
    known = snap.rows["iv"].dropna()
    fallback = float(np.median(known)) if len(known) else 0.2
    legs = []
    for leg, k in zip(strategy.legs, strikes):
        row = snap.rows[(snap.rows["option_type"] == leg.type) & (snap.rows["strike"] == k)].iloc[0]
        iv = float(row["iv"]) if np.isfinite(row["iv"]) else fallback
        legs.append(margin.MarginLeg(leg.sign, leg.type == "CE", k, leg.lots * lot, iv, float(row["settle"])))
    vix_now = float(variables["vix"].get(t, np.nan))
    per_set = margin.estimate(legs, forward=snap.forward, years=snap.dte / 365, rate=rate, vix=vix_now,
                              fill_day=day, expiry=expiry)
    sets = account.sets_affordable(equity, per_set, strategy.max_margin_pct)
    if sets == 0:
        return Skip(day, NO_CAPITAL, f"{t:%H:%M} one set needs Rs {per_set:,.0f} of margin; Rs {equity:,.0f} "
                                     f"at {strategy.max_margin_pct:g}% allows none")
    facts = {"vix": vix_now, "iv_rank": variables.get("iv_rank", np.nan),
             "event_today": variables["event_today"], "dte": (expiry - day).days,
             "trend": market.daily.trend(prev), "decided_time": f"{t:%H:%M}"}
    return _Plan(expiry, tuple(strikes), sets, lot, per_set, equity, facts)


def _open(strategy: OptionsStrategy, plan: _Plan, market: IntradayMarket, day: date, t: pd.Timestamp,
          charges: FnoCharges) -> _Held | Skip:
    book = market.day_options(plan.expiry, day)
    bars = [book[(k, leg.type)] for leg, k in zip(strategy.legs, plan.strikes)]
    candles = [_candle(b, t) for b in bars]
    for leg, k, c in zip(strategy.legs, plan.strikes, candles):
        if c is None:
            return Skip(day, NO_TRADE, f"{leg.type} {k:g} has no candle at {t:%H:%M}: entry skipped")
    units = [leg.lots * plan.lot * plan.sets for leg in strategy.legs]
    opens, moved = _within_limits([leg.sign * u for leg, u in zip(strategy.legs, units)],
                                  [leg.type for leg in strategy.legs], list(plan.strikes),
                                  [float(c["open"]) for c in candles], [int(c["volume"]) for c in candles])
    legs, premium, fees = [], 0.0, 0.0
    for i, (leg, k) in enumerate(zip(strategy.legs, plan.strikes)):
        price = fills.fill_price(opens[i], leg.side, _contracts(bars[i], t - ONE_MINUTE, plan.lot))
        fee = charges.fill(Fill(day, "OPT", leg.side, price, units[i]))
        premium -= leg.sign * price * units[i]
        fees += fee
        legs.append(TradeLeg(leg.type, leg.side, k, units[i], price, price,
                             note=LIMIT_NOTE if i == moved else ""))
    trade = Trade(market.underlying, plan.expiry, day, day, plan.sets, plan.lot, plan.margin_per_set,
                  plan.equity, {**plan.facts, "entry_time": f"{t:%H:%M}"}, legs, premium, premium - fees, fees)
    return _Held(trade, bars, [float(c["close"]) for c in candles])


def _exit(held: _Held, raw: list[float], t: pd.Timestamp, reason: str, charges: FnoCharges, day: date) -> float:
    trade = held.trade
    flow = 0.0
    for i, leg in enumerate(trade.legs):
        side = "sell" if leg.side == "buy" else "buy"
        price = fills.fill_price(raw[i], side, _contracts(held.bars[i], t - ONE_MINUTE, trade.lot_size))
        fee = charges.fill(Fill(day, "OPT", side, price, leg.units))
        flow += leg.sign * price * leg.units - fee
        trade.charges += fee
        leg.exit_price = leg.mark = price
    trade.cash += flow
    trade.exited, trade.exit_reason, trade.pnl = day, reason, trade.cash
    trade.facts["exit_time"] = f"{t:%H:%M}"
    return flow


def _opens(held: _Held, t: pd.Timestamp) -> list[float]:
    o, _, _, _, v = held.minute(t)
    return held.held_to_limits(o, v)


def _breach(strategy: OptionsStrategy, held: _Held, t: pd.Timestamp) -> tuple[str, list[float]] | None:
    o, h, l, c, v = held.minute(t)
    legs = held.trade.legs
    o = held.held_to_limits(o, v)
    worst = held.held_to_limits([hi if leg.sign < 0 else lo for leg, hi, lo in zip(legs, h, l)], v)
    best = held.held_to_limits([lo if leg.sign < 0 else hi for leg, hi, lo in zip(legs, h, l)], v)
    held.last = c
    basis = abs(held.trade.premium)
    if basis <= 0:
        return None
    at_open, at_worst, at_best = held.pnl(o), held.pnl(worst), held.pnl(best)
    ex = strategy.exit
    if ex.stop_pct is not None:
        level = -ex.stop_pct / 100 * basis
        if at_open <= level:
            return "stop", o
        if at_worst <= level:
            return "stop", _between(o, worst, (level - at_open) / (at_worst - at_open))
    if ex.target_pct is not None:
        level = ex.target_pct / 100 * basis
        if at_open >= level:
            return "target", o
        if at_best >= level:
            return "target", _between(o, best, (level - at_open) / (at_best - at_open))
    return None


def _session(strategy: OptionsStrategy, market: IntradayMarket, day: date, cash: float, charges: FnoCharges,
             trades: list[Trade], skips: list[Skip], stev: dict[date, float]) -> float:
    prev = market.previous_session(day)
    if prev is None:
        return cash
    prev_rows = market.daily.rows(prev)
    listed = expiries_of(prev_rows, prev, strategy.cycle)
    if strategy.which >= len(listed):
        skips.append(Skip(day, NO_EXPIRY, f"no {strategy.cycle} expiry number {strategy.which} listed on {prev}"))
        return cash
    expiry = listed[strategy.which]
    if not market.day_options(expiry, day):
        skips.append(Skip(day, NO_MINUTES, f"no option minutes for the {expiry} expiry on {day}"))
        return cash
    bars = market.index_bars(day)
    minutes = [ts for ts in bars.index if ts.date() == day]
    if not minutes:
        skips.append(Skip(day, NO_MINUTES, f"no index minutes on {day}"))
        return cash
    variables = _variables(market, day, prev, expiry, bars, stev)
    enter_on = _signal(strategy.entry_when, bars, variables, minutes)
    exit_on = _signal(strategy.exit.when, bars, variables, minutes) if strategy.exit.when is not None else {}
    lot = market.daily.lot_size(prev, prev_rows[prev_rows["expiry"] == expiry])
    rate = risk_free(day)
    window = (_clock(strategy.window[0]), _clock(strategy.window[1]))
    exit_at = _clock(strategy.exit.time)

    held: _Held | None = None
    pending: tuple[str, Any] | None = None
    entries = 0
    for i, t in enumerate(minutes):
        if pending is not None:
            kind, payload = pending
            pending = None
            if kind == "enter":
                opened = _open(strategy, payload, market, day, t, charges)
                if isinstance(opened, Skip):
                    skips.append(opened)
                else:
                    held = opened
                    cash += held.trade.cash
                    entries += 1
            elif held is not None:
                cash += _exit(held, _opens(held, t), t, payload, charges, day)
                trades.append(held.trade)
                held = None
        if held is not None and t.time() >= exit_at:
            cash += _exit(held, _opens(held, t), t, "exit time", charges, day)
            trades.append(held.trade)
            held = None
        if held is not None:
            hit = _breach(strategy, held, t)
            if hit is not None:
                cash += _exit(held, hit[1], t, hit[0], charges, day)
                trades.append(held.trade)
                held = None
        if i + 1 == len(minutes) or minutes[i + 1].time() >= exit_at:
            continue
        if held is not None:
            if exit_on.get(t.value, False):
                pending = ("exit", "exit condition")
        elif (window[0] <= t.time() < window[1] and entries < strategy.max_per_day
              and enter_on.get(t.value, False)):
            plan = _plan(strategy, market, day, prev, t, expiry, rate, lot, cash, variables)
            if isinstance(plan, Skip):
                skips.append(plan)
            else:
                pending = ("enter", plan)
    if held is not None:
        cash += _exit(held, held.last, minutes[-1], "end of day", charges, day)
        trades.append(held.trade)
    return cash


def run(strategy: OptionsStrategy, market: IntradayMarket, *, start: date, end: date,
        capital: float = CAPITAL, charges: FnoCharges | None = None) -> BacktestResult:
    if strategy.mode != "intraday":
        raise StrategyError(f"{strategy.name}: positional strategies run on fno.engine")
    if strategy.underlying != market.underlying:
        raise ValueError(f"{strategy.name} trades {strategy.underlying}; the data is {market.underlying}")
    charges = charges or FnoCharges.load()
    stev = market.daily.sessions_to_event()
    cash = float(capital)
    trades: list[Trade] = []
    skips: list[Skip] = []
    equity: dict[date, float] = {}
    for day in market.sessions(start, end):
        cash = _session(strategy, market, day, cash, charges, trades, skips, stev)
        equity[day] = cash
    return BacktestResult(strategy.name, strategy.underlying, start, end, float(capital), trades, skips,
                          pd.Series(equity, dtype=float), NOTES)
```

- [ ] **Steps:** tests; run (fail); module; run (pass); full F&O suite; commit `feat(fno): the intraday engine - minute fills, worst-case stops, previous-day facts`.

---

### Task 4: The pass rule

`tests/test_fno_rules.py`:

```python
"""The options pass rule (design 2026-10-02 §6; 12-month intraday minimum, 2026-10-19)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import rules  # noqa: E402


def reading(**kw):
    b = dict(month_count=13, avg_month_pct=6.0, trades_per_month=20.0, years_beating_holding_pct=100.0,
             edge_t=2.5, avg_excess_pct=1.0)
    h = dict(worst_day_pct=-4.0, worst_fall_pct=12.0)
    for k, v in kw.items():
        (h if k in h else b)[k] = v
    return SimpleNamespace(headline=h, basket=SimpleNamespace(**b))


def test_a_good_year_passes_as_intraday_but_is_too_short_as_positional():
    assert rules.disqualified(reading(), "intraday") is None
    assert "needs 24" in rules.disqualified(reading(), "positional")


def test_each_rule_says_why():
    assert "worst day" in rules.disqualified(reading(worst_day_pct=-12.0), "intraday")
    assert "fell" in rules.disqualified(reading(worst_fall_pct=35.0), "intraday")
    assert "lost money" in rules.disqualified(reading(avg_month_pct=-0.5), "intraday")
    assert "too slow" in rules.disqualified(reading(trades_per_month=2.0, avg_month_pct=1.0), "intraday")
    assert "years" in rules.disqualified(reading(years_beating_holding_pct=40.0), "intraday")
    assert "luck" in rules.disqualified(reading(edge_t=1.2), "intraday")
```

`fno/rules.py`:

```python
"""The options pass rule (design 2026-10-02 §6), in the owner's words.

A strategy qualifies only if all hold: enough history (24 months positional,
12 months intraday - the owner's decision of 19 Oct 2026, because real option
minutes begin in Oct 2024), worst fall within 30%, worst single day no worse
than -10%, a positive average month, at least 10 trades a month or the target
month, beating holding the index in at least half its years, and a lead over
holding larger than luck explains (t >= 2).
"""

from __future__ import annotations

from typing import Any

from research.portfolio import LUCK_STRONG_T
from research.segment import TARGET_MONTHLY_MIN

MIN_MONTHS = {"positional": 24, "intraday": 12}
MAX_FALL_PCT = 30.0
WORST_DAY_PCT = -10.0
MIN_TRADES_PER_MONTH = 10.0
MIN_YEARS_BEATING_HOLDING_PCT = 50.0


def disqualified(report: Any, mode: str) -> str | None:
    """Why `report` cannot pass, or None if it does."""
    h, b = report.headline, report.basket
    need = MIN_MONTHS[mode]
    if b.month_count < need:
        return f"only {b.month_count} months of history (needs {need})"
    if h["worst_day_pct"] < WORST_DAY_PCT:
        return f"worst day {h['worst_day_pct']:+.1f}% (limit {WORST_DAY_PCT:.0f}%)"
    if h["worst_fall_pct"] > MAX_FALL_PCT:
        return f"fell {h['worst_fall_pct']:.1f}% from a high (limit {MAX_FALL_PCT:.0f}%)"
    if b.avg_month_pct <= 0:
        return f"lost money on average: {b.avg_month_pct:+.2f}% a month"
    if b.trades_per_month < MIN_TRADES_PER_MONTH and b.avg_month_pct < TARGET_MONTHLY_MIN:
        return (f"too slow: {b.trades_per_month:.1f} trades a month (needs {MIN_TRADES_PER_MONTH:.0f}, "
                f"or {TARGET_MONTHLY_MIN:.0f}% a month)")
    if b.years_beating_holding_pct < MIN_YEARS_BEATING_HOLDING_PCT:
        return f"beat holding the index in only {b.years_beating_holding_pct:.0f}% of years"
    luck = b.avg_excess_pct > 0 if b.edge_t is None else b.edge_t >= LUCK_STRONG_T
    if not luck:
        t = "n/a" if b.edge_t is None else f"{b.edge_t:+.2f}"
        return f"its lead over holding could be luck: t = {t} (needs {LUCK_STRONG_T:.0f})"
    return None
```

- [ ] **Steps:** test (fail), module (pass), commit `feat(fno): the options pass rule, 12 months for intraday`.

---

### Task 5: The intraday atlas and the script

- [ ] **Step 1:** append to `tests/test_fno_atlas.py`:

```python
def test_every_intraday_atlas_strategy_parses_for_both_underlyings():
    parsed = [parse(doc) for doc in atlas.intraday_documents()]
    assert len(parsed) == 2 * len(atlas.INTRADAY)
    assert all(s.mode == "intraday" and s.exit.time == atlas.EXIT_TIME for s in parsed)
    assert {s.window for s in parsed} >= {("09:20", "09:21"), ("09:45", "13:00")}
```

- [ ] **Step 2:** append to `fno/atlas.py`:

```python
# The intraday half (design 2026-10-19 §6): the classic same-day option trades.
EXIT_TIME = "15:15"
INTRADAY: dict[str, dict[str, Any]] = {
    "straddle-0920": {"window": ("09:20", "09:21"),
                      "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0})],
                      "exit": {"stop_pct": 30, "target_pct": 50}},
    "strangle-0930": {"window": ("09:30", "09:31"),
                      "legs": [_leg("sell", "CE", {"delta": 0.20}), _leg("sell", "PE", {"delta": -0.20})],
                      "exit": {"stop_pct": 50, "target_pct": 50}},
    "expiry-day-straddle": {"window": ("09:20", "09:21"), "when": "expiry_dte == 0",
                            "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0})],
                            "exit": {"stop_pct": 30, "target_pct": 50}},
    "iron-fly": {"window": ("09:20", "09:21"),
                 "legs": [_leg("sell", "CE", {"atm": 0}), _leg("sell", "PE", {"atm": 0}),
                          _leg("buy", "CE", {"otm_pct": 2.0}), _leg("buy", "PE", {"otm_pct": 2.0})],
                 "exit": {"stop_pct": 50, "target_pct": 40}},
    "orb-call": {"window": ("09:45", "13:00"), "when": "close > or_high",
                 "legs": [_leg("buy", "CE", {"atm": 0})], "exit": {"stop_pct": 30, "target_pct": 60}},
    "orb-put": {"window": ("09:45", "13:00"), "when": "close < or_low",
                "legs": [_leg("buy", "PE", {"atm": 0})], "exit": {"stop_pct": 30, "target_pct": 60}},
}


def intraday_document(name: str, underlying: str) -> str:
    spec = INTRADAY[name]
    entry: dict[str, Any] = {"window": {"from": spec["window"][0], "to": spec["window"][1]}}
    if "when" in spec:
        entry["when"] = spec["when"]
    doc = {"version": VERSION, "name": f"atlas-intraday-{name}-{underlying.lower()}",
           "underlying": underlying, "mode": "intraday", "expiry": {"cycle": "weekly", "which": 0},
           "entry": entry, "legs": [dict(leg) for leg in spec["legs"]],
           "exit": {**spec["exit"], "time": EXIT_TIME}, "sizing": {"max_margin_pct": MARGIN_PCT}}
    return yaml.safe_dump(doc, sort_keys=False)


def intraday_documents() -> list[str]:
    return [intraday_document(name, u) for u in UNDERLYINGS for name in INTRADAY]
```

- [ ] **Step 3:** rewrite `scripts/fno_atlas.py`'s `main` so that `--intraday` measures `atlas.INTRADAY` on `IntradayMarket.load(root, u, until=end)` with `fno.intraday.run`, starting at `max(training start, market.first_day)`; both modes add `passes` / `why_not` from `rules.disqualified(report, mode)` to each saved report and a "passes?" column to the table; results go to `data/fno/atlas/results.json` (positional) or `intraday.json`.

`scripts/fno_atlas.py`:

```python
"""Measure the options atlas on the training years, NIFTY and BANKNIFTY.

    python scripts/fno_atlas.py                       # the positional atlas (daily data, since 2016)
    python scripts/fno_atlas.py --intraday            # the intraday atlas (minute data, since Oct 2024)
    python scripts/fno_atlas.py --only iron-condor

Writes data/fno/atlas/results.json or intraday.json (every report in full, each
with whether it passes the options rule and why not) and prints a markdown
table. The exam year is cut out at load and never read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import use_utf8_stdout  # noqa: E402
from fno import atlas, intraday, report, rules, store  # noqa: E402
from fno.engine import run  # noqa: E402
from fno.intraday_market import IntradayMarket  # noqa: E402
from fno.market import MarketData, stored_sessions, training_window  # noqa: E402
from fno.strategy import parse  # noqa: E402
from research.portfolio import luck_label  # noqa: E402

HEADER = ("| strategy | index | passes? | avg month | months up | worst day | worst fall | trades/month "
          "| years beating index | luck test | Rs 5 lakh became |\n"
          "|---|---|---|---:|---:|---:|---:|---:|---:|---|---:|")


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--intraday", action="store_true")
    parser.add_argument("--only", choices=sorted(set(atlas.ATLAS) | set(atlas.INTRADAY)))
    args = parser.parse_args(argv)
    catalogue = atlas.INTRADAY if args.intraday else atlas.ATLAS
    if args.only and args.only not in catalogue:
        parser.error(f"{args.only} is not in the {'intraday' if args.intraday else 'positional'} atlas")
    mode = "intraday" if args.intraday else "positional"
    root = store.root_dir()
    names = [args.only] if args.only else list(catalogue)
    reports, lines = [], [HEADER]
    for underlying in atlas.UNDERLYINGS:
        start, end = training_window(stored_sessions(root, underlying))
        if args.intraday:
            market = IntradayMarket.load(root, underlying, until=end)
            if market.first_day is None:
                print(f"{underlying}: no option minutes stored")
                continue
            start = max(start, market.first_day)
            closes, runner, document = market.daily.closes(), intraday.run, atlas.intraday_document
        else:
            market = MarketData.load(root, underlying, until=end)
            closes, runner, document = market.closes(), run, atlas.document
        print(f"{underlying}: training {start} -> {end}", flush=True)
        for name in names:
            result = runner(parse(document(name, underlying)), market, start=start, end=end)
            built = report.build(result, closes)
            why = rules.disqualified(built, mode)
            saved = built.as_dict()
            saved.update(passes=why is None, why_not=why)
            reports.append(saved)
            h = built.headline
            verdict = "**yes**" if why is None else f"no - {why}"
            lines.append(f"| {name} | {underlying} | {verdict} | {h['avg_month_pct']:+.2f}% "
                         f"| {h['months_positive_pct']:.0f}% | {h['worst_day_pct']:+.2f}% "
                         f"| {h['worst_fall_pct']:.1f}% | {h['trades_per_month']:.1f} "
                         f"| {h['years_beating_holding_pct']:.0f}% | {luck_label(h['edge_t'])} "
                         f"| Rs {h['end_value']:,.0f} |")
            print(lines[-1], flush=True)
    path = root / "atlas" / ("intraday.json" if args.intraday else "results.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reports, indent=2, default=str), encoding="utf-8")
    print("\n" + "\n".join(lines))
    print(f"\nwritten: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4:** full suite passes; commit `feat(fno): the intraday atlas, and a pass column in the atlas table`.

---

### Task 6: Measure, check against reality, write it down

- [ ] Rebuild the facts tables so the training window reaches today's data: `python scripts/fno_features.py --only NIFTY` and `--only BANKNIFTY`.
- [ ] Spot-check one real day by hand: NIFTY's 09:20 straddle on an expiry day — print the chain at 09:20, the fills at 09:21, every stop/target check, and compare entry and exit prices with the minute files.
- [ ] `python scripts/fno_atlas.py --intraday`, then the positional atlas again for its pass column.
- [ ] Sanity: short-premium intraday strategies should win often with occasional large losses on trend days; the ORB buys should lose on most trades and win big on a few. Investigate anything that contradicts this before writing it down.
- [ ] `docs/research/2026-10-19-fno-intraday-atlas.md`: the table, what each did, the 12-month caveat (no 2020-style crash inside it), what to try next. Commit.
