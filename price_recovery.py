"""Recovering the sessions the candle store is missing, from a second feed.

WHY THIS EXISTS
---------------
The store stops on 31 July 2026. Dhan access lapsed on 11 September, so
nothing has topped it up since and nothing will. Yahoo still serves 5-minute
candles for roughly the last 58 days, which is the only free way left to get
those sessions back - and it is a closing window, not a standing option.

THE PRICE BASIS IS THE WHOLE JOB
--------------------------------
Dhan's intraday feed is RAW. Yahoo is fetched with `auto_adjust=True`, so it
is split *and* dividend adjusted to today's basis. Appending one to the other
produces precisely the artefact `price_adjust.py` exists to remove: a step at
the join that no rule can tell from a real overnight move. A 3% dividend
adjustment is small enough to look like an ordinary gap and large enough to
be the best signal a momentum rule has ever seen.

So nothing is appended until the two feeds have been measured against each
other on the sessions they BOTH hold, and the incoming candles rescaled onto
the stored basis. That measurement is the reason the deadline is earlier than
the data expiry: the overlap is made of sessions on or before 31 July, and
those age out of Yahoo's window before August does.

WHAT IS DELIBERATELY NOT DONE HERE
----------------------------------
* **Nothing is overwritten.** Incoming candles are trimmed to strictly after
  the last stored timestamp. Where the feeds overlap, the stored candle wins
  and the incoming one is used only as evidence. Letting a second feed
  restate history already backtested on would change results with no record
  of why.
* **Volume is never rescaled.** Same reason as `price_adjust.Adjustment`:
  the measured volume gap between two feeds tracks how they count volume, not
  corporate actions. Volume-based rules stay wrong across the join, which is
  a stated limit rather than a hidden one.
* **Nothing is uploaded.** This module writes no files and reaches no
  network. Where recovered candles go is the caller's decision.

Pure module: no I/O, no network, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Sequence

import numpy as np
import pandas as pd

from config import IST, MARKET_CLOSE_IST, MARKET_OPEN_IST, UTC

# A full NSE session is 09:15 to 15:30 IST: 375 minutes, so 75 five-minute
# candles, the last of them starting at 15:25.
SESSION_MINUTES = 375
EXPECTED_BARS: dict[str, int] = {"5m": SESSION_MINUTES // 5, "day": 1}

# Minutes per candle, for the stored intraday timeframes. Used to check that
# an incoming feed sits on the same grid the 15/30/60-minute resampling is
# built on - see resample.py, which groups from the 09:15 open rather than
# the clock hour.
TIMEFRAME_MINUTES: dict[str, int] = {"5m": 5}

# A session holding fewer than this share of its expected candles is reported
# as thin. Yahoo drops bars at session edges and around halts, and a session
# missing a fifth of itself is not the same object as a stored one.
THIN_SESSION_RATIO = 0.8

# How many sessions the two feeds must share before a rebasing factor is
# trusted. Three is the same floor `price_adjust.MIN_SEGMENT_DAYS` uses, and
# for the same reason: a median over two points is not a median.
MIN_OVERLAP_SESSIONS = 3

# How far the per-session ratios may spread before the factor is refused.
# The two feeds price a session's last candle a few basis points apart
# (different closing-auction handling, see price_adjust), measured at up to
# 1.1% on clean symbols. Beyond 2% the ratios are not describing one constant
# basis difference, so their median is not a correction - it is an average of
# two different things.
MAX_RATIO_SPREAD = 0.02

# A factor this close to 1.0 is noise, not a correction. Mirrors
# price_adjust.NEGLIGIBLE so the two layers cannot disagree about what counts
# as a real basis difference.
NEGLIGIBLE_FACTOR = 0.02

# An overnight move across the join beyond this is reported as a possible
# missed corporate action. Deliberately tighter than
# data_quality.SPLIT_RATIO_THRESHOLD (1.5, sized for splits): at a feed join
# the interesting failure is a few percent of unremoved dividend adjustment,
# which 1.5 would never see.
JOIN_BREAK_RATIO = 0.05


class RecoveryError(RuntimeError):
    """The recovery cannot proceed safely and must not be forced."""


# ---------------------------------------------------------------------------
# The window Yahoo will still serve
# ---------------------------------------------------------------------------


def earliest_recoverable(now_utc: datetime, max_history_days: int) -> date:
    """The oldest IST session date the provider can still serve.

    Taken from the provider's own limit rather than restated here, so this
    cannot drift from `yfinance_client.YF_MAX_HISTORY_DAYS`.
    """
    return (now_utc - timedelta(days=max_history_days)).astimezone(IST).date()


def expires_on(session: date, max_history_days: int) -> date:
    """The last day on which `session` can still be fetched.

    Stated per session because the answer is not one date. The window is a
    rolling one, so the oldest missing session is always the one about to be
    lost, and "August is available until late October" is true only of the
    end of August.
    """
    return session + timedelta(days=max_history_days)


def trading_sessions(
    first: date, last: date, holidays: frozenset[date]
) -> list[date]:
    """Sessions NSE should have held in [first, last], inclusive."""
    out: list[date] = []
    day = first
    while day <= last:
        if day.weekday() < 5 and day not in holidays:
            out.append(day)
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Session shaping
# ---------------------------------------------------------------------------


def ist_dates(frame: pd.DataFrame) -> np.ndarray:
    """The IST session date of every candle."""
    if frame.empty:
        return np.array([], dtype=object)
    return frame.index.tz_convert(IST).date


def trim_to_session(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Drop intraday candles that fall outside 09:15-15:30 IST.

    Yahoo emits a stub candle stamped at the closing bell for some sessions,
    and occasionally a pre-open one. Both are outside the session the stored
    candles describe, and a 76th candle in a 75-candle day quietly changes
    every resampled 15/30/60-minute bar built from it.
    """
    if timeframe == "day" or frame.empty:
        return frame
    local = frame.index.tz_convert(IST).time
    inside = (local >= MARKET_OPEN_IST) & (local < MARKET_CLOSE_IST)
    return frame[inside]


