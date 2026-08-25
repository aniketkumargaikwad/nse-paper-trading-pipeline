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

Detection limits
----------------
The 1.5 ratio threshold catches every stock split and any bonus issue of 1:2
or richer. It deliberately MISSES smaller bonuses (1:4 -> ratio 1.25,
1:10 -> ratio 1.10), which are real but small price discontinuities. Indian
equities carry 2-20% circuit limits, so a 50% overnight move is unreachable
by ordinary trading - that is why the threshold can sit this low without
drowning in false positives.

Pure module: no I/O, no network, no database, no clock reads.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from config import IST, MARKET_OPEN_IST, UTC

# ---------------------------------------------------------------------------
# Split detection tuning
# ---------------------------------------------------------------------------

# An overnight move beyond this ratio is implausible for a liquid equity and
# is more likely a corporate action. Deliberately loose: we would rather
# review a few real crashes than silently trade through an unadjusted split.
SPLIT_RATIO_THRESHOLD = 1.5

# How closely the adjusted daily series must agree with the intraday move for
# it to count as a genuine price move rather than a split artefact.
# RELATIVE, not absolute: `ratio` is unbounded above, so a fixed window would
# demand ever-tighter agreement as the move grows (+/-12.5% at ratio 2 but
# only +/-2.5% at ratio 10), false-flagging genuine large moves that the two
# feeds happen to price a few percent apart.
ADJUSTED_AGREEMENT_TOLERANCE = 0.25


