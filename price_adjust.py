"""Putting intraday candles onto the same price basis as the daily feed.

THE PROBLEM
-----------
Dhan's daily feed is adjusted for corporate actions; its intraday feed is raw.
So EICHERMOT's 5-minute candles say 21,780 on 2020-08-21 and 2,178 the next
morning — a 90% collapse that never happened, it was a 1:10 split. Twenty-two
of the fifty NIFTY 50 symbols carry at least one of these.

A backtest spanning one does not error and does not look odd. A breakout rule
simply finds the strongest signal in its entire sample and builds a result on
it.

WHY NOT JUST FIX THE SPLITS
---------------------------
Because "the splits" is not the whole set. WIPRO's ratio against the daily
feed is 2.667 before its 2017 bonus and 1.332 AFTER — there is a second
adjustment in 2019 that the split detector never flagged, because the
detector only looks for jumps above 1.5x.

The general fact is simpler than any list of events: the daily feed is
adjusted to TODAY's basis, so the gap between the two feeds on any past day
IS the cumulative adjustment still owed to that day. Measure the gap and the
events take care of themselves — splits, bonuses, demergers, and whatever
else the daily feed accounts for.

WHY THE GAP CANNOT BE USED DAY BY DAY
-------------------------------------
The intraday "close" is the last 5-minute candle; the daily close is the
official closing price, which includes the closing auction. They differ by a
few basis points every day — measured on clean symbols, up to 1.1%, with the
95th percentile around 0.5%.

So the factor is taken as PIECEWISE CONSTANT: it only truly changes at a
corporate action, and between them the median of the daily gaps is a far
better estimate than any single day. A step is recognised only when the gap
moves further than daily noise could explain.

NOTHING IS OVERWRITTEN
----------------------
These factors are applied when candles are READ, not written into the store.
The raw feed stays exactly as Dhan sent it, the correction is a row anybody
can look at, and removing the row undoes it. That matters because a corrected
price is indistinguishable from a real one — so the correction has to be
somewhere you can see it, not baked into the data.

Pure module: no I/O, no database. The caller supplies the two series.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

import numpy as np
import pandas as pd

# A day-to-day move in the ratio bigger than this is treated as a corporate
# action rather than noise. Measured against clean symbols, ordinary
# closing-auction noise reaches about 1.1% at its worst, so 2% clears it while
# still catching the smallest real event seen (WIPRO 2020, x1.023).
STEP_THRESHOLD = 0.02

# Segments whose price factor is within this of 1.0 need no adjustment.
# Set above the ~1.1% day-to-day noise measured on clean symbols: below that,
# a "correction" is just noise being written into every candle.
NEGLIGIBLE = 0.02

# A segment shorter than this is more likely two events close together being
# mis-split than a real plateau, and its median is not trustworthy.
MIN_SEGMENT_DAYS = 3


@dataclass(frozen=True)
class Adjustment:
    """One period during which intraday prices need rescaling.

    `price_factor` multiplies open/high/low/close.

    VOLUME IS MEASURED AND DELIBERATELY NOT APPLIED
    -----------------------------------------------
    A split really does change volume — 1:10 means a tenth of the price and
    ten times the shares — so adjusting it looks obviously right. It is not,
    and this was nearly shipped.

    Measured on the real data: RELIANCE's volume gap against the daily feed
    is 0.50 across the nine-year history but 1.01 over the last year, while
    its PRICE gap is 1.00 throughout. Nothing happened to the share count.
    The two feeds simply changed how they count volume partway through, and
    several symbols show the same shape (NESTLEIND 0.05, KOTAKBANK 0.20,
    DRREDDY 0.20).

    Segments here are cut at PRICE steps, so a volume-only change lands in the
    middle of a segment and poisons its median. Applying that would have
    multiplied nine years of RELIANCE volume by two, for no reason at all.

    `volume_factor` is kept because it is genuine evidence about what kind of
    event this was — a split moves it, a demerger does not — but nothing
    multiplies by it. Volume-based rules therefore remain wrong across a
    split boundary; that is a known, stated limit rather than a silent one.
    """

    effective_from: date          # inclusive
    effective_to: date            # inclusive
    price_factor: float
    volume_factor: float          # evidence only — never applied, see above
    sample_days: int

    @property
    def is_material(self) -> bool:
        """Worth correcting at all.

        Judged on PRICE alone. The threshold sits above ordinary
        closing-auction noise, which reaches about 1.1% day to day, so a
        correction is only made where something real moved the price.
        """
        return abs(self.price_factor - 1.0) > NEGLIGIBLE


def daily_ratio(
    intraday_last_close: pd.Series, adjusted_daily_close: pd.Series
) -> pd.Series:
    """How much each day's intraday prices must be multiplied by.

    Both series are indexed by date. Only days present in both are returned,
    because a ratio needs both halves.
    """
    common = intraday_last_close.index.intersection(adjusted_daily_close.index)
    if len(common) == 0:
        return pd.Series(dtype="float64")
    intra = intraday_last_close[common].astype(float)
    daily = adjusted_daily_close[common].astype(float)
    usable = (intra > 0) & (daily > 0)
    return (daily[usable] / intra[usable]).sort_index()


def find_segments(
    price_ratio: pd.Series,
    volume_ratio: pd.Series | None = None,
    *,
    threshold: float = STEP_THRESHOLD,
) -> list[Adjustment]:
    """Split the ratio history into constant periods and measure each one.

    Steps are found in the PRICE ratio only. Volume is measured over the same
    periods rather than segmented separately: a corporate action moves both at
    once, and letting them disagree about when a period starts would produce
    a day where price is adjusted and volume is not.
    """
    if price_ratio.empty:
        return []

    ratio = price_ratio.sort_index()
    change = (ratio / ratio.shift(1)) - 1.0
    breaks = [i for i, c in enumerate(change.to_numpy()) if abs(c) > threshold]

    bounds = [0, *breaks, len(ratio)]
    segments: list[Adjustment] = []
    for start, end in zip(bounds, bounds[1:]):
        if end <= start:
            continue
        window = ratio.iloc[start:end]
        if len(window) < MIN_SEGMENT_DAYS:
            continue
        price_factor = float(np.median(window.to_numpy()))

        volume_factor = 1.0
        if volume_ratio is not None and not volume_ratio.empty:
            overlap = volume_ratio.index.intersection(window.index)
            if len(overlap) >= MIN_SEGMENT_DAYS:
                volume_factor = float(np.median(volume_ratio[overlap].to_numpy()))

        segments.append(Adjustment(
            effective_from=window.index[0],
            effective_to=window.index[-1],
            price_factor=price_factor,
            volume_factor=volume_factor,
            sample_days=len(window),
        ))
    return _merge_similar(segments)


def _merge_similar(
    segments: Sequence[Adjustment], tolerance: float = NEGLIGIBLE
) -> list[Adjustment]:
    """Join neighbouring periods that agree about the factor.

    A noisy symbol trips the step test repeatedly without anything actually
    happening: BHARTIARTL produced twenty-three periods all hovering around
    0.90, where the truth is about three. Each fragment then gets its own
    slightly different median, so the correction itself introduces small
    jumps — the exact artefact this code exists to remove.

    Neighbours within `tolerance` of each other are therefore treated as one
    period and re-measured across the whole of it.
    """
    if not segments:
        return []

    merged: list[Adjustment] = [segments[0]]
    for nxt in segments[1:]:
        last = merged[-1]
        if abs(nxt.price_factor - last.price_factor) <= tolerance * last.price_factor:
            weight = last.sample_days + nxt.sample_days
            merged[-1] = Adjustment(
                effective_from=last.effective_from,
                effective_to=nxt.effective_to,
                # Weighted by how many days each contributed, so a three-day
                # fragment cannot outvote a two-year plateau.
                price_factor=(last.price_factor * last.sample_days
                              + nxt.price_factor * nxt.sample_days) / weight,
                volume_factor=(last.volume_factor * last.sample_days
                               + nxt.volume_factor * nxt.sample_days) / weight,
                sample_days=weight,
            )
        else:
            merged.append(nxt)
    return merged


def material_adjustments(segments: Sequence[Adjustment]) -> list[Adjustment]:
    """Only the periods actually worth correcting.

    The most recent period is normally 1.0 — the daily feed is adjusted to
    today, so there is nothing left owing — and adjusting by 1.0 would only
    add rounding noise to every candle for no benefit.
    """
    return [a for a in segments if a.is_material]


def apply_adjustments(
    candles: pd.DataFrame, adjustments: Sequence[Adjustment]
) -> pd.DataFrame:
    """Return `candles` rescaled onto the adjusted basis.

    A copy is returned, never a mutation: the caller's frame may be a cached
    read that other code is also holding.

    Candles outside every adjustment period are left exactly as they are, so
    a symbol with no corporate actions is returned untouched rather than
    multiplied by 1.0 and rounded.
    """
    if candles.empty or not adjustments:
        return candles

    out = candles.copy()
    days = out.index.tz_convert("Asia/Kolkata").date
    price_columns = [c for c in ("open", "high", "low", "close") if c in out.columns]

    for adjustment in adjustments:
        if not adjustment.is_material:
            continue
        mask = (days >= adjustment.effective_from) & (days <= adjustment.effective_to)
        if not mask.any():
            continue
        # Price only. See the note on Adjustment for why volume is measured
        # but never multiplied.
        out.loc[mask, price_columns] *= adjustment.price_factor
    return out