def session_last_close(frame: pd.DataFrame) -> pd.Series:
    """Each session's final close, indexed by IST date."""
    if frame.empty:
        return pd.Series(dtype="float64")
    return (
        pd.DataFrame(
            {"date": ist_dates(frame), "close": frame["close"].astype(float).to_numpy()}
        )
        .groupby("date", sort=True)["close"]
        .last()
    )


def session_bar_counts(frame: pd.DataFrame) -> pd.Series:
    """How many candles each session holds, indexed by IST date."""
    if frame.empty:
        return pd.Series(dtype="int64")
    return pd.Series(ist_dates(frame)).value_counts().sort_index()


# ---------------------------------------------------------------------------
# Timestamp alignment
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Alignment:
    """Whether the incoming feed stamps candles where the stored one does."""

    timeframe: str
    stored_offsets: tuple[time, ...]
    incoming_offsets: tuple[time, ...]
    restamp_to: time | None = None      # daily only: the stored time-of-day
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.note


def _times_of_day(frame: pd.DataFrame) -> tuple[time, ...]:
    if frame.empty:
        return ()
    return tuple(sorted({t for t in frame.index.tz_convert(IST).time}))


def check_alignment(
    stored: pd.DataFrame, incoming: pd.DataFrame, timeframe: str
) -> Alignment:
    """Compare where the two feeds put their candles within a session.

    Intraday candles must land on the same five-minute offsets or the merged
    series interleaves two grids and every resampled bar is built from a
    mixture.

    DAILY IS DIFFERENT AND THIS IS THE TRAP. Dhan stamps a daily candle from
    an epoch second; Yahoo stamps it at IST midnight. Both are the right
    *day*, so nothing looks wrong - but stored and incoming would sit at
    different times of day and the store would hold TWO candles for every
    recovered session. The stored time-of-day is therefore returned and the
    incoming candles are re-stamped to it.
    """
    stored_offsets = _times_of_day(stored)
    incoming_offsets = _times_of_day(incoming)

    if timeframe == "day":
        if len(stored_offsets) != 1:
            return Alignment(
                timeframe, stored_offsets, incoming_offsets,
                note=(
                    "stored daily candles do not share one time of day "
                    f"({len(stored_offsets)} distinct); re-stamping incoming "
                    "ones would guess at which is correct"
                ),
            )
        return Alignment(
            timeframe, stored_offsets, incoming_offsets,
            restamp_to=stored_offsets[0],
        )

    # Checked against the SESSION GRID, not against the stored sample. An
    # earlier version compared the two sets of offsets directly, which
    # refused any symbol whose stored tail happened to be short: a store
    # holding six candles of its last session "never uses" 09:45, so a full
    # incoming session looked like a different grid. That is a refusal
    # against a deadline, for nothing - the invariant that actually matters
    # is the one resample.py relies on, that every candle sits a whole
    # number of periods after the 09:15 open.
    step = TIMEFRAME_MINUTES.get(timeframe)
    if step is None:
        return Alignment(
            timeframe, stored_offsets, incoming_offsets,
            note=f"{timeframe!r} is not a stored intraday timeframe",
        )
    off_grid = [t for t in incoming_offsets if not _on_session_grid(t, step)]
    if off_grid:
        return Alignment(
            timeframe, stored_offsets, incoming_offsets,
            note=(
                f"{len(off_grid)} incoming offset(s) do not sit on the "
                f"{step}-minute grid anchored at "
                f"{MARKET_OPEN_IST.strftime('%H:%M')} IST (e.g. "
                f"{off_grid[0].strftime('%H:%M')}); the two feeds are on "
                "different grids and every resampled bar would mix them"
            ),
        )
    return Alignment(timeframe, stored_offsets, incoming_offsets)


