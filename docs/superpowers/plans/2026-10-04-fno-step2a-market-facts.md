# F&O Step 2a — Market Facts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything a strategy may read about the option market on a day, computed only from that day's data: dated F&O charges, implied volatility and Greeks for every contract, the chain as it stood, and a daily facts table per underlying (ATM IV, IV rank, PCR, max pain, skew, expected move, basis, VIX, events, FII positioning).

**Architecture:** Pure functions over the step-1 store. `costs` prices a fill from `charges.yaml`; `pricing` is vectorised Black-76; `chain` turns one day's rows into a `ChainSnapshot` with IV and Greeks; `features` reduces a snapshot to one row of facts; `scripts/fno_features.py` writes `data/fno/features/<UNDERLYING>.parquet`. Step 2b's engine reads these.

**Tech Stack:** Python 3.11, numpy, pandas, pyarrow, PyYAML, yfinance, pytest. No SciPy: the normal CDF uses Abramowitz–Stegun 7.1.26 (error < 1.5e-7, a fraction of a paisa on an index option).

**Spec:** `docs/superpowers/specs/2026-10-02-fno-options-design.md` §2.1, §2.4, §2.5, §4 (charges). Charges sources: `docs/research/2026-10-04-fno-charges.md`.

**Conventions (from step 1):** run with `.venv/Scripts/python.exe -m pytest ...`; LF line endings (edit with the editor or `write_bytes`); tests import shared fixtures as `from test_fno_nse_files import OLD, UDIFF`.

**Decisions made while planning:**
- IV uses each contract's **settlement price** (always present; for an untraded contract it is NSE's theoretical price), so the surface is complete every day. Fills in step 2b use traded prices only.
- Time to expiry is calendar days / 365 from the trade date's close to the expiry's close; on expiry day it is 0 and IV is undefined (NaN).
- The forward for an expiry is that expiry's future settlement if one exists, else put–call parity at the strike where |call − put| is smallest: `F = K + e^{rT}(C − P)`.
- Delta is Black-76 forward delta. "20-delta" means the same thing everywhere in this system.
- The rate only discounts. `rates.yaml` holds approximate yearly 91-day T-bill averages; a one-point error moves a 30-day ATM option by under 0.1%, which the file says.
- Spot: Yahoo daily for NIFTY (`^NSEI`), BANKNIFTY (`^NSEBANK`), NIFTYNXT50 (`^NSMIDCP`) — verified 4 Oct 2026 back to 2007. FINNIFTY and MIDCPNIFTY have no usable Yahoo history: their spot is NSE's `underlying_price` (from 8 Jul 2024) or NaN, and their facts carry `spot_source`.
- The facts table's "near" expiry is the nearest with at least one day to run.

---

## File structure

| File | Responsibility |
|---|---|
| `fno/charges.yaml` | Dated STT, exchange, SEBI, stamp, GST, brokerage |
| `fno/costs.py` | `FnoCharges`: charges on a fill and on exercise |
| `fno/rates.yaml`, `fno/rates.py` | Risk-free rate for a date |
| `fno/pricing.py` | Normal CDF/PDF, Black-76 price, implied vol, Greeks — vectorised |
| `fno/yahoo.py` | Shared Yahoo daily fetch + normalise (VIX moves onto it) |
| `fno/spot.py` | Index daily closes for the three Yahoo underlyings |
| `fno/chain.py` | Expiries on a day, the forward, `ChainSnapshot`, `snapshot()`, `chain_at()` |
| `fno/events.yaml`, `fno/events.py` | Budget, RBI policy and election-result days |
| `fno/features.py` | One snapshot → one row of facts; a daily table with VIX, IV rank, events, FII |
| `scripts/fno_features.py` | Build `data/fno/features/<UNDERLYING>.parquet` |
| `tests/test_fno_costs.py`, `test_fno_rates.py`, `test_fno_pricing.py`, `test_fno_spot.py`, `test_fno_chain.py`, `test_fno_events.py`, `test_fno_features.py` | One per module |

---

### Task 1: Dated charges

**Files:** Create `fno/charges.yaml`, `fno/costs.py`, `tests/test_fno_costs.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_costs.py`:

```python
"""NSE F&O charges on a fill, at the rates in force that day (design §4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.costs import FnoCharges, Fill  # noqa: E402

CHARGES = FnoCharges.load()


def test_stt_on_selling_an_option_tracks_each_change():
    assert CHARGES.stt_rate("OPT", date(2023, 3, 31)) == 0.0005
    assert CHARGES.stt_rate("OPT", date(2023, 4, 1)) == 0.000625
    assert CHARGES.stt_rate("OPT", date(2024, 10, 1)) == 0.001
    assert CHARGES.stt_rate("OPT", date(2026, 3, 31)) == 0.001
    assert CHARGES.stt_rate("OPT", date(2026, 4, 1)) == 0.0015


def test_futures_stt_tracks_each_change():
    assert CHARGES.stt_rate("FUT", date(2020, 1, 1)) == 0.0001
    assert CHARGES.stt_rate("FUT", date(2026, 4, 1)) == 0.0005


def test_one_sold_nifty_lot_on_30_sep_2026_worked_by_hand():
    """65 units at Rs 100: turnover 6,500.
    brokerage 20; exchange 6500 x 0.0003553 = 2.30945; SEBI 0.0065;
    STT 6500 x 0.0015 = 9.75; no stamp on a sale;
    GST 18% of (20 + 2.30945 + 0.0065) = 4.016919."""
    got = CHARGES.fill(Fill(date(2026, 9, 30), "OPT", "sell", 100.0, 65))
    assert got == pytest.approx(20 + 2.30945 + 0.0065 + 9.75 + 4.016919, abs=1e-6)


def test_buying_pays_stamp_and_no_stt():
    got = CHARGES.fill(Fill(date(2026, 9, 30), "OPT", "buy", 100.0, 65))
    gst = (20 + 2.30945 + 0.0065) * 0.18
    assert got == pytest.approx(20 + 2.30945 + 0.0065 + 6500 * 0.00003 + gst, abs=1e-6)


def test_gst_was_service_tax_before_july_2017():
    assert CHARGES.gst_rate(date(2017, 6, 30)) == 0.15
    assert CHARGES.gst_rate(date(2017, 7, 1)) == 0.18


def test_exercise_stt_is_on_the_settlement_value():
    assert CHARGES.exercise(date(2026, 3, 26), 10_000.0) == pytest.approx(12.5)
    assert CHARGES.exercise(date(2026, 4, 1), 10_000.0) == pytest.approx(15.0)


def test_a_date_before_the_table_is_refused():
    with pytest.raises(LookupError):
        CHARGES.stt_rate("OPT", date(2015, 12, 31))
```

