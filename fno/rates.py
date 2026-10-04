"""The risk-free rate for a date: an approximate yearly 91-day T-bill average."""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from pathlib import Path

import yaml

RATES_FILE = Path(__file__).with_name("rates.yaml")


@lru_cache(maxsize=1)
def _table() -> dict[int, float]:
    return {int(k): float(v) for k, v in yaml.safe_load(RATES_FILE.read_text(encoding="utf-8")).items()}


def risk_free(day: date) -> float:
    try:
        return _table()[day.year]
    except KeyError:
        raise LookupError(f"no risk-free rate for {day.year}; add it to {RATES_FILE.name}") from None