def _on_session_grid(at: time, step_minutes: int) -> bool:
    """Is `at` a whole number of periods after the session open?"""
    minutes = at.hour * 60 + at.minute
    open_minutes = MARKET_OPEN_IST.hour * 60 + MARKET_OPEN_IST.minute
    offset = minutes - open_minutes
    return at.second == 0 and offset >= 0 and offset % step_minutes == 0


def restamp_daily(frame: pd.DataFrame, at_ist: time) -> pd.DataFrame:
    """Move daily candles onto the stored feed's time of day."""
    if frame.empty:
        return frame
    out = frame.copy()
    out.index = pd.DatetimeIndex(
        [
            datetime.combine(d, at_ist, tzinfo=IST).astimezone(UTC)
            for d in ist_dates(frame)
        ],
        name="ts",
    )
    return out[~out.index.duplicated(keep="last")].sort_index()


# ---------------------------------------------------------------------------
# Rebasing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rebasing:
    """The factor putting an incoming feed onto the stored price basis."""

    factor: float
    method: str                         # 'overlap' | 'identity' | 'refused'
    sessions: int
    spread: float                       # max/min of the per-session ratios - 1
    ratios: tuple[float, ...] = ()
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.method != "refused"

    @property
    def is_material(self) -> bool:
        return abs(self.factor - 1.0) > NEGLIGIBLE_FACTOR

    def describe(self) -> str:
        if not self.ok:
            return f"REFUSED: {self.reason}"
        if not self.is_material:
            return (
                f"x{self.factor:.6f} over {self.sessions} shared session(s) - "
                "the feeds already agree, nothing rescaled"
            )
        return (
            f"x{self.factor:.6f} over {self.sessions} shared session(s), "
            f"ratios spread {self.spread * 100:.2f}%"
        )


def overlap_ratios(stored: pd.DataFrame, incoming: pd.DataFrame) -> pd.Series:
    """Per-session stored/incoming close ratios, for sessions in both feeds."""
    left = session_last_close(stored)
    right = session_last_close(incoming)
    shared = left.index.intersection(right.index)
    if len(shared) == 0:
        return pd.Series(dtype="float64")
    a = left[shared].astype(float)
    b = right[shared].astype(float)
    usable = (a > 0) & (b > 0) & np.isfinite(a) & np.isfinite(b)
    return (a[usable] / b[usable]).sort_index()


def measure_rebasing(
    stored: pd.DataFrame,
    incoming: pd.DataFrame,
    *,
    min_sessions: int = MIN_OVERLAP_SESSIONS,
    max_spread: float = MAX_RATIO_SPREAD,
) -> Rebasing:
    """Measure what the incoming feed must be multiplied by, or refuse.

    Refusing is the point. Recovering candles onto an unverified basis is
    worse than not recovering them: the gap is visible and a silent 3% step
    at the join is not.
    """
    ratios = overlap_ratios(stored, incoming)
    if len(ratios) < min_sessions:
        return Rebasing(
            factor=1.0, method="refused", sessions=len(ratios), spread=float("nan"),
            ratios=tuple(float(r) for r in ratios),
            reason=(
                f"only {len(ratios)} session(s) are held by both feeds, and "
                f"{min_sessions} are needed to tell a basis difference from "
                "one odd close. The overlap shrinks by a session a day, so "
                "this does not get better by waiting"
            ),
        )

    values = ratios.to_numpy(dtype=float)
    spread = float(values.max() / values.min() - 1.0)
    if spread > max_spread:
        return Rebasing(
            factor=1.0, method="refused", sessions=len(ratios), spread=spread,
            ratios=tuple(float(r) for r in values),
            reason=(
                f"the per-session ratios spread {spread * 100:.2f}%, beyond "
                f"the {max_spread * 100:.0f}% two feeds differ by on ordinary "
                "closing-auction noise. Something moved inside the overlap "
                "(a corporate action, or the feeds disagreeing about a "
                "session), so one factor cannot describe it"
            ),
        )

    return Rebasing(
        factor=float(np.median(values)),
        method="overlap",
        sessions=len(ratios),
        spread=spread,
        ratios=tuple(float(v) for v in values),
    )


