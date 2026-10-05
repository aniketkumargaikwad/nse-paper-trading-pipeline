"""Everything the options engine reads about one underlying (design §2.5, §4).

A decision on a day is handed that day's rows only; the fill at the next
open is handed the next session's rows. `until` cuts everything after a date
out at load - the atlas and a default backtest pass the training end, so the
exam year cannot be read even by mistake.

Two facts are made here rather than in fno.features, because they depend on
the strategy or on the calendar ahead:

* expiry_dte - calendar days to the strategy's OWN expiry (its cycle and
  `which`); the facts table's `dte` is always the nearest weekly.
* sessions_to_event - sessions until the next Budget / RBI / election-result
  day, 0 on the day, NaN beyond EVENT_HORIZON. Those dates are announced in
  advance and NSE publishes its holidays a year ahead, so counting forward
  to them is not hindsight.

TREND (for the report): the index's 50-day average against itself 20
sessions earlier - up more than 2% rising, down more than 2% falling.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from expr_eval import evaluate
from fno import spot as spot_store
from fno import store
from fno.contracts import LotSizes
from fno.events import Events
from fno.nse_files import COLUMNS
from fno.strikes import pick_expiry
from fno.yahoo import read_whole
from strategy.expr import Expr

TREND_SMA = 50
TREND_LOOKBACK = 20
TREND_BAND_PCT = 2.0
EVENT_HORIZON = 10
EXAM_DAYS = 365
TRAINING_START = date(2016, 1, 1)
BOOL_FACTS = ("event_today",)


def training_window(sessions: list[date]) -> tuple[date, date]:
    """The training years: from 2016 to the last session before the exam year."""
    if not sessions:
        raise ValueError("no sessions stored")
    cutoff = sessions[-1] - timedelta(days=EXAM_DAYS)
    inside = [d for d in sessions if TRAINING_START <= d <= cutoff]
    if not inside:
        raise ValueError("no training sessions before the exam year")
    return inside[0], inside[-1]


def features_path(root: Path, underlying: str) -> Path:
    return Path(root) / "features" / f"{underlying}.parquet"


def stored_sessions(root: Path, underlying: str) -> list[date]:
    """Every session the facts table has, without loading the rest."""
    return sorted(pd.read_parquet(features_path(root, underlying), columns=["trade_date"])["trade_date"])


def _empty() -> pd.DataFrame:
    return pd.DataFrame(columns=list(COLUMNS))


class MarketData:
    def __init__(self, underlying: str, *, facts: pd.DataFrame, spot: pd.DataFrame, lots: LotSizes,
                 read_year: Callable[[int], pd.DataFrame], calendar: pd.DataFrame, events: Events,
                 until: date | None = None) -> None:
        if until is not None:
            facts = facts[facts["trade_date"] <= until]
            spot = spot[spot["trade_date"] <= until]
            calendar = calendar[calendar["trade_date"] <= until]
        self.underlying = underlying
        self.until = until
        self.facts = facts.sort_values("trade_date").reset_index(drop=True)
        self.sessions_all: list[date] = list(self.facts["trade_date"])
        self._position = {d: i for i, d in enumerate(self.sessions_all)}
        self._facts_by_day = self.facts.set_index("trade_date")
        self.spot = spot.sort_values("trade_date").reset_index(drop=True)
        self._closes = dict(zip(self.spot["trade_date"], self.spot["close"].astype(float)))
        self.lots = lots
        self.calendar = calendar
        self.events = events
        self._read_year = read_year
        self._years: dict[int, dict[date, pd.DataFrame]] = {}
        self._trend = self._trend_labels()

    @classmethod
    def from_frames(cls, underlying: str, *, daily: pd.DataFrame, facts: pd.DataFrame,
                    spot: pd.DataFrame, lots: LotSizes, events: Events,
                    until: date | None = None) -> "MarketData":
        def read_year(year: int) -> pd.DataFrame:
            return daily[[d.year == year for d in daily["trade_date"]]].reset_index(drop=True)

        calendar = daily[["trade_date", "instrument", "expiry"]].drop_duplicates()
        return cls(underlying, facts=facts, spot=spot, lots=lots, read_year=read_year,
                   calendar=calendar, events=events, until=until)

    @classmethod
    def load(cls, root: Path, underlying: str, *, until: date | None = None,
             lots: LotSizes | None = None, events: Events | None = None) -> "MarketData":
        root = Path(root)
        facts = pd.read_parquet(features_path(root, underlying))
        spot = read_whole(spot_store.spot_path(root, underlying))
        years = sorted(int(p.stem) for p in (root / "daily" / underlying).glob("*.parquet"))
        calendar = pd.concat(
            [pd.read_parquet(store.daily_path(root, underlying, y),
                             columns=["trade_date", "instrument", "expiry"]).drop_duplicates()
             for y in years], ignore_index=True)

        def read_year(year: int) -> pd.DataFrame:
            return store.read_daily(root, underlying, date(year, 1, 1), date(year, 12, 31))

        return cls(underlying, facts=facts, spot=spot, lots=lots or LotSizes.load(),
                   read_year=read_year, calendar=calendar, events=events or Events.load(), until=until)

    # -- the calendar -------------------------------------------------------

    def sessions(self, start: date, end: date) -> list[date]:
        return [d for d in self.sessions_all if start <= d <= end]

    def next_session(self, day: date) -> date | None:
        i = self._position.get(day)
        return self.sessions_all[i + 1] if i is not None and i + 1 < len(self.sessions_all) else None

    # -- one day ------------------------------------------------------------

    def rows(self, day: date) -> pd.DataFrame:
        """That day's F&O rows and nothing else (empty after `until`)."""
        if self.until is not None and day > self.until:
            return _empty()
        if day.year not in self._years:
            frame = self._read_year(day.year)
            self._years = {y: v for y, v in self._years.items() if y >= day.year - 1}
            self._years[day.year] = {d: g.reset_index(drop=True) for d, g in frame.groupby("trade_date")}
        return self._years[day.year].get(day, _empty())

    def fact(self, day: date, name: str) -> Any:
        try:
            return self._facts_by_day.at[day, name]
        except KeyError:
            return float("nan")

    def spot_close(self, day: date) -> float:
        return self._closes.get(day, float("nan"))

    def closes(self) -> dict[date, float]:
        return dict(self._closes)

    def lot_size(self, day: date, rows: pd.DataFrame) -> int:
        """NSE's own lot where the file has one (from 8 Jul 2024), else lot_sizes.yaml."""
        published = rows.loc[rows["instrument"] == "OPT", "lot_size"].dropna()
        if not published.empty:
            return int(published.mode().iloc[0])
        return self.lots.on(self.underlying, day)

    def final_settlement(self, expiry: date, rows: pd.DataFrame) -> float:
        """The index level an expiring option pays out on. On the expiry day NSE's old
        file gives the OPTION a settlement price of 0; the expiring future's settles at
        the index close, so that is read first, then the close itself."""
        fut = rows[(rows["instrument"] == "FUT") & (rows["expiry"] == expiry) & (rows["settle"] > 0)]
        if not fut.empty:
            return float(fut["settle"].iloc[0])
        close = self.spot_close(expiry)
        if close == close:
            return close
        published = rows["underlying_price"].dropna()
        if not published.empty:
            return float(published.iloc[0])
        raise LookupError(f"{self.underlying} {expiry}: no final settlement price "
                          "(no expiring future, no index close)")

    # -- facts the engine makes ---------------------------------------------

    def expiry_dte(self, cycle: str, which: int) -> dict[date, float]:
        out: dict[date, float] = {}
        for day, rows in self.calendar.groupby("trade_date"):
            expiry = pick_expiry(rows, day, cycle, which)
            out[day] = float((expiry - day).days) if expiry is not None else float("nan")
        return out

    def sessions_to_event(self) -> dict[date, float]:
        s = self.sessions_all
        if not s:
            return {}
        events = set(self.events.between(s[0], s[-1]))
        return {day: next((float(k) for k in range(EVENT_HORIZON + 1)
                           if i + k < len(s) and s[i + k] in events), float("nan"))
                for i, day in enumerate(s)}

    def _trend_labels(self) -> dict[date, str]:
        close = self.spot.set_index("trade_date")["close"].astype(float)
        sma = close.rolling(TREND_SMA).mean()
        change = 100 * (sma / sma.shift(TREND_LOOKBACK) - 1)
        return {day: ("unknown" if c != c else "rising" if c > TREND_BAND_PCT
                      else "falling" if c < -TREND_BAND_PCT else "sideways")
                for day, c in change.items()}

    def trend(self, day: date) -> str:
        return self._trend.get(day, "unknown")

    def signal(self, node: Expr | None, extra: dict[str, dict[date, float]]) -> dict[date, bool]:
        """Whether `node` holds at each session's close. The index's own daily candles
        are the bars (all of Yahoo's history, so sma(200) is ready in 2016); the facts
        and `extra` are variables, missing on days the F&O market has none."""
        if node is None:
            return {d: True for d in self.sessions_all}
        days = sorted(set(self.spot["trade_date"]) | set(self.sessions_all))
        index = pd.DatetimeIndex(pd.to_datetime(days))
        bars = self.spot.assign(trade_date=pd.to_datetime(self.spot["trade_date"])).set_index("trade_date")
        df = bars.reindex(index)[["open", "high", "low", "close"]].astype(float)
        facts = (self.facts.assign(trade_date=pd.to_datetime(self.facts["trade_date"]))
                 .set_index("trade_date").reindex(index))
        variables: dict[str, pd.Series] = {}
        for name in facts.columns:
            if name in BOOL_FACTS:
                variables[name] = facts[name].astype("boolean").fillna(False).astype(bool)
            elif pd.api.types.is_numeric_dtype(facts[name]):
                variables[name] = facts[name].astype(float)
        for name, values in extra.items():
            variables[name] = pd.Series({pd.Timestamp(d): v for d, v in values.items()},
                                        dtype=float).reindex(index)
        out = evaluate(node, df, variables)

        def holds(value: Any) -> bool:
            return bool(value) if value == value else False

        return {d: holds(out.get(pd.Timestamp(d), False)) for d in self.sessions_all}