- [ ] **Step 2: Run to see them fail** — `.venv/Scripts/python.exe -m pytest tests/test_fno_costs.py -q` → `ModuleNotFoundError: No module named 'fno.costs'`

- [ ] **Step 3: Write the table and the code**

`fno/charges.yaml`:

```yaml
# NSE F&O charges by effective date, as fractions. Each row applies from its
# date until the next. Sources and notes: docs/research/2026-10-04-fno-charges.md
stt:
  - {from: 2016-01-01, option_sell_premium: 0.0005, future_sell: 0.0001, option_exercise: 0.00125}
  - {from: 2023-04-01, option_sell_premium: 0.000625, future_sell: 0.000125, option_exercise: 0.00125}
  - {from: 2024-10-01, option_sell_premium: 0.001, future_sell: 0.0002, option_exercise: 0.00125}
  - {from: 2026-04-01, option_sell_premium: 0.0015, future_sell: 0.0005, option_exercise: 0.0015}
exchange:   # transaction charge + NSE IPFT, both sides
  - {from: 2016-01-01, option_premium: 0.0005, future: 0.000019}
  - {from: 2024-10-01, option_premium: 0.0003553, future: 0.0000183}
gst:
  - {from: 2016-01-01, rate: 0.15}
  - {from: 2017-07-01, rate: 0.18}
sebi: 0.000001
stamp_buy: {option_premium: 0.00003, future: 0.00002}
brokerage_per_order: 20.0
```

`fno/costs.py`:

```python
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
```

- [ ] **Step 4: Run** — expect `7 passed`.
- [ ] **Step 5: Commit** — `git add fno/charges.yaml fno/costs.py tests/test_fno_costs.py && git commit -m "feat(fno): NSE F&O charges at the rates in force on each date"`

---

### Task 2: The risk-free rate

**Files:** Create `fno/rates.yaml`, `fno/rates.py`, `tests/test_fno_rates.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_rates.py`:

```python
"""The rate used only to discount option prices (design §2.4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.rates import risk_free  # noqa: E402


def test_each_year_has_a_rate_between_two_and_ten_percent():
    for year in range(2016, 2027):
        assert 0.02 < risk_free(date(year, 6, 1)) < 0.10


def test_the_covid_years_are_lower_than_the_years_around_them():
    assert risk_free(date(2020, 6, 1)) < risk_free(date(2019, 6, 1))
    assert risk_free(date(2021, 6, 1)) < risk_free(date(2023, 6, 1))


def test_a_year_outside_the_table_is_refused():
    with pytest.raises(LookupError):
        risk_free(date(2015, 6, 1))
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Write the table and the loader**

`fno/rates.yaml`:

```yaml
# Approximate yearly average 91-day Treasury-bill yields, as fractions.
# Used ONLY to discount option prices; the forward comes from futures or
# put-call parity. A one-point error moves a 30-day ATM option by under 0.1%,
# so approximate yearly values are adequate. Refine from RBI's DBIE series if
# a finer figure is ever needed.
2016: 0.065
2017: 0.062
2018: 0.066
2019: 0.060
2020: 0.037
2021: 0.034
2022: 0.055
2023: 0.068
2024: 0.068
2025: 0.058
2026: 0.054
```

`fno/rates.py`:

```python
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
```

- [ ] **Step 4: Run** — expect `3 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): risk-free rate by year, for discounting option prices"`

---

### Task 3: Black-76 pricing

**Files:** Create `fno/pricing.py`, `tests/test_fno_pricing.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_pricing.py`:

```python
"""Black-76 prices, implied volatility and Greeks, vectorised."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import pricing  # noqa: E402


def test_the_normal_cdf_matches_math_erf_closely():
    xs = np.linspace(-6, 6, 241)
    exact = np.array([0.5 * (1 + math.erf(x / math.sqrt(2))) for x in xs])
    assert np.max(np.abs(pricing.norm_cdf(xs) - exact)) < 2e-7


def test_an_atm_call_at_zero_rate_matches_the_textbook_value():
    """F = K = 100, T = 1, sigma = 20%, r = 0: 100 x (2N(0.1) - 1) = 7.9656."""
    price = pricing.black76(np.array([100.0]), np.array([100.0]), np.array([1.0]),
                            np.array([0.2]), 0.0, np.array([True]))
    assert price[0] == pytest.approx(7.965567, abs=1e-5)


def test_put_call_parity_holds():
    F, K, T, s, r = 22000.0, 22500.0, 30 / 365, 0.14, 0.06
    c = pricing.black76(np.array([F]), np.array([K]), np.array([T]), np.array([s]), r, np.array([True]))[0]
    p = pricing.black76(np.array([F]), np.array([K]), np.array([T]), np.array([s]), r, np.array([False]))[0]
    assert c - p == pytest.approx(math.exp(-r * T) * (F - K), abs=1e-6)


def test_implied_vol_round_trips_across_strikes():
    F = np.full(5, 22000.0)
    K = np.array([20000.0, 21500.0, 22000.0, 22500.0, 24000.0])
    T = np.full(5, 20 / 365)
    sig = np.array([0.22, 0.16, 0.13, 0.12, 0.15])
    call = np.array([False, False, True, True, True])
    price = pricing.black76(F, K, T, sig, 0.065, call)
    assert np.allclose(pricing.implied_vol(price, F, K, T, 0.065, call), sig, atol=1e-5)


def test_a_price_below_intrinsic_has_no_implied_vol():
    iv = pricing.implied_vol(np.array([50.0]), np.array([22000.0]), np.array([21000.0]),
                             np.array([10 / 365]), 0.06, np.array([True]))
    assert np.isnan(iv[0])


def test_zero_time_has_no_implied_vol():
    iv = pricing.implied_vol(np.array([100.0]), np.array([22000.0]), np.array([22000.0]),
                             np.array([0.0]), 0.06, np.array([True]))
    assert np.isnan(iv[0])


def test_deltas_have_the_right_signs_and_sizes():
    F, K, T, s = np.array([22000.0] * 2), np.array([22000.0] * 2), np.array([30 / 365] * 2), np.array([0.13] * 2)
    g = pricing.greeks(F, K, T, s, 0.06, np.array([True, False]))
    assert 0.45 < g["delta"][0] < 0.55 and -0.55 < g["delta"][1] < -0.45
    assert g["gamma"][0] == pytest.approx(g["gamma"][1])
    assert g["vega"][0] > 0 and g["theta"][0] < 0
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Write the implementation**

`fno/pricing.py`:

```python
"""Black-76 prices, implied volatility and Greeks for European index options.

Black-76 prices off the FORWARD, which for NSE index options is the
same-expiry future (or put-call parity when a weekly expiry has none), so
dividends never need estimating. Vectorised over numpy arrays: one day's
whole chain is solved at once.

No SciPy: norm_cdf is Abramowitz-Stegun 7.1.26 for erf, error < 1.5e-7.
Delta is forward delta; vega is per 1.00 of volatility; theta is per
calendar day.
"""

from __future__ import annotations

import numpy as np

_SQRT2 = np.sqrt(2.0)
IV_LOW, IV_HIGH, IV_ITER = 1e-4, 5.0, 80


def _erf(x: np.ndarray) -> np.ndarray:
    sign = np.sign(x)
    a = np.abs(x)
    t = 1.0 / (1.0 + 0.3275911 * a)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741
           + t * (-1.453152027 + t * 1.061405429))))
    return sign * (1.0 - poly * np.exp(-a * a))


def norm_cdf(x) -> np.ndarray:
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / _SQRT2))


def norm_pdf(x) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / np.sqrt(2.0 * np.pi)


def _d1d2(F, K, T, sigma):
    vol_t = sigma * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sigma * sigma * T) / vol_t
    return d1, d1 - vol_t


def black76(F, K, T, sigma, r, is_call) -> np.ndarray:
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    disc = np.exp(-r * T)
    d1, d2 = _d1d2(F, K, T, sigma)
    call = disc * (F * norm_cdf(d1) - K * norm_cdf(d2))
    put = disc * (K * norm_cdf(-d2) - F * norm_cdf(-d1))
    return np.where(np.asarray(is_call), call, put)


def implied_vol(price, F, K, T, r, is_call) -> np.ndarray:
    """Bisection on [IV_LOW, IV_HIGH]; NaN where no volatility fits the price."""
    price, F, K, T = (np.asarray(a, dtype=float) for a in (price, F, K, T))
    is_call = np.asarray(is_call)
    disc = np.exp(-r * T)
    intrinsic = disc * np.where(is_call, np.maximum(F - K, 0.0), np.maximum(K - F, 0.0))
    ok = (T > 0) & (price > intrinsic) & np.isfinite(price) & (F > 0) & (K > 0)
    t_safe = np.where(ok, T, 1.0)
    lo = np.full(price.shape, IV_LOW)
    hi = np.full(price.shape, IV_HIGH)
    ok &= black76(F, K, t_safe, hi, r, is_call) >= price
    for _ in range(IV_ITER):
        mid = 0.5 * (lo + hi)
        above = black76(F, K, t_safe, mid, r, is_call) > price
        hi = np.where(above, mid, hi)
        lo = np.where(above, lo, mid)
    return np.where(ok, 0.5 * (lo + hi), np.nan)