def apply_rebasing(frame: pd.DataFrame, rebasing: Rebasing) -> pd.DataFrame:
    """Rescale prices onto the stored basis. Volume is left alone.

    An immaterial factor is not applied at all, so a symbol with no corporate
    action comes back byte-identical rather than multiplied by 1.0000003 and
    rounded.
    """
    if not rebasing.ok:
        raise RecoveryError(f"cannot rebase: {rebasing.reason}")
    if frame.empty or not rebasing.is_material:
        return frame
    out = frame.copy()
    columns = [c for c in ("open", "high", "low", "close") if c in out.columns]
    out.loc[:, columns] *= rebasing.factor
    return out


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


@dataclass
class RecoveryReport:
    """What one symbol/timeframe recovery found. Evidence, not a verdict."""

    symbol: str
    timeframe: str
    stored_last: datetime | None = None
    recovered_first: datetime | None = None
    recovered_last: datetime | None = None
    candles: int = 0
    sessions_recovered: tuple[date, ...] = ()
    sessions_expected: tuple[date, ...] = ()
    sessions_missing: tuple[date, ...] = ()
    sessions_unreachable: tuple[date, ...] = ()
    thin_sessions: dict[date, int] = field(default_factory=dict)
    duplicates_declined: int = 0
    out_of_session_dropped: int = 0
    invalid_dropped: int = 0
    rebasing: Rebasing | None = None
    alignment: Alignment | None = None
    join_ratio: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def recovered_anything(self) -> bool:
        return self.candles > 0

    def render(self) -> str:
        """One symbol's outcome, written for someone deciding whether to keep it."""
        head = f"{self.symbol:<18} {self.timeframe:<4}"
        if not self.recovered_anything:
            return f"{head} nothing recovered" + (
                f" - {self.problems[0]}" if self.problems else ""
            )
        span = ""
        if self.recovered_first and self.recovered_last:
            span = (
                f"{self.recovered_first.astimezone(IST).date()} -> "
                f"{self.recovered_last.astimezone(IST).date()}"
            )
        parts = [
            f"{head} {self.candles:>6} candles  {span}",
            f"      {len(self.sessions_recovered)} session(s) recovered",
        ]
        if self.rebasing:
            parts.append(f"      rebasing {self.rebasing.describe()}")
        if self.sessions_missing:
            parts.append(
                f"      {len(self.sessions_missing)} expected session(s) absent "
                f"from the feed: {_compact_dates(self.sessions_missing)}"
            )
        if self.sessions_unreachable:
            parts.append(
                f"      {len(self.sessions_unreachable)} session(s) already past "
                f"the provider's window: {_compact_dates(self.sessions_unreachable)}"
            )
        if self.thin_sessions:
            worst = sorted(self.thin_sessions.items(), key=lambda kv: kv[1])[:3]
            parts.append(
                f"      {len(self.thin_sessions)} thin session(s), e.g. "
                + ", ".join(f"{d} ({n} candles)" for d, n in worst)
            )
        if self.duplicates_declined:
            parts.append(
                f"      {self.duplicates_declined} candle(s) the store already "
                "holds were declined, not overwritten"
            )
        if self.out_of_session_dropped:
            parts.append(
                f"      {self.out_of_session_dropped} candle(s) outside "
                "09:15-15:30 IST dropped"
            )
        if self.invalid_dropped:
            parts.append(
                f"      {self.invalid_dropped} structurally impossible "
                "candle(s) dropped"
            )
        if self.join_ratio is not None and abs(self.join_ratio - 1.0) > JOIN_BREAK_RATIO:
            parts.append(
                f"      WARNING: {(self.join_ratio - 1.0) * 100:+.1f}% step "
                "across the join after rebasing - check for a corporate "
                "action inside the gap"
            )
        for problem in self.problems:
            parts.append(f"      PROBLEM: {problem}")
        return "\n".join(parts)


