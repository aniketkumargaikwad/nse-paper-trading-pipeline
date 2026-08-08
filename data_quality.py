"""Candle quality checks. Findings are FLAGGED for review, never auto-fixed.

A silently "corrected" price is indistinguishable from real data and would
quietly invalidate every result built on it. So each check returns flags; the
caller records them and (except for structurally impossible candles) keeps the
data as received.

Three checks
------------
* OHLC sanity      - structurally impossible candles. The caller DROPS these.
* Suspected splits - an overnight jump in intraday data that is ABSENT from
                     Dhan's corporate-action-adjusted daily series. The
                     disagreement between the two feeds is what makes this
                     detectable without a separate corporate-actions source.
* Session gaps     - an expected trading day with no candles at all.

Pure module: no I/O, no network, no database, no clock reads.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Sequence

import pandas as pd

from config import IST, UTC

# ---------------------------------------------------------------------------
# Split detection tuning
# ---------------------------------------------------------------------------

# An overnight move beyond this ratio is implausible for a liquid equity and
# is more likely a corporate action. Deliberately loose: we would rather
# review a few real crashes than silently trade through an unadjusted split.
SPLIT_RATIO_THRESHOLD = 1.5

# How closely the adjusted daily series must agree with the intraday move for
# it to count as a genuine price move rather than a split artefact.
ADJUSTED_AGREEMENT_TOLERANCE = 0.25


class DataQualityError(ValueError):
    """Raised when a frame cannot be checked (e.g. missing required columns)."""


# ---------------------------------------------------------------------------
# Flag type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QualityFlag:
    """One detected problem, ready to be written to data_quality_flags."""

    flag_type: str            # 'suspected_split' | 'session_gap' | 'ohlc_invalid'
    ts: datetime
    detail: dict[str, Any] = field(default_factory=dict)

    def to_row(self, instrument_id: int, timeframe: str) -> dict[str, Any]:
        """Shape this flag as a data_quality_flags row."""
        return {
            "instrument_id": instrument_id,
            "timeframe": timeframe,
            "flag_type": self.flag_type,
            "ts": self.ts.astimezone(UTC).isoformat(),
            "detail": self.detail,
        }


# ---------------------------------------------------------------------------
# OHLC sanity
# ---------------------------------------------------------------------------


def check_ohlc_sanity(df: pd.DataFrame) -> list[QualityFlag]:
    """Find structurally impossible candles.

    These are the only findings the caller should DROP rather than merely
    flag: a candle whose high is below its close (or similar) never existed.
    """
    flags: list[QualityFlag] = []
    if df.empty:
        return flags

    for ts, row in df.iterrows():
        o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        v = float(row["volume"])
        reason: str | None = None
        if h < max(o, c):
            reason = f"high ({h}) is below max(open, close) ({max(o, c)})"
        elif l > min(o, c):
            reason = f"low ({l}) is above min(open, close) ({min(o, c)})"
        elif min(o, h, l, c) <= 0:
            reason = "a price is zero or negative"
        elif v < 0:
            reason = f"volume is negative ({v})"
        if reason:
            flags.append(QualityFlag(
                flag_type="ohlc_invalid",
                ts=ts.to_pydatetime(),
                detail={"reason": reason, "open": o, "high": h, "low": l,
                        "close": c, "volume": v},
            ))
    return flags


# ---------------------------------------------------------------------------
# Suspected splits
# ---------------------------------------------------------------------------


def detect_suspected_splits(
    intraday: pd.DataFrame, adjusted_daily: pd.DataFrame
) -> list[QualityFlag]:
    """Flag OVERNIGHT jumps that the ADJUSTED daily series does not corroborate.

    Dhan's daily feed is corporate-action adjusted; its intraday feed is not
    documented as adjusted. So a 1:5 split shows up intraday as an 80% crash
    while the daily series shows no such move. That disagreement is the signal.

    An overnight move present in BOTH series is a genuine price move and is
    left alone. Moves WITHIN a single session are never splits and are ignored.

    `intraday` needs only a `close` column (plus a tz-aware DatetimeIndex);
    full OHLCV is not required for this check.
    """
    flags: list[QualityFlag] = []
    if intraday.empty or len(intraday) < 2:
        return flags

    ist_dates = intraday.index.tz_convert(IST).date
    closes = intraday["close"].astype(float)

    # Last close of each session, in chronological order.
    session_last = (
        pd.DataFrame({"date": ist_dates, "close": closes.to_numpy()})
        .groupby("date", sort=True)["close"].last()
    )

    if len(session_last) < 2:
        return flags

    daily_by_date: dict[date, float] = {}
    if not adjusted_daily.empty:
        daily_by_date = {
            ts.astimezone(IST).date(): float(close)
            for ts, close in zip(adjusted_daily.index, adjusted_daily["close"])
        }

    previous_date: date | None = None
    previous_close: float | None = None
    for day, close in session_last.items():
        close = float(close)
        if previous_close is not None and previous_close > 0 and close > 0:
            ratio = max(close / previous_close, previous_close / close)
            if ratio >= SPLIT_RATIO_THRESHOLD:
                prev_adj = daily_by_date.get(previous_date)
                curr_adj = daily_by_date.get(day)
                corroborated = False
                if prev_adj and curr_adj and prev_adj > 0 and curr_adj > 0:
                    adj_ratio = max(curr_adj / prev_adj, prev_adj / curr_adj)
                    corroborated = abs(adj_ratio - ratio) <= ADJUSTED_AGREEMENT_TOLERANCE
                if not corroborated:
                    flags.append(QualityFlag(
                        flag_type="suspected_split",
                        ts=datetime.combine(day, datetime.min.time(), tzinfo=IST).astimezone(UTC),
                        detail={
                            "ratio": round(ratio, 4),
                            "previous_close": previous_close,
                            "close": close,
                            "previous_date": previous_date.isoformat() if previous_date else None,
                            "corroborated_by_adjusted_daily": corroborated,
                        },
                    ))
        previous_date, previous_close = day, close
    return flags


# ---------------------------------------------------------------------------
# Session gaps
# ---------------------------------------------------------------------------


def detect_session_gaps(
    df: pd.DataFrame, expected_days: Sequence[date]
) -> list[QualityFlag]:
    """Flag expected trading days that have no candles at all."""
    flags: list[QualityFlag] = []
    present = set(df.index.tz_convert(IST).date) if not df.empty else set()
    for day in expected_days:
        if day not in present:
            flags.append(QualityFlag(
                flag_type="session_gap",
                ts=datetime.combine(day, datetime.min.time(), tzinfo=IST).astimezone(UTC),
                detail={"missing_date": day.isoformat()},
            ))
    return flags