def greeks(F, K, T, sigma, r, is_call) -> dict[str, np.ndarray]:
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    is_call = np.asarray(is_call)
    disc = np.exp(-r * T)
    d1, d2 = _d1d2(F, K, T, sigma)
    pdf = norm_pdf(d1)
    delta = np.where(is_call, disc * norm_cdf(d1), -disc * norm_cdf(-d1))
    gamma = disc * pdf / (F * sigma * np.sqrt(T))
    vega = disc * F * pdf * np.sqrt(T)
    price = black76(F, K, T, sigma, r, is_call)
    theta_year = -disc * F * pdf * sigma / (2.0 * np.sqrt(T)) + r * price
    return {"delta": delta, "gamma": gamma, "vega": vega, "theta": theta_year / 365.0}
```

- [ ] **Step 4: Run** — expect `7 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): vectorised Black-76 prices, implied volatility and Greeks"`

---

### Task 4: Index spot from Yahoo (and VIX onto the same helper)

**Files:** Create `fno/yahoo.py`, `fno/spot.py`, `tests/test_fno_spot.py`; Modify `fno/vix.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_spot.py`:

```python
"""Index daily closes from Yahoo for the underlyings it covers."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import spot  # noqa: E402
from test_fno_vix import yahoo_like  # noqa: E402


def test_only_the_three_yahoo_covers_have_symbols():
    assert spot.YAHOO_SYMBOLS == {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYNXT50": "^NSMIDCP"}


def test_spot_is_stored_and_looked_up_by_date(tmp_path):
    from fno.yahoo import normalise
    spot.write(tmp_path, "NIFTY", normalise(yahoo_like()))
    closes = spot.closes(tmp_path, "NIFTY")
    assert closes[date(2026, 9, 30)] == 14.0
    assert date(2026, 10, 1) not in closes


def test_an_uncovered_underlying_has_no_closes(tmp_path):
    assert spot.closes(tmp_path, "MIDCPNIFTY") == {}
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

`fno/yahoo.py`:

```python
"""Daily OHLC from Yahoo, normalised to trade_date/open/high/low/close."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

COLUMNS = ["trade_date", "open", "high", "low", "close"]


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename(columns=str.lower)[["open", "high", "low", "close"]]
    frame = frame.dropna(subset=["close"])
    frame.insert(0, "trade_date", pd.DatetimeIndex(frame.index).date)
    return frame.reset_index(drop=True)[COLUMNS]


def fetch(symbol: str) -> pd.DataFrame:
    import yfinance as yf

    return normalise(yf.download(symbol, period="max", interval="1d",
                                 auto_adjust=False, progress=False))


def write_whole(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read_whole(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
```

Replace the body of `fno/vix.py` (keep its docstring) with:

```python
from __future__ import annotations

from pathlib import Path

import pandas as pd

from fno.yahoo import COLUMNS, fetch, normalise, read_whole, write_whole  # noqa: F401 - normalise re-exported

SYMBOL = "^INDIAVIX"


def fetch_daily() -> pd.DataFrame:
    return fetch(SYMBOL)


def vix_path(root: Path) -> Path:
    return Path(root) / "vix" / "daily.parquet"


def write(root: Path, frame: pd.DataFrame) -> None:
    write_whole(vix_path(root), frame)


def read(root: Path) -> pd.DataFrame:
    return read_whole(vix_path(root))
```

`fno/spot.py`:

```python
"""Index daily closes from Yahoo, for the underlyings Yahoo covers.

FINNIFTY and MIDCPNIFTY have no usable Yahoo history (checked 4 Oct 2026);
their spot comes from NSE's underlying_price column (from 8 Jul 2024).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from fno.yahoo import fetch, read_whole, write_whole

YAHOO_SYMBOLS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "NIFTYNXT50": "^NSMIDCP"}


def spot_path(root: Path, underlying: str) -> Path:
    return Path(root) / "spot" / f"{underlying}.parquet"


def refresh(root: Path, underlying: str) -> pd.DataFrame:
    frame = fetch(YAHOO_SYMBOLS[underlying])
    write(root, underlying, frame)
    return frame


def write(root: Path, underlying: str, frame: pd.DataFrame) -> None:
    write_whole(spot_path(root, underlying), frame)


def closes(root: Path, underlying: str) -> dict[date, float]:
    frame = read_whole(spot_path(root, underlying))
    return dict(zip(frame["trade_date"], frame["close"].astype(float)))
```

- [ ] **Step 4: Run** `tests/test_fno_spot.py tests/test_fno_vix.py` — expect `5 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): index spot from Yahoo; VIX on the same helper"`

---

### Task 5: The chain as it stood

**Files:** Create `fno/chain.py`, `tests/test_fno_chain.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_chain.py`:

```python
"""One day's chain with IV and Greeks, never anything later (design §2.5)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain, pricing, store  # noqa: E402
from fno.nse_files import COLUMNS  # noqa: E402

DAY = date(2026, 9, 1)
EXP = date(2026, 9, 29)          # 28 days
R = 0.054
F_TRUE = 22000.0
SIGMA = 0.14


def synthetic_day(day=DAY, *, with_future=True, planted=False):
    """A clean chain priced with Black-76 at one volatility."""
    strikes = np.arange(20000, 24050, 100.0)
    T = (EXP - day).days / 365
    rows = []
    for k in strikes:
        for is_call, typ in ((True, "CE"), (False, "PE")):
            px = float(pricing.black76(np.array([F_TRUE]), np.array([k]), np.array([T]),
                                       np.array([SIGMA]), R, np.array([is_call]))[0])
            rows.append({"trade_date": day, "underlying": "NIFTY", "instrument": "OPT", "expiry": EXP,
                         "strike": k, "option_type": typ, "open": px, "high": px, "low": px,
                         "close": px, "settle": px, "volume_contracts": 100,
                         "oi_units": int(1000 + abs(k - F_TRUE)), "oi_change_units": 0,
                         "turnover_inr": 0.0, "lot_size": 65.0, "underlying_price": 21900.0})
    if with_future:
        rows.append({**rows[0], "instrument": "FUT", "strike": np.nan, "option_type": np.nan,
                     "close": F_TRUE, "settle": F_TRUE, "open": F_TRUE, "high": F_TRUE, "low": F_TRUE})
    frame = pd.DataFrame(rows)[list(COLUMNS)]
    if planted:   # a later day whose prices must never leak into DAY
        later = frame.copy()
        later["trade_date"] = date(2026, 9, 2)
        later["settle"] = later["settle"] * 3
        frame = pd.concat([frame, later], ignore_index=True)
    return frame


def test_expiries_listed_on_a_day_are_sorted_and_future_dated():
    rows = synthetic_day()
    assert chain.expiries(rows, DAY) == [EXP]


def test_the_forward_is_the_same_expiry_future_when_there_is_one():
    assert chain.forward(synthetic_day(), EXP, DAY, R) == F_TRUE


def test_without_a_future_the_forward_comes_from_put_call_parity():
    assert chain.forward(synthetic_day(with_future=False), EXP, DAY, R) == pytest.approx(F_TRUE, rel=1e-6)


def test_the_snapshot_recovers_the_volatility_it_was_priced_with():
    snap = chain.snapshot(synthetic_day(), "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    near = snap.rows[(snap.rows["strike"] - F_TRUE).abs() <= 1000]
    assert np.allclose(near["iv"], SIGMA, atol=1e-4)
    assert snap.dte == 28 and snap.forward == F_TRUE and snap.spot == 21900.0
    assert {"delta", "gamma", "vega", "theta"} <= set(snap.rows.columns)


def test_a_later_day_never_reaches_the_snapshot(tmp_path):
    frame = synthetic_day(planted=True)
    store.write_day(tmp_path, frame[frame["trade_date"] == DAY], DAY)
    store.write_day(tmp_path, frame[frame["trade_date"] == date(2026, 9, 2)], date(2026, 9, 2))
    snap = chain.chain_at(tmp_path, "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    assert (snap.rows["trade_date"] == DAY).all()
    assert np.allclose(snap.rows["iv"].dropna(), SIGMA, atol=1e-3)


def test_on_expiry_day_iv_is_undefined():
    day_rows = synthetic_day()
    day_rows["trade_date"] = EXP
    snap = chain.snapshot(day_rows, "NIFTY", EXP, EXP, spot=21900.0, rate=R)
    assert snap.dte == 0 and snap.rows["iv"].isna().all()
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

`fno/chain.py`:

```python
"""The option chain as it stood on a day (design §2.5).

Only rows with trade_date == the day are ever read, so nothing later can
reach a snapshot. IV and Greeks are computed from settlement prices (always
present; NSE's theoretical price for an untraded contract) so the surface
is complete. Whether a contract TRADED is its volume - fills (step 2b) use
traded prices only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from fno import pricing, store


@dataclass(frozen=True)
class ChainSnapshot:
    underlying: str
    day: date
    expiry: date
    dte: int                  # calendar days to expiry
    forward: float
    spot: float               # NaN when unknown
    rate: float
    rows: pd.DataFrame        # options of this expiry, with iv/delta/gamma/vega/theta


def expiries(day_rows: pd.DataFrame, day: date) -> list[date]:
    exp = day_rows.loc[(day_rows["trade_date"] == day) & (day_rows["instrument"] == "OPT"), "expiry"]
    return sorted(e for e in set(exp) if e >= day)


def forward(day_rows: pd.DataFrame, expiry: date, day: date, rate: float) -> float:
    rows = day_rows[(day_rows["trade_date"] == day) & (day_rows["expiry"] == expiry)]
    fut = rows[(rows["instrument"] == "FUT") & (rows["settle"] > 0)]
    if not fut.empty:
        return float(fut["settle"].iloc[0])
    opts = rows[rows["instrument"] == "OPT"]
    calls = opts[opts["option_type"] == "CE"].set_index("strike")["settle"]
    puts = opts[opts["option_type"] == "PE"].set_index("strike")["settle"]
    both = pd.concat([calls.rename("c"), puts.rename("p")], axis=1).dropna()
    both = both[(both["c"] > 0) & (both["p"] > 0)]
    if both.empty:
        return float("nan")
    k = (both["c"] - both["p"]).abs().idxmin()
    T = (expiry - day).days / 365
    return float(k + np.exp(rate * T) * (both.loc[k, "c"] - both.loc[k, "p"]))


def snapshot(day_rows: pd.DataFrame, underlying: str, day: date, expiry: date,
             *, spot: float, rate: float) -> ChainSnapshot:
    rows = day_rows[(day_rows["trade_date"] == day) & (day_rows["underlying"] == underlying)
                    & (day_rows["instrument"] == "OPT") & (day_rows["expiry"] == expiry)].copy()
    F = forward(day_rows[day_rows["underlying"] == underlying], expiry, day, rate)
    dte = (expiry - day).days
    T = np.full(len(rows), dte / 365)
    is_call = (rows["option_type"] == "CE").to_numpy()
    Fs = np.full(len(rows), F)
    K = rows["strike"].to_numpy(dtype=float)
    iv = pricing.implied_vol(rows["settle"].to_numpy(dtype=float), Fs, K, T, rate, is_call)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = pricing.greeks(Fs, K, T, iv, rate, is_call)
    rows["iv"] = iv
    for name, values in g.items():
        rows[name] = np.where(np.isfinite(iv), values, np.nan)
    rows = rows.sort_values(["strike", "option_type"]).reset_index(drop=True)
    return ChainSnapshot(underlying, day, expiry, dte, F, float(spot), rate, rows)


def chain_at(root: Path, underlying: str, day: date, expiry: date | None = None,
             *, spot: float, rate: float) -> ChainSnapshot:
    """The chain for `expiry` (default: nearest with a day to run) as it stood on `day`."""
    day_rows = store.read_daily(root, underlying, day, day)
    if expiry is None:
        later = [e for e in expiries(day_rows, day) if e > day]
        if not later:
            raise LookupError(f"{underlying} {day}: no expiry with a day to run")
        expiry = later[0]
    return snapshot(day_rows, underlying, day, expiry, spot=spot, rate=rate)
```

- [ ] **Step 4: Run** — expect `6 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): the option chain as it stood on a day, with IV and Greeks"`

---

### Task 6: Event days

**Files:** Create `fno/events.yaml`, `fno/events.py`, `tests/test_fno_events.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_events.py`:

```python
"""Budget, RBI policy and election-result days."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.events import Events  # noqa: E402