def _compact_dates(days: Sequence[date], limit: int = 6) -> str:
    shown = ", ".join(d.isoformat() for d in days[:limit])
    return shown if len(days) <= limit else f"{shown}, +{len(days) - limit} more"


# ---------------------------------------------------------------------------
# The recovery itself
# ---------------------------------------------------------------------------


def recover(
    symbol: str,
    timeframe: str,
    stored: pd.DataFrame,
    incoming: pd.DataFrame,
    *,
    expected_sessions: Sequence[date],
    earliest_available: date,
    invalid_timestamps: Sequence[datetime] = (),
) -> tuple[pd.DataFrame, RecoveryReport]:
    """Put `incoming` onto the stored basis and say exactly what it holds.

    Returns the candles safe to append and the evidence behind them. An empty
    frame with a populated report is a normal, useful outcome: it says the
    recovery was attempted and why nothing survived it.

    `invalid_timestamps` are candles the caller's own OHLC check rejected -
    passed in rather than re-derived so this module stays free of the
    duplicate sanity logic `data_quality.check_ohlc_sanity` already owns.
    """
    report = RecoveryReport(
        symbol=symbol,
        timeframe=timeframe,
        sessions_expected=tuple(expected_sessions),
        stored_last=(
            stored.index[-1].to_pydatetime() if not stored.empty else None
        ),
    )

    unreachable = [d for d in expected_sessions if d < earliest_available]
    report.sessions_unreachable = tuple(unreachable)

    if incoming.empty:
        report.problems.append("the provider returned no candles at all")
        return _empty_like(incoming), report

    inside = trim_to_session(incoming, timeframe)
    report.out_of_session_dropped = len(incoming) - len(inside)

    if invalid_timestamps:
        bad = set(invalid_timestamps)
        before = len(inside)
        inside = inside[~inside.index.isin(bad)]
        report.invalid_dropped = before - len(inside)

    alignment = check_alignment(stored, inside, timeframe)
    report.alignment = alignment
    if not alignment.ok:
        report.problems.append(alignment.note)
        return _empty_like(incoming), report
    if alignment.restamp_to is not None:
        inside = restamp_daily(inside, alignment.restamp_to)

    rebasing = measure_rebasing(stored, inside)
    report.rebasing = rebasing
    if not rebasing.ok:
        report.problems.append(rebasing.reason)
        return _empty_like(incoming), report

    rebased = apply_rebasing(inside, rebasing)

    # Everything at or before the last stored candle is declined rather than
    # merged: the store's own candles stay the record, and a second feed does
    # not get to restate a session a backtest has already run on.
    if report.stored_last is not None:
        fresh = rebased[rebased.index > report.stored_last]
        report.duplicates_declined = len(rebased) - len(fresh)
    else:
        fresh = rebased

    if fresh.empty:
        report.problems.append(
            "every candle the provider returned is already in the store"
        )
        return _empty_like(incoming), report

    report.candles = len(fresh)
    report.recovered_first = fresh.index[0].to_pydatetime()
    report.recovered_last = fresh.index[-1].to_pydatetime()

    counts = session_bar_counts(fresh)
    report.sessions_recovered = tuple(counts.index)

    recoverable = [
        d for d in expected_sessions
        if d >= earliest_available
        and (report.stored_last is None
             or d > report.stored_last.astimezone(IST).date())
    ]
    present = set(counts.index)
    report.sessions_missing = tuple(d for d in recoverable if d not in present)

    expected_bars = EXPECTED_BARS.get(timeframe, 1)
    floor = max(1, int(expected_bars * THIN_SESSION_RATIO))
    report.thin_sessions = {
        day: int(n) for day, n in counts.items() if int(n) < floor
    }

    report.join_ratio = _join_ratio(stored, fresh)
    return fresh, report


def _empty_like(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.iloc[0:0] if not frame.empty else frame


def _join_ratio(stored: pd.DataFrame, fresh: pd.DataFrame) -> float | None:
    """First recovered close over last stored close: the step at the seam.

    Measured AFTER rebasing, so a value away from 1.0 is a move the overlap
    did not explain - most likely a corporate action that fell inside the
    gap, where there are no shared sessions to measure it from.
    """
    if stored.empty or fresh.empty:
        return None
    last = float(stored["close"].iloc[-1])
    first = float(fresh["close"].iloc[0])
    if last <= 0 or first <= 0 or not (np.isfinite(last) and np.isfinite(first)):
        return None
    return first / last