class DataQualityError(ValueError):
    """Raised when a flag cannot be safely serialised (e.g. a naive
    timestamp, which would be silently read as the machine's local zone)."""


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
        if self.ts.tzinfo is None:
            raise DataQualityError(
                "QualityFlag.ts must be timezone-aware; a naive timestamp "
                "would be silently read as the machine's local zone."
            )
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
    flag: a candle whose high is below its close never existed.

    Vectorised deliberately: this runs on every fetched page, and a per-row
    loop measured ~370x slower (hours versus minutes across a full backfill).
    """
    flags: list[QualityFlag] = []
    if df.empty:
        return flags

    o = df["open"].to_numpy(dtype=float)
    h = df["high"].to_numpy(dtype=float)
    lo = df["low"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    v = df["volume"].to_numpy(dtype=float)

    hi_oc, lo_oc = np.maximum(o, c), np.minimum(o, c)

    # NaN/inf MUST be tested first and explicitly. Every comparison against
    # NaN is False, so an unfinite value would otherwise slip through all the
    # other checks - and Postgres will not stop it either, because it orders
    # NaN above every real number, so `check (close > 0)` accepts it.
    bad_nan = ~(np.isfinite(o) & np.isfinite(h) & np.isfinite(lo)
                & np.isfinite(c) & np.isfinite(v))
    bad_high = h < hi_oc
    bad_low = lo > lo_oc
    bad_price = np.minimum(np.minimum(o, h), np.minimum(lo, c)) <= 0
    bad_vol = v < 0

    suspect = bad_nan | bad_high | bad_low | bad_price | bad_vol
    if not suspect.any():
        return flags

    # Details are materialised only for failing rows, normally a tiny fraction.
    for i in np.flatnonzero(suspect):
        if bad_nan[i]:
            reason = "a price or volume is NaN or infinite"
        elif bad_high[i]:
            reason = f"high ({h[i]}) is below max(open, close) ({hi_oc[i]})"
        elif bad_low[i]:
            reason = f"low ({lo[i]}) is above min(open, close) ({lo_oc[i]})"
        elif bad_price[i]:
            reason = "a price is zero or negative"
        else:
            reason = f"volume is negative ({v[i]})"
        flags.append(QualityFlag(
            flag_type="ohlc_invalid",
            ts=df.index[i].to_pydatetime(),
            detail={"reason": reason, "open": float(o[i]), "high": float(h[i]),
                    "low": float(lo[i]), "close": float(c[i]), "volume": float(v[i])},
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
        if not math.isfinite(close) or close <= 0:
            continue  # unusable session: keep the last good close as anchor
        if previous_close is not None:
            ratio = max(close / previous_close, previous_close / close)
            if ratio >= SPLIT_RATIO_THRESHOLD:
                prev_adj = daily_by_date.get(previous_date)
                curr_adj = daily_by_date.get(day)
                corroborated = False
                # Distinguishing "the adjusted series disagreed" from "there
                # was no adjusted series" matters more than it looks. The
                # first is strong evidence of an unadjusted corporate action;
                # the second is no evidence at all. Reporting both as
                # corroborated=false made a confirmed defect and an unchecked
                # one indistinguishable in the flags table.
                adj_ratio: float | None = None
                if prev_adj and curr_adj and prev_adj > 0 and curr_adj > 0:
                    adj_ratio = max(curr_adj / prev_adj, prev_adj / curr_adj)
                    corroborated = (
                        abs(adj_ratio - ratio) <= ADJUSTED_AGREEMENT_TOLERANCE * ratio
                    )
                if not corroborated:
                    flags.append(QualityFlag(
                        flag_type="suspected_split",
                        ts=datetime.combine(day, MARKET_OPEN_IST, tzinfo=IST).astimezone(UTC),
                        detail={
                            "ratio": round(ratio, 4),
                            "previous_close": previous_close,
                            "close": close,
                            "previous_date": previous_date.isoformat() if previous_date else None,
                            "corroborated_by_adjusted_daily": corroborated,
                            "adjusted_daily_checked": adj_ratio is not None,
                            "adjusted_ratio": (
                                round(adj_ratio, 4) if adj_ratio is not None else None
                            ),
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
                ts=datetime.combine(day, MARKET_OPEN_IST, tzinfo=IST).astimezone(UTC),
                detail={"missing_date": day.isoformat()},
            ))
    return flags


# ---------------------------------------------------------------------------
# Warning a run about known-bad candles
# ---------------------------------------------------------------------------


def splits_inside_window(
    flags: Sequence[Mapping[str, Any]],
    symbols: Sequence[str],
    from_utc: datetime,
    to_utc: datetime,
) -> list[dict[str, Any]]:
    """Confirmed splits that fall inside a backtest's window.

    WHY A BACKTEST MUST BE TOLD
    ---------------------------
    An unadjusted split is a 50% overnight collapse that never happened. A
    strategy spanning one does not error, does not look odd, and does not
    report anything unusual — it simply finds the strongest breakout signal in
    its entire sample and builds a result on it.

    Nine years of 5-minute data contains eight of these across the NIFTY 50.
    They are 4.5% of all candles, but 91% of TMPV's and 36% of EICHERMOT's, so
    "a small fraction overall" is exactly the wrong way to think about it.

    Only CONFIRMED splits are returned — ones where the adjusted daily feed
    was consulted and disagreed. An unchecked flag means nobody looked yet,
    which is a different statement and not grounds for a warning.
    """
    wanted = set(symbols)
    found: list[dict[str, Any]] = []
    for flag in flags:
        if flag.get("flag_type") != "suspected_split":
            continue
        symbol = flag.get("symbol")
        if symbol not in wanted:
            continue
        detail = flag.get("detail") or {}
        if not detail.get("adjusted_daily_checked"):
            continue
        ts = flag.get("ts")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if ts is None or not (from_utc <= ts <= to_utc):
            continue
        found.append({
            "symbol": symbol,
            "when": ts,
            "ratio": detail.get("ratio"),
            "previous_close": detail.get("previous_close"),
            "close": detail.get("close"),
        })
    return sorted(found, key=lambda f: (f["symbol"], f["when"]))


def describe_split_warning(found: Sequence[Mapping[str, Any]]) -> str:
    """The warning text a run prints. Empty string when there is nothing wrong."""
    if not found:
        return ""
    lines = [
        f"WARNING: {len(found)} confirmed unadjusted corporate action(s) fall "
        "inside this window.",
        "  Prices before these dates are on a different basis, so the candle "
        "across each one shows a",
        "  crash that never happened. A breakout or momentum rule will read it "
        "as its best-ever signal.",
    ]
    for f in found:
        when = f["when"].astimezone(IST).date() if hasattr(f["when"], "astimezone") else f["when"]
        lines.append(
            f"    {f['symbol']:<16} {when}  "
            f"{f.get('previous_close')} -> {f.get('close')} (x{f.get('ratio')})"
        )
    lines.append(
        "  Either exclude these symbols, start the window after the date, or "
        "treat the result as unsound."
    )
    return "\n".join(lines)