EVENTS = Events.load()


def test_known_budget_and_election_days_are_events():
    assert EVENTS.on(date(2020, 2, 1)) == ["budget"]
    assert "election_result" in EVENTS.on(date(2024, 6, 4))


def test_an_ordinary_day_has_no_event():
    assert EVENTS.on(date(2024, 6, 5)) == []


def test_every_rbi_date_has_a_source():
    rbi = [r for r in EVENTS.rows if r["kind"] == "rbi_policy"]
    assert len(rbi) >= 40        # ~6 a year since Oct 2016, plus the unscheduled ones
    for row in rbi:
        assert row.get("source"), row


def test_events_between_two_dates():
    assert date(2019, 7, 5) in EVENTS.between(date(2019, 7, 1), date(2019, 7, 31))
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Research the RBI policy dates, then write the file**

Budget and election days are fixed in the YAML below. For RBI, find each Monetary Policy Committee announcement date from the first MPC (4 Oct 2016) to the latest, including the unscheduled ones (27 Mar 2020, 22 May 2020, 4 May 2022). Use RBI's own press releases ("Monetary Policy Statement") where possible; record the URL in each row's `source`. Do not add a date without a source.

`fno/events.yaml` (Budget and elections complete; append the RBI rows found):

```yaml
# Market event days (design 2.1). kind: budget | rbi_policy | election_result.
# RBI rows carry a source URL each.
- {date: 2016-02-29, kind: budget}
- {date: 2017-02-01, kind: budget}
- {date: 2018-02-01, kind: budget}
- {date: 2019-02-01, kind: budget}
- {date: 2019-07-05, kind: budget}
- {date: 2020-02-01, kind: budget}
- {date: 2021-02-01, kind: budget}
- {date: 2022-02-01, kind: budget}
- {date: 2023-02-01, kind: budget}
- {date: 2024-02-01, kind: budget}
- {date: 2024-07-23, kind: budget}
- {date: 2025-02-01, kind: budget}
- {date: 2026-02-01, kind: budget}
- {date: 2019-05-23, kind: election_result}
- {date: 2024-06-04, kind: election_result}
```

`fno/events.py`:

```python
"""Market event days: Union Budget, RBI policy, general-election results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

EVENTS_FILE = Path(__file__).with_name("events.yaml")


@dataclass(frozen=True)
class Events:
    rows: tuple[dict[str, Any], ...]

    @classmethod
    def load(cls, path: Path = EVENTS_FILE) -> "Events":
        rows = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
        for row in rows:
            row["date"] = date.fromisoformat(str(row["date"]))
        return cls(tuple(rows))

    def on(self, day: date) -> list[str]:
        return sorted(r["kind"] for r in self.rows if r["date"] == day)

    def between(self, start: date, end: date) -> list[date]:
        return sorted({r["date"] for r in self.rows if start <= r["date"] <= end})
```

- [ ] **Step 4: Run** — expect `4 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): Budget, RBI policy and election-result days, each RBI date sourced"`

---

### Task 7: Facts from a snapshot, and the daily table

**Files:** Create `fno/features.py`, `tests/test_fno_features.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_features.py`:

