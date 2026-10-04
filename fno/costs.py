"""NSE F&O charges on a fill, at the rates in force on its date (design §4).

Every rate lives in charges.yaml with the date it took effect; sources in
docs/research/2026-10-04-fno-charges.md. STT on option premium tripled
between 2016 and 2026 - charging one rate across the history would flatter
option selling most in the most recent years.

Each leg is one order (one brokerage). Exercise at expiry pays STT on the
settlement value and no brokerage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

CHARGES_FILE = Path(__file__).with_name("charges.yaml")


@dataclass(frozen=True)
class Fill:
    day: date
    instrument: str          # "OPT" | "FUT"
    side: str                # "buy" | "sell"
    price: float             # per unit (option premium or future price)
    quantity: int            # units, i.e. lots x lot size


def _in_force(rows: list[dict[str, Any]], day: date) -> dict[str, Any]:
    chosen = None
    for row in rows:
        if date.fromisoformat(str(row["from"])) <= day:
            chosen = row
    if chosen is None:
        raise LookupError(f"no charges known before {rows[0]['from']} (asked {day})")
    return chosen


@dataclass(frozen=True)
class FnoCharges:
    table: dict[str, Any]

    @classmethod
    def load(cls, path: Path = CHARGES_FILE) -> "FnoCharges":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def stt_rate(self, instrument: str, day: date) -> float:
        row = _in_force(self.table["stt"], day)
        return float(row["option_sell_premium"] if instrument == "OPT" else row["future_sell"])

    def exchange_rate(self, instrument: str, day: date) -> float:
        row = _in_force(self.table["exchange"], day)
        return float(row["option_premium"] if instrument == "OPT" else row["future"])

    def gst_rate(self, day: date) -> float:
        return float(_in_force(self.table["gst"], day)["rate"])

    def fill(self, fill: Fill) -> float:
        """Rupees charged on one executed leg."""
        turnover = fill.price * fill.quantity
        brokerage = float(self.table["brokerage_per_order"])
        exchange = turnover * self.exchange_rate(fill.instrument, fill.day)
        sebi = turnover * float(self.table["sebi"])
        stt = turnover * self.stt_rate(fill.instrument, fill.day) if fill.side == "sell" else 0.0
        stamp_key = "option_premium" if fill.instrument == "OPT" else "future"
        stamp = (turnover * float(self.table["stamp_buy"][stamp_key])
                 if fill.side == "buy" else 0.0)
        gst = (brokerage + exchange + sebi) * self.gst_rate(fill.day)
        return brokerage + exchange + sebi + stt + stamp + gst

    def exercise(self, day: date, settlement_value: float) -> float:
        """STT on an in-the-money option exercised at expiry, on its settlement value."""
        return settlement_value * float(_in_force(self.table["stt"], day)["option_exercise"])
