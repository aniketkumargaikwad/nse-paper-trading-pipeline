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