```python
"""Market facts from one day's chain (design §2.4)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import chain, features  # noqa: E402
from test_fno_chain import DAY, EXP, F_TRUE, R, SIGMA, synthetic_day  # noqa: E402


@pytest.fixture
def snap():
    return chain.snapshot(synthetic_day(), "NIFTY", DAY, EXP, spot=21900.0, rate=R)


def test_atm_is_the_strike_nearest_the_forward(snap):
    facts = features.from_snapshot(snap)
    assert facts["atm_strike"] == 22000.0
    assert facts["atm_iv"] == pytest.approx(SIGMA, abs=1e-4)


def test_expected_move_is_the_straddle_over_the_forward(snap):
    facts = features.from_snapshot(snap)
    atm = snap.rows[snap.rows["strike"] == 22000.0]
    assert facts["straddle"] == pytest.approx(atm["settle"].sum())
    assert facts["expected_move_pct"] == pytest.approx(100 * atm["settle"].sum() / F_TRUE)


def test_pcr_divides_put_open_interest_by_call(snap):
    facts = features.from_snapshot(snap)
    puts = snap.rows.loc[snap.rows["option_type"] == "PE", "oi_units"].sum()
    calls = snap.rows.loc[snap.rows["option_type"] == "CE", "oi_units"].sum()
    assert facts["pcr_oi"] == pytest.approx(puts / calls)


def test_max_pain_is_the_strike_that_pays_option_holders_least():
    """All open interest on 22000 calls and 21000 puts: settling between
    them pays nothing, and the lowest such strike is 21000."""
    rows = synthetic_day()
    rows["oi_units"] = 0
    rows.loc[(rows["strike"] == 22000.0) & (rows["option_type"] == "CE"), "oi_units"] = 1000
    rows.loc[(rows["strike"] == 21000.0) & (rows["option_type"] == "PE"), "oi_units"] = 1000
    snap = chain.snapshot(rows, "NIFTY", DAY, EXP, spot=21900.0, rate=R)
    assert features.from_snapshot(snap)["max_pain"] == 21000.0


def test_flat_volatility_has_no_skew(snap):
    assert features.from_snapshot(snap)["skew_25d"] == pytest.approx(0.0, abs=1e-3)


def test_basis_and_days_to_expiry(snap):
    facts = features.from_snapshot(snap)
    assert facts["dte"] == 28
    assert facts["basis_pct"] == pytest.approx(100 * (F_TRUE / 21900.0 - 1))


def test_iv_rank_places_today_within_the_past_year():
    ivs = pd.Series([0.10] * 100 + [0.20] * 100 + [0.15], dtype=float)
    rank, pctile = features.iv_rank(ivs, window=252)
    assert rank.iloc[-1] == pytest.approx(50.0)
    assert pctile.iloc[-1] == pytest.approx(100 * 100 / 200)
```

- [ ] **Step 2: Run to see them fail.**

- [ ] **Step 3: Implement**

`fno/features.py`:

```python
"""Market facts from one day's chain, and the daily table a strategy reads.

Every fact is computed from that day's snapshot and earlier data only
(design §2.4). Names here are the names a strategy writes (design §3).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from fno.chain import ChainSnapshot


def _nearest_delta(rows: pd.DataFrame, typ: str, target: float) -> pd.Series | None:
    side = rows[(rows["option_type"] == typ) & rows["delta"].notna()]
    if side.empty:
        return None
    return side.loc[(side["delta"] - target).abs().idxmin()]


def max_pain(rows: pd.DataFrame) -> float:
    strikes = np.sort(rows["strike"].unique())
    calls = rows[rows["option_type"] == "CE"]
    puts = rows[rows["option_type"] == "PE"]
    best, best_pay = float("nan"), float("inf")
    for s in strikes:
        pay = (np.maximum(s - calls["strike"], 0) * calls["oi_units"]).sum() \
            + (np.maximum(puts["strike"] - s, 0) * puts["oi_units"]).sum()
        if pay < best_pay:
            best, best_pay = float(s), pay
    return best


def from_snapshot(snap: ChainSnapshot) -> dict[str, Any]:
    rows = snap.rows
    out: dict[str, Any] = {"trade_date": snap.day, "expiry": snap.expiry, "dte": snap.dte,
                           "spot": snap.spot, "forward": snap.forward}
    strikes = rows["strike"].unique()
    atm = float(strikes[np.argmin(np.abs(strikes - snap.forward))]) if len(strikes) else np.nan
    at = rows[rows["strike"] == atm]
    out["atm_strike"] = atm
    out["atm_iv"] = float(at["iv"].mean()) if len(at) else np.nan
    out["straddle"] = float(at["settle"].sum()) if len(at) == 2 else np.nan
    out["expected_move_pct"] = 100 * out["straddle"] / snap.forward if snap.forward else np.nan
    calls, puts = rows[rows["option_type"] == "CE"], rows[rows["option_type"] == "PE"]
    out["pcr_oi"] = puts["oi_units"].sum() / calls["oi_units"].sum() if calls["oi_units"].sum() else np.nan
    out["pcr_vol"] = (puts["volume_contracts"].sum() / calls["volume_contracts"].sum()
                      if calls["volume_contracts"].sum() else np.nan)
    out["max_pain"] = max_pain(rows)
    p25, c25 = _nearest_delta(rows, "PE", -0.25), _nearest_delta(rows, "CE", 0.25)
    out["skew_25d"] = float(p25["iv"] - c25["iv"]) if p25 is not None and c25 is not None else np.nan
    out["max_oi_call_strike"] = float(calls.loc[calls["oi_units"].idxmax(), "strike"]) if len(calls) else np.nan
    out["max_oi_put_strike"] = float(puts.loc[puts["oi_units"].idxmax(), "strike"]) if len(puts) else np.nan
    out["basis_pct"] = 100 * (snap.forward / snap.spot - 1) if snap.spot == snap.spot and snap.spot else np.nan
    return out


def iv_rank(atm_iv: pd.Series, *, window: int = 252) -> tuple[pd.Series, pd.Series]:
    """IV rank (position in the window's range) and percentile (share of days below), in %."""
    lo = atm_iv.rolling(window, min_periods=20).min()
    hi = atm_iv.rolling(window, min_periods=20).max()
    rank = 100 * (atm_iv - lo) / (hi - lo)
    pctile = atm_iv.rolling(window, min_periods=20).apply(
        lambda w: 100 * (w[:-1] < w[-1]).sum() / (len(w) - 1), raw=True)
    return rank, pctile


def daily_table(facts: list[dict[str, Any]], *, vix: dict[date, float],
                events_on, fii_net: dict[date, float]) -> pd.DataFrame:
    """Facts per day plus VIX, IV rank, weekday, events and FII index-future positioning."""
    table = pd.DataFrame(facts).sort_values("trade_date").reset_index(drop=True)
    table["vix"] = table["trade_date"].map(vix)
    table["vix_chg_pct"] = 100 * table["vix"].pct_change()
    table["iv_rank"], table["iv_pctile"] = iv_rank(table["atm_iv"])
    table["weekday"] = [d.weekday() for d in table["trade_date"]]
    table["event_today"] = [bool(events_on(d)) for d in table["trade_date"]]
    table["fii_index_fut_net"] = table["trade_date"].map(fii_net)
    return table
```

