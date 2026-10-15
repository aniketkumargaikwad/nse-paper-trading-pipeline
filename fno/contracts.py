"""Which F&O contracts this platform researches, and their lot sizes.

Index derivatives only (design §0). Five underlyings are kept end-of-day for
positional research; two of them are also kept minute by minute (step 3).

LOT SIZES (design §2.3). NSE's file carries the lot only from 8 Jul 2024.
Before that it is read from the busiest future each day:

    lot ~= turnover / (contracts x close)

Average traded price differs from the close by a few percent at most, so
rounding to the nearest LOT_ROUNDING gives the true lot (NIFTY, 4 Jan 2016:
75.45 -> 75). Daily estimates collapse into the dates each lot took effect,
counting a change only once CONFIRM_DAYS sessions agree, so one thin expiry
day cannot invent a lot. The result is reviewed against NSE's circulars and
committed as lot_sizes.yaml - from then on read, never re-derived.

Caveat, stated rather than hidden: a revision applies to NEW contracts, so
for a few weeks after a change a far-month contract may still trade in the
old lot. A table keyed by date gives the near-month lot. From 8 Jul 2024
every row carries its own lot, which the engine prefers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pandas as pd
import yaml

INDEX_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
MINUTE_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY")

# Listed before 2016 and traded every session since, so a day's file without
# them is a fault rather than history. FINNIFTY (2021), MIDCPNIFTY (2022) and
# NIFTYNXT50 (2024) started later.
ALWAYS_LISTED: tuple[str, ...] = ("NIFTY", "BANKNIFTY")

LOT_ROUNDING = 5
CONFIRM_DAYS = 3
LOT_SIZES_FILE = Path(__file__).with_name("lot_sizes.yaml")


class LotSizeError(LookupError):
    """No lot size is known for that underlying on that date."""


def estimate_lot_sizes(daily: pd.DataFrame) -> pd.DataFrame:
    """Per underlying and day: the lot, from NSE's own column or the busiest future."""
    fut = daily[(daily["instrument"] == "FUT") & (daily["volume_contracts"] > 0)
                & (daily["close"] > 0)]
    busiest = fut.loc[fut.groupby(["underlying", "trade_date"])["volume_contracts"].idxmax()]
    raw = busiest["turnover_inr"] / (busiest["volume_contracts"] * busiest["close"])
    derived = (raw / LOT_ROUNDING).round().astype("int64") * LOT_ROUNDING
    published = busiest["lot_size"]
    out = pd.DataFrame({
        "underlying": busiest["underlying"],
        "trade_date": busiest["trade_date"],
        "lot_size": published.where(published.notna(), derived).astype("int64"),
        "derived": derived,
        "raw_estimate": raw.round(2),
        "source": published.notna().map({True: "nse", False: "derived"}),
    })
    return out.sort_values(["underlying", "trade_date"]).reset_index(drop=True)


def change_points(estimates: pd.DataFrame, *,
                  confirm_days: int = CONFIRM_DAYS) -> dict[str, list[tuple[date, int]]]:
    """The dates each lot took effect, once `confirm_days` sessions in a row agree."""
    out: dict[str, list[tuple[date, int]]] = {}
    for underlying, rows in estimates.sort_values("trade_date").groupby("underlying"):
        changes: list[tuple[date, int]] = []
        current = candidate = None
        start = None
        run = 0
        for day, lot in zip(rows["trade_date"], rows["lot_size"]):
            lot = int(lot)
            if lot == current:
                candidate, run = None, 0
                continue
            if lot == candidate:
                run += 1
            else:
                candidate, run, start = lot, 1, day
            if run >= confirm_days:
                changes.append((start, lot))
                current, candidate, run = lot, None, 0
        out[underlying] = changes
    return out


@dataclass(frozen=True)
class LotSizes:
    """The lot in force for an underlying on a date."""

    changes: Mapping[str, tuple[tuple[date, int], ...]]

    def on(self, underlying: str, day: date) -> int:
        seq = self.changes.get(underlying)
        if not seq:
            raise LotSizeError(f"no lot sizes recorded for {underlying}")
        lot = None
        for start, size in seq:
            if start <= day:
                lot = size
        if lot is None:
            raise LotSizeError(f"{underlying}: no lot size known before {seq[0][0]} (asked {day})")
        return lot

    def to_yaml(self) -> str:
        data = {u: [{"from": s.isoformat(), "lot": n} for s, n in seq]
                for u, seq in self.changes.items()}
        return yaml.safe_dump(data, sort_keys=True)

    @classmethod
    def from_yaml(cls, text: str) -> "LotSizes":
        data = yaml.safe_load(text) or {}
        return cls({u: tuple((date.fromisoformat(str(r["from"])), int(r["lot"])) for r in rows)
                    for u, rows in data.items()})

    @classmethod
    def load(cls, path: Path = LOT_SIZES_FILE) -> "LotSizes":
        return cls.from_yaml(Path(path).read_text(encoding="utf-8"))