- [ ] **Step 4: Run** — expect `7 passed`.
- [ ] **Step 5: Commit** — `git commit -m "feat(fno): market facts from a day's chain - ATM IV, PCR, max pain, skew, IV rank"`

---

### Task 8: Build the facts tables and check them against reality

**Files:** Create `scripts/fno_features.py`; Modify `docs/DATA.md`

- [ ] **Step 1: Write the script**

`scripts/fno_features.py`:

```python
"""Build data/fno/features/<UNDERLYING>.parquet from the local F&O store.

    python scripts/fno_features.py                 # every underlying, all history
    python scripts/fno_features.py --only NIFTY
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from config import use_utf8_stdout  # noqa: E402
from fno import chain, features, participants, spot, store, vix  # noqa: E402
from fno.contracts import INDEX_UNDERLYINGS  # noqa: E402
from fno.events import Events  # noqa: E402
from fno.rates import risk_free  # noqa: E402
from fno.yahoo import write_whole  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=INDEX_UNDERLYINGS)
    args = parser.parse_args(argv)
    root = store.root_dir()
    vix_close = dict(zip(*(vix.read(root)[c] for c in ("trade_date", "close"))))
    fii = participants.read(root, date(2016, 1, 1), date.today())
    fii = fii[fii["client_type"] == "FII"]
    fii_net = dict(zip(fii["trade_date"], fii["future_index_long"] - fii["future_index_short"]))
    events = Events.load()

    for underlying in [args.only] if args.only else INDEX_UNDERLYINGS:
        if underlying in spot.YAHOO_SYMBOLS:
            spot.refresh(root, underlying)
        closes = spot.closes(root, underlying)
        facts = []
        for year in range(2016, date.today().year + 1):
            rows = store.read_daily(root, underlying, date(year, 1, 1), date(year, 12, 31))
            for day, day_rows in rows.groupby("trade_date"):
                later = [e for e in chain.expiries(day_rows, day) if e > day]
                if not later:
                    continue
                s = closes.get(day, day_rows["underlying_price"].dropna().iloc[0]
                               if day_rows["underlying_price"].notna().any() else float("nan"))
                snap = chain.snapshot(day_rows, underlying, day, later[0], spot=s, rate=risk_free(day))
                row = features.from_snapshot(snap)
                row["spot_source"] = ("yahoo" if day in closes
                                      else "nse" if s == s else "none")
                facts.append(row)
        table = features.daily_table(facts, vix=vix_close, events_on=events.on, fii_net=fii_net)
        write_whole(root / "features" / f"{underlying}.parquet", table)
        print(f"{underlying}: {len(table)} days, {table['trade_date'].min()} -> {table['trade_date'].max()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it on the full store** — `.venv/Scripts/python.exe scripts/fno_features.py`. Expected: one line per underlying with day counts matching the stored sessions (NIFTY and BANKNIFTY ~2,600 days).

- [ ] **Step 3: Check the facts against reality**

```bash
.venv/Scripts/python.exe -c "
import pandas as pd
t = pd.read_parquet('data/fno/features/NIFTY.parquet')
print('ATM IV vs India VIX correlation:', round(t['atm_iv'].corr(t['vix']), 3))
print('median |max pain - spot| / spot %:', round((100*(t['max_pain']-t['spot']).abs()/t['spot']).median(), 2))
print(t[['pcr_oi','expected_move_pct','basis_pct','skew_25d']].describe().round(3).to_string())
print(t.loc[t['trade_date'].astype(str).isin(['2020-03-23','2024-06-04']), ['trade_date','atm_iv','vix','expected_move_pct']].to_string())
"
```

Expected, and what each tests: correlation of ATM IV with VIX above 0.85 (VIX is NIFTY's 30-day implied volatility, so a weak correlation means the IV is wrong); max pain within a few percent of spot; PCR mostly 0.5–2; skew positive on most days (Indian index puts carry more volatility than calls); ATM IV and VIX high on 23 Mar 2020 and 4 Jun 2024. Investigate anything that misses before committing.

- [ ] **Step 4: Document** — append to `docs/DATA.md` section 9 a "Market facts" paragraph: where `features/<UNDERLYING>.parquet` lives, its columns (this plan's Task 7 names), that IV comes from settlement prices with Black-76 and forward delta, the spot sources, and the Step 3 numbers.

- [ ] **Step 5: Full suite and commit** — run `.venv/Scripts/python.exe -m pytest tests/ -q` (all pass), then
`git add scripts/fno_features.py docs/DATA.md && git commit -m "feat(fno): daily market-facts tables, checked against India VIX"`
