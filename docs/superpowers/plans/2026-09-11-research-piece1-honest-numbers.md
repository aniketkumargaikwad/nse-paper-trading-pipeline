# Research Loop — Piece 1: Honest Numbers — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A hand-run command that tests one stored strategy on every stock, index and timeframe using training years only, picks the best stock × timeframe by a fixed rule, then opens the locked final year once and prints what ₹1 lakh became — with overnight trades charged real delivery fees.

**Architecture:** A new `research/` package of small, mostly pure modules: a frozen calendar (`windows`), a no-fetch price reader (`prices`), the list of combinations (`universe`), a 4-process sweep over the existing simulator (`sweep`), ₹1 lakh compounding (`lakh`), the pick rule (`picker`) and a thin CLI (`evaluate`). Fees gain a `DeliveryCostModel` and a `HoldingCostModel` that picks intraday or delivery per trade; the one change to existing engine code is in `backtest._make_trade`. Index history comes from a one-time Yahoo download into the existing parquet store.

**Tech Stack:** Python 3.11, pandas 2.2.3, pyarrow, pytest 8.3.4, multiprocessing (spawn), yfinance, Supabase client (read-only lookups).

**Spec:** `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md` — this plan is build piece 1 of 4 (§10).

---

## Before you start: facts about this codebase

- Windows machine, Git Bash. Run Python as `./.venv/Scripts/python.exe`.
- Tests are flat files in `tests/`, each starting with
  `sys.path.insert(0, str(Path(__file__).resolve().parent.parent))`. No conftest.
- Candle files live at `data/candles/{timeframe}/{instrument_id}/{year}.parquet`.
  Stored timeframes are `5m` and `day`; `15m/25m/30m/60m` are resampled from
  `5m` by `resample.resample_candles`. Timestamps are candle **start** times,
  tz-aware UTC. A daily candle for 27 Aug is stamped `2026-08-26 18:30 UTC`
  (India midnight).
- `candle_store.CandleStore.get_candles` **fetches missing data from Dhan**.
  The Dhan subscription has lapsed and prices are frozen, so research code must
  never call it. Use `ParquetCandleBackend(None, root).read_candles(...)`,
  which reads local files only.
- Intraday prices need corporate-action corrections from the Supabase
  `price_adjustments` table (`price_adjust.apply_adjustments`). Daily prices do
  not.
- Every trade's fee is charged in `backtest._make_trade`; the v3 simulator
  (`machine_backtest.py`) calls the same function.
- `walk_forward.split_trades(trades, split_ts)` splits by **entry fill**;
  the boundary belongs to the later half.
- Parsed strategies (v2 `Strategy` and v3 `StateMachine`) pickle cleanly —
  verified — so they can be sent to worker processes.
- The 9 indexes already have rows in the `instruments` table:
  `NSE:NIFTY, NSE:BANKNIFTY, NSE:NIFTYIT, NSE:NIFTYAUTO, NSE:NIFTYPHARMA,
  NSE:NIFTYFMCG, NSE:NIFTYMETAL, NSE:FINNIFTY, NSE:NIFTYMID100FREE`.
- `.env` has `CANDLE_STORE=parquet` and no `CANDLE_ROOT`, so
  `settings.candle_root` is `data/candles`.

## File map

| File | Status | Responsibility |
|---|---|---|
| `costs.py` | modify | add `DeliveryCostModel`, `HoldingCostModel` |
| `backtest.py` | modify | `_make_trade` passes timestamps to holding-aware models; `COST_MODEL=holding` |
| `config.py` | modify | comment documents `holding` |
| `research/__init__.py` | create | package marker |
| `research/universe.py` | create | timeframes, the 9 indexes, `Combo`, volume-rule detection |
| `research/windows.py` | create | `DATA_END`, training and locked windows, trimming |
| `research/prices.py` | create | `FrozenPriceReader`, instrument-id and correction loaders |
| `research/sweep.py` | create | run one strategy over all combinations, serial or 4 processes |
| `research/lakh.py` | create | ₹1 lakh compounding, just-holding, locked verdict |
| `research/picker.py` | create | the §5.3 pick rule |
| `research/evaluate.py` | create | hand-run CLI |
| `scripts/download_index_history.py` | create | one-time Yahoo index download |
| `tests/test_costs_delivery.py` | create | delivery and holding fee structure |
| `tests/test_backtest_holding_costs.py` | create | simulator seam + `build_cost_model` |
| `tests/test_research_universe.py` | create | |
| `tests/test_research_windows.py` | create | |
| `tests/test_research_prices.py` | create | |
| `tests/test_research_sweep.py` | create | |
| `tests/research_helpers.py` | create | shared trade builder for lakh and picker tests |
| `tests/test_research_lakh.py` | create | |
| `tests/test_research_picker.py` | create | |
| `tests/test_research_evaluate.py` | create | |

---

### Task 0: Restore a green baseline

The suite has one failure, caused by an uncommitted edit that added six
strategies to `strategies.yaml`; `tests/test_strategy_schema.py::test_shipped_strategies_file_is_valid`
expects exactly one. Those six strategies are already stored in the database,
so the file edit can be reverted without losing them.

**Files:**
- Modify: `strategies.yaml` (restore committed version)

- [ ] **Step 1: Confirm the six strategies are in the database**

Run:
```bash
./.venv/Scripts/python.exe -c "
from config import get_settings
from db import SupabaseStore
names = {d['name'] for d in SupabaseStore.connect(get_settings()).list_strategy_documents()}
want = {'N200-BREAKOUT-DAY','N200-BREAKDOWN-DAY','N200-PULLBACK-DAY','N200-MACD-REGIME-DAY','N200-PDH-BREAK-15m','N200-PDL-BREAK-15m'}
print('all present' if want <= names else f'MISSING: {sorted(want - names)}')
"
```
Expected: `all present`. If anything is missing, STOP and report — do not revert.

- [ ] **Step 2: Restore the committed file**

Run: `git checkout -- strategies.yaml`

- [ ] **Step 3: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: `1095 passed, 1 skipped` (0 failed).

No commit — nothing changed relative to `HEAD`.

---

### Task 1: Delivery cost model

**Files:**
- Modify: `costs.py` (constants after `DEFAULT_BROKERAGE_PCT`; class after `CostModel`)
- Test: `tests/test_costs_delivery.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_costs_delivery.py`:
```python
"""Delivery (overnight) charges, and the model that chooses per trade.

Structure, not rates: rates drift and are a visible number to update; a charge
on the wrong leg is a silent bias in every result.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel  # noqa: E402


def test_delivery_stt_applies_to_both_legs():
    m = DeliveryCostModel()
    no_stt = DeliveryCostModel(stt=0.0)
    qty, entry, exit_ = 100, 1000.0, 1100.0
    diff = m.round_trip(entry, exit_, qty) - no_stt.round_trip(entry, exit_, qty)
    assert diff == pytest.approx((entry + exit_) * qty * m.stt, rel=1e-6)


def test_delivery_stamp_duty_applies_to_the_buy_side_only():
    m = DeliveryCostModel()
    no_stamp = DeliveryCostModel(stamp_duty_buy=0.0)
    qty, entry, exit_ = 100, 1000.0, 1100.0
    diff = m.round_trip(entry, exit_, qty) - no_stamp.round_trip(entry, exit_, qty)
    assert diff == pytest.approx(entry * qty * m.stamp_duty_buy, rel=1e-6)


def test_depository_charge_is_once_per_trade_whatever_the_size():
    m = DeliveryCostModel()
    no_dp = DeliveryCostModel(dp_charge_per_sell=0.0)
    for qty in (10, 10_000):
        diff = m.round_trip(1000.0, 1000.0, qty) - no_dp.round_trip(1000.0, 1000.0, qty)
        assert diff == pytest.approx(m.dp_charge_per_sell)


def test_overnight_costs_more_than_the_same_trade_intraday():
    assert DeliveryCostModel().round_trip(1000.0, 1000.0, 100) > CostModel().round_trip(1000.0, 1000.0, 100)


def test_one_lakh_round_trip_is_about_a_fifth_of_a_percent():
    """Pins the scale measured in docs/research/2026-08-30: 0.21-0.26%."""
    cost = DeliveryCostModel().round_trip(1000.0, 1000.0, 100)
    assert 0.18 < cost / 100_000 * 100 < 0.30


def test_zero_quantity_costs_nothing():
    assert DeliveryCostModel().round_trip(1000.0, 1100.0, 0) == 0.0
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_costs_delivery.py -v`
Expected: collection error, `ImportError: cannot import name 'DeliveryCostModel'`.

- [ ] **Step 3: Add the constants**

In `costs.py`, directly after the line `DEFAULT_BROKERAGE_PCT = 0.0003          # 0.03%`, insert:
```python

# Delivery (CNC): a position still held after the day it was opened.
# As understood on 2026-09-11; RE-VERIFY before trusting a rupee figure.
STT_DELIVERY = 0.001                 # 0.1% on BOTH legs (intraday: sell only)
STAMP_DUTY_DELIVERY_BUY = 0.00015    # 0.015% on the buy side
DEFAULT_DELIVERY_BROKERAGE_PER_ORDER = 0.0   # Dhan charges no delivery brokerage
DEFAULT_DP_CHARGE_PER_SELL = 15.0            # depository charge per sell incl. GST (approx.)
```

- [ ] **Step 4: Add the class**

In `costs.py`, directly after the end of `class CostModel` (before `@dataclass(frozen=True)` for `FlatCostModel`), insert:
```python
@dataclass(frozen=True)
class DeliveryCostModel:
    """Charges for a round trip held overnight.

    Differs from intraday in three places that matter: STT on BOTH legs at a
    much higher rate, a higher stamp duty, and a depository charge on the sale.
    Charging an overnight position intraday rates flattered every multi-day
    backtest by roughly 0.15% per trade (docs/research/2026-08-30 §4).
    """

    brokerage_per_order: float = DEFAULT_DELIVERY_BROKERAGE_PER_ORDER
    stt: float = STT_DELIVERY
    exchange_txn: float = EXCHANGE_TXN
    sebi_fees: float = SEBI_FEES
    stamp_duty_buy: float = STAMP_DUTY_DELIVERY_BUY
    gst_rate: float = GST_RATE
    dp_charge_per_sell: float = DEFAULT_DP_CHARGE_PER_SELL

    def round_trip(self, entry_price: float, exit_price: float, quantity: float) -> float:
        """Total charges for a completed overnight trade, in rupees."""
        if quantity <= 0:
            return 0.0

        buy_turnover = entry_price * quantity
        sell_turnover = exit_price * quantity
        turnover = buy_turnover + sell_turnover

        brokerage = 2 * self.brokerage_per_order
        exchange = turnover * self.exchange_txn
        sebi = turnover * self.sebi_fees
        stt = turnover * self.stt                      # both legs
        stamp = buy_turnover * self.stamp_duty_buy     # buy side only
        gst = (brokerage + exchange + sebi) * self.gst_rate

        return round(
            brokerage + exchange + sebi + stt + stamp + gst + self.dp_charge_per_sell, 4
        )

    def describe(self) -> str:
        return (
            f"delivery: STT {self.stt * 100:g}% both legs, "
            f"stamp {self.stamp_duty_buy * 100:g}% buy, "
            f"DP Rs{self.dp_charge_per_sell:g}/sell, "
            f"brokerage Rs{self.brokerage_per_order:g}/order"
        )


```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_costs_delivery.py tests/test_costs.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add costs.py tests/test_costs_delivery.py
git commit -m "feat(costs): charge overnight trades delivery rates

STT on both legs, higher stamp duty and a depository charge. An overnight
position priced at intraday rates flattered every multi-day backtest by
about 0.15% per trade.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Holding-aware cost model

**Files:**
- Modify: `costs.py` (imports; class after `DeliveryCostModel`)
- Test: `tests/test_costs_delivery.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_costs_delivery.py`, replace the import block
```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel  # noqa: E402
```
with
```python
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel, HoldingCostModel  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)
```
and append:
```python
def test_same_day_trade_is_charged_intraday():
    got = HoldingCostModel().round_trip(
        1000.0, 1010.0, 100, entry_ts=ist(2026, 8, 3, 9, 30), exit_ts=ist(2026, 8, 3, 15, 20)
    )
    assert got == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_trade_held_overnight_is_charged_delivery():
    got = HoldingCostModel().round_trip(
        1000.0, 1010.0, 100, entry_ts=ist(2026, 8, 3, 15, 0), exit_ts=ist(2026, 8, 4, 10, 0)
    )
    assert got == pytest.approx(DeliveryCostModel().round_trip(1000.0, 1010.0, 100))


def test_the_day_boundary_is_india_midnight_not_utc():
    entry, exit_ = ist(2026, 8, 3, 23, 50), ist(2026, 8, 4, 0, 10)
    assert entry.astimezone(UTC).date() == exit_.astimezone(UTC).date()
    assert HoldingCostModel().is_delivery(entry, exit_)


def test_missing_timestamps_are_refused():
    with pytest.raises(ValueError, match="entry_ts and exit_ts"):
        HoldingCostModel().round_trip(1000.0, 1010.0, 100)


def test_only_the_holding_model_asks_for_timestamps():
    assert HoldingCostModel.prices_by_holding is True
    assert not getattr(CostModel(), "prices_by_holding", False)
    assert not getattr(DeliveryCostModel(), "prices_by_holding", False)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_costs_delivery.py -v`
Expected: `ImportError: cannot import name 'HoldingCostModel'`.

- [ ] **Step 3: Update imports**

In `costs.py`, replace `from dataclasses import dataclass` with:
```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import ClassVar

from config import IST
```

- [ ] **Step 4: Add the class**

In `costs.py`, directly after `class DeliveryCostModel` (before `FlatCostModel`), insert:
```python
@dataclass(frozen=True)
class HoldingCostModel:
    """Intraday charges for a same-day trade, delivery charges otherwise.

    Decided per TRADE, not per strategy: an intraday strategy that happens to
    hold a position overnight pays delivery charges for that trade, which is
    what a broker would actually charge. The day boundary is India midnight.
    """

    intraday: CostModel = field(default_factory=CostModel)
    delivery: DeliveryCostModel = field(default_factory=DeliveryCostModel)

    # Read by backtest._make_trade: this model needs the trade's timestamps.
    prices_by_holding: ClassVar[bool] = True

    def is_delivery(self, entry_ts: datetime, exit_ts: datetime) -> bool:
        return entry_ts.astimezone(IST).date() != exit_ts.astimezone(IST).date()

    def round_trip(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        *,
        entry_ts: datetime | None = None,
        exit_ts: datetime | None = None,
    ) -> float:
        if entry_ts is None or exit_ts is None:
            raise ValueError(
                "HoldingCostModel needs entry_ts and exit_ts: without them it "
                "cannot tell a same-day trade from an overnight one."
            )
        model = self.delivery if self.is_delivery(entry_ts, exit_ts) else self.intraday
        return model.round_trip(entry_price, exit_price, quantity)

    def describe(self) -> str:
        return f"same day: {self.intraday.describe()}; overnight: {self.delivery.describe()}"


```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_costs_delivery.py tests/test_costs.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add costs.py tests/test_costs_delivery.py
git commit -m "feat(costs): choose intraday or delivery charges per trade

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Wire holding-aware fees into the simulator

**Files:**
- Modify: `backtest.py:30` (import), `backtest.py:136-142` (`_make_trade` costs), `backtest.py:509-519` (`build_cost_model`)
- Modify: `config.py:51-54` (comment)
- Test: `tests/test_backtest_holding_costs.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_backtest_holding_costs.py`:
```python
"""The simulator's single fee seam charges overnight trades delivery rates.

_make_trade is called by both the v2 simulator and the v3 state machine, so
this one seam covers every strategy format.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import _make_trade, build_cost_model  # noqa: E402
from config import Settings  # noqa: E402
from costs import CostModel, DeliveryCostModel, FlatCostModel, HoldingCostModel  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")


def ist(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def make(entry_ts, exit_ts, model):
    return _make_trade(
        entry_signal_ts=entry_ts, entry_fill_ts=entry_ts,
        exit_signal_ts=exit_ts, exit_fill_ts=exit_ts,
        position_type="long", quantity=100,
        intended_entry=1000.0, entry_price=1000.0,
        intended_exit=1010.0, exit_price=1010.0,
        exit_reason="signal", cost_per_trade_inr=30.0, cost_model=model,
    )


def test_overnight_trade_pays_delivery_charges():
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), HoldingCostModel())
    assert t.costs == pytest.approx(DeliveryCostModel().round_trip(1000.0, 1010.0, 100))
    assert t.net_pnl == pytest.approx(t.gross_pnl - t.costs, abs=1e-3)


def test_same_day_trade_pays_intraday_charges():
    t = make(ist(2026, 8, 3, 10, 0), ist(2026, 8, 3, 14, 0), HoldingCostModel())
    assert t.costs == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_the_plain_itemised_model_is_unchanged_even_overnight():
    """Existing stored results used CostModel; they must stay reproducible."""
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), CostModel())
    assert t.costs == pytest.approx(CostModel().round_trip(1000.0, 1010.0, 100))


def test_no_model_still_charges_the_flat_amount():
    t = make(ist(2026, 8, 3, 15, 0), ist(2026, 8, 4, 10, 0), None)
    assert t.costs == 30.0


def settings(cost_model: str) -> Settings:
    return Settings(supabase_url="", supabase_service_role_key="", cost_model=cost_model)


def test_cost_model_holding_selects_the_holding_model():
    assert isinstance(build_cost_model(settings("holding")), HoldingCostModel)


def test_existing_cost_model_choices_are_unchanged():
    itemised = build_cost_model(settings("itemised"))
    assert isinstance(itemised, CostModel) and not isinstance(itemised, HoldingCostModel)
    assert isinstance(build_cost_model(settings("flat")), FlatCostModel)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_backtest_holding_costs.py -v`
Expected: `test_overnight_trade_pays_delivery_charges` FAILS with `ValueError: HoldingCostModel needs entry_ts and exit_ts`, and `test_cost_model_holding_selects_the_holding_model` FAILS because `build_cost_model` returns a `FlatCostModel`.

- [ ] **Step 3: Update the import**

In `backtest.py`, replace `from costs import CostModel, FlatCostModel` with:
```python
from costs import CostModel, FlatCostModel, HoldingCostModel
```

- [ ] **Step 4: Pass timestamps at the seam**

In `backtest.py` `_make_trade`, replace:
```python
    # An itemised model prices this trade's OWN turnover; the flat fallback
    # ignores it. Defaulting to flat keeps every existing result reproducible.
    costs = (
        cost_model.round_trip(entry_price, exit_price, quantity)
        if cost_model is not None
        else cost_per_trade_inr
    )
```
with:
```python
    # An itemised model prices this trade's OWN turnover; the flat fallback
    # ignores it. Defaulting to flat keeps every existing result reproducible.
    # A holding-aware model also needs to know whether the trade crossed a
    # day, because an overnight position pays delivery charges.
    if cost_model is None:
        costs = cost_per_trade_inr
    elif getattr(cost_model, "prices_by_holding", False):
        costs = cost_model.round_trip(
            entry_price, exit_price, quantity,
            entry_ts=entry_fill_ts, exit_ts=exit_fill_ts,
        )
    else:
        costs = cost_model.round_trip(entry_price, exit_price, quantity)
```

- [ ] **Step 5: Add the setting**

In `backtest.py` `build_cost_model`, replace:
```python
    if getattr(settings, "cost_model", "flat") == "itemised":
        return CostModel()
    return FlatCostModel(settings.cost_per_trade_inr)
```
with:
```python
    choice = getattr(settings, "cost_model", "flat")
    if choice == "holding":
        return HoldingCostModel()
    if choice == "itemised":
        return CostModel()
    return FlatCostModel(settings.cost_per_trade_inr)
```

- [ ] **Step 6: Document it in config**

In `config.py`, replace:
```python
# 'flat' charges a fixed rupee amount per round trip; 'itemised' models real
# Indian intraday charges, which scale with turnover. Flat is the default so
```
with:
```python
# 'flat' charges a fixed rupee amount per round trip; 'itemised' models real
# Indian intraday charges, which scale with turnover; 'holding' is itemised
# plus delivery charges for any trade held overnight. Flat is the default so
```

- [ ] **Step 7: Run to verify pass, then the whole suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_backtest_holding_costs.py -v`
Expected: all PASS.

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: 0 failed.

- [ ] **Step 8: Commit**

```bash
git add backtest.py config.py tests/test_backtest_holding_costs.py
git commit -m "feat(backtest): COST_MODEL=holding charges overnight trades delivery rates

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Research universe

**Files:**
- Create: `research/__init__.py`, `research/universe.py`
- Test: `tests/test_research_universe.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_universe.py`:
```python
"""Which symbols and timeframes a research run tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.universe import (  # noqa: E402
    INDEX_TIMEFRAMES,
    INDEXES,
    STOCK_TIMEFRAMES,
    Combo,
    document_uses_volume,
    research_combos,
)


def test_two_hundred_stocks_and_nine_indexes_make_1218_combinations():
    stocks = [f"NSE:S{i}" for i in range(200)]
    combos = research_combos(stocks, include_indexes=True)
    assert len(INDEXES) == 9
    assert len(combos) == 200 * len(STOCK_TIMEFRAMES) + 9 * len(INDEX_TIMEFRAMES) == 1218


def test_indexes_are_only_tested_on_hourly_and_daily():
    combos = research_combos([], include_indexes=True)
    assert {c.timeframe for c in combos} == {"60m", "day"}
    assert all(c.is_index for c in combos)


def test_indexes_can_be_left_out():
    combos = research_combos(["NSE:A"], include_indexes=False)
    assert combos == [Combo("NSE:A", tf, False) for tf in STOCK_TIMEFRAMES]


def test_stock_timeframes_can_be_narrowed_for_a_quick_run():
    combos = research_combos(["NSE:A"], include_indexes=False, stock_timeframes=("day",))
    assert combos == [Combo("NSE:A", "day", False)]


def test_v2_volume_operand_counts_as_a_volume_rule():
    doc = {"entry": {"all": [{"indicator": "volume", "operator": ">",
                              "compare_to": {"indicator": "sma", "source": "volume",
                                             "params": {"period": 20}}}]},
           "exit": {"any": []}}
    assert document_uses_volume(doc)


def test_v3_vwap_expression_counts_as_a_volume_rule():
    doc = {"version": 3, "states": [{"name": "a", "transitions": [
        {"when": "close > vwap()", "goto": "a"}]}]}
    assert document_uses_volume(doc)


def test_a_name_mentioning_volume_does_not_count():
    doc = {"name": "VOLUME-IDEA", "description": "volume someday",
           "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
           "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]}}
    assert not document_uses_volume(doc)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_universe.py -v`
Expected: `ModuleNotFoundError: No module named 'research'`.

- [ ] **Step 3: Create the package marker**

Create `research/__init__.py`:
```python
"""The autonomous research loop.

Design: docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md
"""
```

- [ ] **Step 4: Implement**

Create `research/universe.py`:
```python
"""What a research run tests: which symbols, on which timeframes.

Indexes are tested on 60m and daily only, because free intraday index history
does not reach further back than Yahoo's 730-day 60m window. A strategy whose
rules read volume is not tested on indexes at all: Yahoo's index "volume" is
not real exchange volume, and a result built on it would look like evidence.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

STOCK_TIMEFRAMES: tuple[str, ...] = ("5m", "15m", "25m", "30m", "60m", "day")
INDEX_TIMEFRAMES: tuple[str, ...] = ("60m", "day")

# Store symbol -> Yahoo symbol. Store symbols are rows already in the
# `instruments` table, so each index gets a real instrument_id and an ordinary
# parquet folder like any stock.
INDEXES: dict[str, str] = {
    "NSE:NIFTY": "^NSEI",
    "NSE:BANKNIFTY": "^NSEBANK",
    "NSE:NIFTYIT": "^CNXIT",
    "NSE:NIFTYAUTO": "^CNXAUTO",
    "NSE:NIFTYPHARMA": "^CNXPHARMA",
    "NSE:NIFTYFMCG": "^CNXFMCG",
    "NSE:NIFTYMETAL": "^CNXMETAL",
    "NSE:FINNIFTY": "NIFTY_FIN_SERVICE.NS",
    "NSE:NIFTYMID100FREE": "NIFTY_MIDCAP_100.NS",
}

# Everything that reads volume, by name, in either strategy format.
_VOLUME_WORDS = re.compile(r"\b(volume|vwap|obv|mfi|cmf|vwma)\b", re.IGNORECASE)

# Only the parts of a document that are rules. A strategy NAMED "VOLUME-IDEA"
# must not lose its index tests.
_RULE_KEYS = ("entry", "exit", "states")


@dataclass(frozen=True)
class Combo:
    symbol: str
    timeframe: str
    is_index: bool


def _mentions_volume(node: Any) -> bool:
    if isinstance(node, str):
        return bool(_VOLUME_WORDS.search(node))
    if isinstance(node, Mapping):
        return any(_mentions_volume(k) or _mentions_volume(v) for k, v in node.items())
    if isinstance(node, Sequence):
        return any(_mentions_volume(item) for item in node)
    return False


def document_uses_volume(doc: Mapping[str, Any]) -> bool:
    """True when any rule in a v2 or v3 strategy document reads volume."""
    return any(_mentions_volume(doc.get(key)) for key in _RULE_KEYS)


def research_combos(
    stocks: Iterable[str],
    *,
    include_indexes: bool,
    stock_timeframes: Sequence[str] = STOCK_TIMEFRAMES,
) -> list[Combo]:
    """Every stock x timeframe, then every index x timeframe. Order is stable."""
    combos = [Combo(s, tf, False) for s in stocks for tf in stock_timeframes]
    if include_indexes:
        combos += [Combo(s, tf, True) for s in INDEXES for tf in INDEX_TIMEFRAMES]
    return combos
```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_universe.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add research/__init__.py research/universe.py tests/test_research_universe.py
git commit -m "feat(research): the combinations a research run tests

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Research windows

**Files:**
- Create: `research/windows.py`
- Test: `tests/test_research_windows.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_windows.py`:
```python
"""The frozen calendar: where prices end, where training ends, what is locked."""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.windows import (  # noqa: E402
    ResearchWindows,
    data_end_from,
    ist_midnight,
    training_start,
    trim_to_data_end,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
TICK = timedelta(microseconds=1)


def five_min(day: date, last: time) -> pd.DatetimeIndex:
    t = datetime.combine(day, time(9, 15), tzinfo=IST)
    stamps = []
    while t.time() <= last:
        stamps.append(t.astimezone(UTC))
        t += timedelta(minutes=5)
    return pd.DatetimeIndex(stamps)


def daily(*days: date) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([ist_midnight(d) for d in days])


def test_india_midnight_is_1830_utc_the_day_before():
    assert ist_midnight(date(2026, 8, 27)) == datetime(2026, 8, 26, 18, 30, tzinfo=UTC)


def test_a_day_that_stops_before_the_last_candle_is_not_data_end():
    idx = five_min(date(2026, 8, 27), time(15, 25)).append(five_min(date(2026, 8, 28), time(15, 10)))
    assert data_end_from(idx, daily(date(2026, 8, 27), date(2026, 8, 28))) == date(2026, 8, 27)


def test_data_end_is_limited_by_the_daily_candles():
    idx = five_min(date(2026, 8, 27), time(15, 25)).append(five_min(date(2026, 8, 28), time(15, 25)))
    assert data_end_from(idx, daily(date(2026, 8, 27))) == date(2026, 8, 27)


def test_empty_history_is_refused():
    with pytest.raises(ValueError):
        data_end_from(pd.DatetimeIndex([], tz="UTC"), daily(date(2026, 8, 27)))


def test_the_locked_year_is_365_days_ending_on_data_end():
    w = ResearchWindows(date(2026, 8, 27))
    assert w.locked_from == date(2025, 8, 28)
    assert w.locked_from_utc == ist_midnight(date(2025, 8, 28))


def test_training_ends_one_tick_before_the_locked_year():
    w = ResearchWindows(date(2026, 8, 27))
    start, end = w.training(is_index=False, timeframe="15m")
    assert start == ist_midnight(date(2017, 4, 3))
    assert end + TICK == w.locked_from_utc


def test_the_full_window_ends_at_the_last_moment_of_data_end():
    w = ResearchWindows(date(2026, 8, 27))
    _, end = w.full(is_index=True, timeframe="60m")
    assert end + TICK == ist_midnight(date(2026, 8, 28))


def test_training_start_depends_on_kind_and_timeframe():
    assert training_start(is_index=False, timeframe="day") == date(2010, 1, 1)
    assert training_start(is_index=True, timeframe="day") == date(2010, 1, 1)
    assert training_start(is_index=True, timeframe="60m") == date(2023, 10, 4)
    assert training_start(is_index=False, timeframe="5m") == date(2017, 4, 3)


def test_training_days_counts_calendar_days():
    w = ResearchWindows(date(2026, 8, 27))
    assert w.training_days(is_index=True, timeframe="60m") == (date(2025, 8, 28) - date(2023, 10, 4)).days


def test_trim_keeps_data_end_and_drops_later_candles():
    idx = pd.DatetimeIndex([
        datetime(2026, 8, 27, 15, 25, tzinfo=IST).astimezone(UTC),
        datetime(2026, 8, 28, 9, 15, tzinfo=IST).astimezone(UTC),
    ])
    frame = pd.DataFrame({"close": [1.0, 2.0]}, index=idx)
    assert list(trim_to_data_end(frame, date(2026, 8, 27))["close"]) == [1.0]
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_windows.py -v`
Expected: `ModuleNotFoundError: No module named 'research.windows'`.

- [ ] **Step 3: Implement**

Create `research/windows.py`:
```python
"""The frozen research calendar.

DATA_END is the last date complete in every stored timeframe. The LOCKED year
is the 365 days ending on it; everything a strategy is built from comes before
it. Windows are closed intervals [start, end] in UTC, matching what
ParquetCandleBackend.read_candles expects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from config import IST, UTC

LAST_BAR_START_IST = time(15, 25)     # the final 5-minute candle of an NSE session
LOCKED_DAYS = 365

DAILY_TRAINING_START = date(2010, 1, 1)
STOCK_INTRADAY_TRAINING_START = date(2017, 4, 3)    # where Dhan's intraday archive begins
INDEX_INTRADAY_TRAINING_START = date(2023, 10, 4)   # where Yahoo's 60m index history began

_TICK = timedelta(microseconds=1)


def ist_midnight(day: date) -> datetime:
    """The instant a trading day begins in India, in UTC."""
    return datetime.combine(day, time(0), tzinfo=IST).astimezone(UTC)


def training_start(*, is_index: bool, timeframe: str) -> date:
    if timeframe == "day":
        return DAILY_TRAINING_START
    return INDEX_INTRADAY_TRAINING_START if is_index else STOCK_INTRADAY_TRAINING_START


def data_end_from(five_min_index: pd.DatetimeIndex, day_index: pd.DatetimeIndex) -> date:
    """Last date with a full 5-minute session AND a daily candle.

    Measured from the data rather than a holiday calendar, because the holiday
    file only covers the current year.
    """
    if len(five_min_index) == 0 or len(day_index) == 0:
        raise ValueError(
            "cannot place DATA_END: the reference symbol has no 5-minute or no daily candles"
        )
    local = pd.DatetimeIndex(five_min_index).tz_convert(IST)
    complete = [d for d, t in zip(local.date, local.time) if t >= LAST_BAR_START_IST]
    if not complete:
        raise ValueError("no complete 5-minute session: no candle starts at 15:25 IST")
    last_daily = pd.DatetimeIndex(day_index).tz_convert(IST).max().date()
    return min(max(complete), last_daily)


def trim_to_data_end(frame: pd.DataFrame, data_end: date) -> pd.DataFrame:
    """Drop every candle that starts after DATA_END."""
    return frame[frame.index < ist_midnight(data_end + timedelta(days=1))]


@dataclass(frozen=True)
class ResearchWindows:
    data_end: date

    @property
    def locked_from(self) -> date:
        return self.data_end - timedelta(days=LOCKED_DAYS - 1)

    @property
    def locked_from_utc(self) -> datetime:
        return ist_midnight(self.locked_from)

    @property
    def end_utc(self) -> datetime:
        return ist_midnight(self.data_end + timedelta(days=1)) - _TICK

    def training(self, *, is_index: bool, timeframe: str) -> tuple[datetime, datetime]:
        start = ist_midnight(training_start(is_index=is_index, timeframe=timeframe))
        return start, self.locked_from_utc - _TICK

    def training_days(self, *, is_index: bool, timeframe: str) -> int:
        return (self.locked_from - training_start(is_index=is_index, timeframe=timeframe)).days

    def full(self, *, is_index: bool, timeframe: str) -> tuple[datetime, datetime]:
        start = ist_midnight(training_start(is_index=is_index, timeframe=timeframe))
        return start, self.end_utc
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_windows.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/windows.py tests/test_research_windows.py
git commit -m "feat(research): the frozen calendar - DATA_END, training, locked year

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Frozen price reader

**Files:**
- Create: `research/prices.py`
- Test: `tests/test_research_prices.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_prices.py`:
```python
"""Reading frozen price history without ever fetching."""

from __future__ import annotations

import pickle
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from parquet_candle_backend import ParquetCandleBackend  # noqa: E402
from price_adjust import Adjustment  # noqa: E402
from research.prices import FrozenPriceReader  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
FROM = datetime(2026, 1, 1, tzinfo=UTC)
TO = datetime(2026, 12, 31, tzinfo=UTC)


def bars(start_ist: datetime, n: int, step_min: int, base: float = 100.0) -> pd.DataFrame:
    idx = pd.DatetimeIndex([(start_ist + timedelta(minutes=step_min * i)).astimezone(UTC) for i in range(n)])
    return pd.DataFrame(
        {
            "open": [base + i for i in range(n)],
            "high": [base + i + 1 for i in range(n)],
            "low": [base + i - 1 for i in range(n)],
            "close": [base + i + 0.5 for i in range(n)],
            "volume": [1000.0] * n,
        },
        index=idx,
    )


def write(root: Path, instrument_id: int, timeframe: str, frame: pd.DataFrame) -> None:
    ParquetCandleBackend(None, str(root)).write_candles(instrument_id, timeframe, frame)


HALF = Adjustment(
    effective_from=date(2026, 8, 1), effective_to=date(2026, 8, 31),
    price_factor=0.5, volume_factor=1.0, sample_days=10,
)


def test_stock_intraday_is_resampled_from_the_5_minute_base(tmp_path):
    write(tmp_path, 7, "5m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 6, 5))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {}, frozenset())
    got = reader.candles("NSE:ABC", "15m", FROM, TO)
    assert len(got) == 2
    assert got["open"].iloc[0] == 100.0
    assert got["close"].iloc[0] == 102.5


def test_stock_intraday_applies_stored_corrections(tmp_path):
    write(tmp_path, 7, "5m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 3, 5))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset())
    got = reader.candles("NSE:ABC", "5m", FROM, TO)
    assert got["open"].iloc[0] == pytest.approx(50.0)


def test_daily_candles_are_read_directly_without_corrections(tmp_path):
    write(tmp_path, 7, "day", bars(datetime(2026, 8, 3, 0, 0, tzinfo=IST), 3, 1440))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset())
    got = reader.candles("NSE:ABC", "day", FROM, TO)
    assert list(got["open"]) == [100.0, 101.0, 102.0]


def test_index_timeframes_are_read_exactly_as_stored(tmp_path):
    write(tmp_path, 9, "60m", bars(datetime(2026, 8, 3, 9, 15, tzinfo=IST), 7, 60))
    reader = FrozenPriceReader(str(tmp_path), {"NSE:NIFTY": 9}, {}, frozenset({"NSE:NIFTY"}))
    assert len(reader.candles("NSE:NIFTY", "60m", FROM, TO)) == 7


def test_nothing_stored_is_an_empty_frame_not_a_fetch(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {}, frozenset())
    assert reader.candles("NSE:ABC", "15m", FROM, TO).empty


def test_an_unknown_symbol_is_a_key_error(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {}, {}, frozenset())
    with pytest.raises(KeyError):
        reader.candles("NSE:NOPE", "day", FROM, TO)


def test_the_reader_can_be_sent_to_a_worker_process(tmp_path):
    reader = FrozenPriceReader(str(tmp_path), {"NSE:ABC": 7}, {7: [HALF]}, frozenset({"NSE:NIFTY"}))
    assert pickle.loads(pickle.dumps(reader)) == reader
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_prices.py -v`
Expected: `ModuleNotFoundError: No module named 'research.prices'`.

- [ ] **Step 3: Implement**

Create `research/prices.py`:
```python
"""Read the frozen price history without ever fetching.

candle_store.CandleStore.get_candles fills gaps from the data provider. With
the Dhan subscription lapsed and prices deliberately frozen, a gap must show
up as a missing combination, never as a network call - so research reads the
parquet files directly, applies the stored corrections, and resamples.

The reader holds only plain data, so it can be pickled into worker processes;
everything that needs the database happens once, in the parent, via the
loaders below.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pandas as pd

from config import source_timeframe_for
from parquet_candle_backend import ParquetCandleBackend
from price_adjust import Adjustment, apply_adjustments
from providers.base import empty_frame
from resample import resample_candles

_CHUNK = 100   # symbols or ids per Supabase `in` filter


@dataclass(frozen=True)
class FrozenPriceReader:
    root: str
    instrument_ids: dict[str, int]
    adjustments: dict[int, list[Adjustment]]    # 5-minute corrections by instrument id
    index_symbols: frozenset[str]

    def candles(
        self, symbol: str, timeframe: str, from_utc: datetime, to_utc: datetime
    ) -> pd.DataFrame:
        """Candles in [from_utc, to_utc]. Empty when nothing is stored.

        Raises KeyError for a symbol with no instrument id.
        """
        instrument_id = self.instrument_ids[symbol]
        backend = ParquetCandleBackend(None, self.root)

        if symbol in self.index_symbols:
            # Index timeframes are stored exactly as downloaded (60m and day).
            frame = backend.read_candles(instrument_id, timeframe, from_utc, to_utc)
            return frame if frame is not None else empty_frame()

        stored = source_timeframe_for(timeframe)
        base = backend.read_candles(instrument_id, stored, from_utc, to_utc)
        if base is None or base.empty:
            return empty_frame()
        if stored != "day":
            # Daily candles arrive already adjusted; see candle_store._price_adjustments.
            base = apply_adjustments(base, self.adjustments.get(instrument_id, []))
        return base if timeframe == stored else resample_candles(base, timeframe)


def _chunks(items: Sequence[Any], size: int) -> Iterator[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def load_instrument_ids(client: Any, symbols: Iterable[str]) -> dict[str, int]:
    """'NSE:RELIANCE' -> instruments.id for every symbol, or LookupError."""
    wanted = sorted(set(symbols))
    ids: dict[str, int] = {}
    for chunk in _chunks(wanted, _CHUNK):
        rows = (
            client.table("instruments").select("id,symbol")
            .in_("symbol", list(chunk)).execute().data
        )
        ids.update({row["symbol"]: int(row["id"]) for row in rows})
    missing = [s for s in wanted if s not in ids]
    if missing:
        raise LookupError(f"not in the instruments table: {', '.join(missing)}")
    return ids


def load_adjustments(client: Any, instrument_ids: Iterable[int]) -> dict[int, list[Adjustment]]:
    """5-minute corporate-action corrections, oldest first, for every id."""
    wanted = sorted(set(instrument_ids))
    out: dict[int, list[Adjustment]] = {i: [] for i in wanted}
    for chunk in _chunks(wanted, _CHUNK):
        rows = (
            client.table("price_adjustments")
            .select("instrument_id,effective_from,effective_to,price_factor,volume_factor,sample_days")
            .in_("instrument_id", list(chunk))
            .eq("timeframe", "5m")
            .order("effective_from")
            .execute().data
        )
        for row in rows:
            out[int(row["instrument_id"])].append(Adjustment(
                effective_from=date.fromisoformat(row["effective_from"]),
                effective_to=date.fromisoformat(row["effective_to"]),
                price_factor=float(row["price_factor"]),
                volume_factor=float(row["volume_factor"] or 1.0),
                sample_days=int(row["sample_days"]),
            ))
    return out
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_prices.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/prices.py tests/test_research_prices.py
git commit -m "feat(research): read frozen prices without ever fetching

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Loader tests

The loaders were written in Task 6; this task pins their behaviour against a
fake Supabase client.

**Files:**
- Test: `tests/test_research_prices.py`

- [ ] **Step 1: Write the tests**

Append to `tests/test_research_prices.py`:
```python
from types import SimpleNamespace  # noqa: E402

from research.prices import load_adjustments, load_instrument_ids  # noqa: E402


class FakeQuery:
    def __init__(self, rows, log):
        self._rows, self._log, self._filters = rows, log, []

    def select(self, columns):
        self._log.append(("select", columns))
        return self

    def in_(self, column, values):
        self._filters.append(("in", column, list(values)))
        return self

    def eq(self, column, value):
        self._filters.append(("eq", column, value))
        return self

    def order(self, column):
        return self

    def execute(self):
        rows = self._rows
        for kind, column, value in self._filters:
            rows = [r for r in rows if (r[column] in value if kind == "in" else r[column] == value)]
        return SimpleNamespace(data=rows)


class FakeClient:
    def __init__(self, tables):
        self.tables, self.log = tables, []

    def table(self, name):
        return FakeQuery(self.tables[name], self.log)


def test_instrument_ids_are_looked_up_in_chunks():
    rows = [{"id": i, "symbol": f"NSE:S{i}"} for i in range(250)]
    client = FakeClient({"instruments": rows})
    ids = load_instrument_ids(client, [r["symbol"] for r in rows])
    assert ids["NSE:S249"] == 249
    assert sum(1 for entry in client.log if entry[0] == "select") == 3


def test_a_missing_instrument_is_named():
    client = FakeClient({"instruments": [{"id": 1, "symbol": "NSE:A"}]})
    with pytest.raises(LookupError, match="NSE:B"):
        load_instrument_ids(client, ["NSE:A", "NSE:B"])


def test_adjustments_are_grouped_by_instrument_and_only_5_minute():
    rows = [
        {"instrument_id": 1, "timeframe": "5m", "effective_from": "2020-01-01",
         "effective_to": "2020-06-30", "price_factor": 0.1, "volume_factor": None, "sample_days": 5},
        {"instrument_id": 1, "timeframe": "1m", "effective_from": "2020-01-01",
         "effective_to": "2020-06-30", "price_factor": 0.1, "volume_factor": 10.0, "sample_days": 5},
    ]
    got = load_adjustments(FakeClient({"price_adjustments": rows}), [1, 2])
    assert got[2] == []
    assert len(got[1]) == 1
    assert got[1][0].effective_from == date(2020, 1, 1)
    assert got[1][0].volume_factor == 1.0
```

- [ ] **Step 2: Run**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_prices.py -v`
Expected: all PASS. If a loader test fails, fix `research/prices.py` (not the test) — the test states the intended behaviour.

- [ ] **Step 3: Commit**

```bash
git add tests/test_research_prices.py
git commit -m "test(research): pin instrument and correction loaders

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Parallel sweep

**Files:**
- Create: `research/sweep.py`
- Test: `tests/test_research_sweep.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_sweep.py`:
```python
"""One strategy across many combinations, serially or in worker processes."""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import HoldingCostModel  # noqa: E402
from parquet_candle_backend import ParquetCandleBackend  # noqa: E402
from research.prices import FrozenPriceReader  # noqa: E402
from research.sweep import ComboResult, count_results, run_combo, run_sweep  # noqa: E402
from research.universe import Combo  # noqa: E402
from strategy_schema import parse_strategy_dict  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")
WINDOW = (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 12, 31, tzinfo=UTC))
CLOSES = [100, 102, 103, 100, 102, 104, 99, 98, 102, 103, 99]


def daily_frame() -> pd.DataFrame:
    start = datetime(2026, 8, 3, 0, 0, tzinfo=IST)
    idx = pd.DatetimeIndex([(start + timedelta(days=i)).astimezone(UTC) for i in range(len(CLOSES))])
    return pd.DataFrame(
        {"open": [c - 0.5 for c in CLOSES], "high": [c + 1 for c in CLOSES],
         "low": [c - 1 for c in CLOSES], "close": [float(c) for c in CLOSES],
         "volume": [1000.0] * len(CLOSES)},
        index=idx,
    )


def strategy():
    return parse_strategy_dict({
        "name": "sweep-test", "enabled": False, "position_type": "long", "timeframe": "day",
        "instruments": ["NSE:ABC"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 101}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 101}]},
        "risk": {"stop_loss": {"type": "percent", "value": 20},
                 "target": {"type": "percent", "value": 50}},
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "max_cycles_per_day": 1,
    })


def reader(tmp_path) -> FrozenPriceReader:
    backend = ParquetCandleBackend(None, str(tmp_path))
    backend.write_candles(1, "day", daily_frame())
    backend.write_candles(2, "day", daily_frame())
    return FrozenPriceReader(str(tmp_path), {"NSE:ABC": 1, "NSE:DEF": 2, "NSE:EMPTY": 3}, {}, frozenset())


def run(combo, rdr):
    return run_combo(combo, strategy(), rdr, WINDOW, slippage_pct=0.05, cost_model=HoldingCostModel())


def test_a_stored_combination_produces_trades(tmp_path):
    result = run(Combo("NSE:ABC", "day", False), reader(tmp_path))
    assert result.skipped_reason is None
    assert len(result.trades) >= 1
    assert result.first_candle == daily_frame().index[0].to_pydatetime()


def test_no_candles_is_a_skip_not_an_error(tmp_path):
    result = run(Combo("NSE:EMPTY", "day", False), reader(tmp_path))
    assert result.skipped_reason == "no candles in window"
    assert result.trades == ()


def test_an_unreadable_combination_is_a_skip_with_the_reason(tmp_path):
    result = run(Combo("NSE:UNKNOWN", "day", False), reader(tmp_path))
    assert result.skipped_reason.startswith("price read failed")


def test_workers_give_the_same_results_as_serial(tmp_path):
    rdr = reader(tmp_path)
    combos = [Combo("NSE:ABC", "day", False), Combo("NSE:DEF", "day", False),
              Combo("NSE:EMPTY", "day", False)]

    def go(workers):
        return run_sweep(strategy(), combos, rdr, lambda c: WINDOW,
                         slippage_pct=0.05, cost_model=HoldingCostModel(), workers=workers)

    assert go(1) == go(2)


def test_counts_separate_skipped_tested_and_profitable(tmp_path):
    some_trade = run(Combo("NSE:ABC", "day", False), reader(tmp_path)).trades[0]
    results = [
        ComboResult("A", "day", False, (replace(some_trade, net_pnl=500.0),)),
        ComboResult("B", "day", False, (replace(some_trade, net_pnl=-500.0),)),
        ComboResult("C", "day", False, (), "no candles in window"),
    ]
    counts = count_results(results)
    assert (counts.tested, counts.skipped, counts.profitable) == (2, 1, 1)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_sweep.py -v`
Expected: `ModuleNotFoundError: No module named 'research.sweep'`.

- [ ] **Step 3: Implement**

Create `research/sweep.py`:
```python
"""Run one strategy over many stock x timeframe combinations.

Each combination is independent - one position at a time per symbol - so the
work splits cleanly across processes. Workers receive the strategy, the price
reader and the fee model once, at start-up; each task carries only a Combo and
its window. Results come back in the order the combinations were given.

A combination that cannot be read or simulated becomes a skip with a reason,
never an exception that ends the run: one young stock with too little history
for an ATR must not cost the other 1,217 results.
"""

from __future__ import annotations

import multiprocessing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backtest import simulate_any
from backtest_types import SimTrade
from research.prices import FrozenPriceReader
from research.universe import Combo

Window = tuple[datetime, datetime]


@dataclass(frozen=True)
class ComboResult:
    symbol: str
    timeframe: str
    is_index: bool
    trades: tuple[SimTrade, ...]
    skipped_reason: str | None = None
    # When this combination's candles actually begin inside the window. A
    # stock listed in 2024 has far fewer training days than the window allows,
    # and annualising its return over the whole window would understate it.
    first_candle: datetime | None = None

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)


@dataclass(frozen=True)
class SweepCounts:
    tested: int
    skipped: int
    profitable: int


def run_combo(
    combo: Combo,
    strategy: Any,
    reader: FrozenPriceReader,
    window: Window,
    *,
    slippage_pct: float,
    cost_model: Any,
) -> ComboResult:
    def skip(reason: str) -> ComboResult:
        return ComboResult(combo.symbol, combo.timeframe, combo.is_index, (), reason)

    try:
        frame = reader.candles(combo.symbol, combo.timeframe, *window)
    except Exception as exc:        # noqa: BLE001 - becomes a recorded skip
        return skip(f"price read failed: {exc!r}")
    if len(frame) < 2:
        return skip("no candles in window")
    try:
        result = simulate_any(
            frame, strategy,
            slippage_pct=slippage_pct, cost_per_trade_inr=0.0, cost_model=cost_model,
        )
    except Exception as exc:        # noqa: BLE001 - becomes a recorded skip
        return skip(f"simulation failed: {exc!r}")
    return ComboResult(
        combo.symbol, combo.timeframe, combo.is_index, tuple(result.trades),
        first_candle=frame.index[0].to_pydatetime(),
    )


_WORKER: dict[str, Any] = {}


def _init_worker(strategy: Any, reader: FrozenPriceReader, slippage_pct: float, cost_model: Any) -> None:
    _WORKER.update(strategy=strategy, reader=reader, slippage_pct=slippage_pct, cost_model=cost_model)


def _run_task(task: tuple[Combo, Window]) -> ComboResult:
    combo, window = task
    return run_combo(
        combo, _WORKER["strategy"], _WORKER["reader"], window,
        slippage_pct=_WORKER["slippage_pct"], cost_model=_WORKER["cost_model"],
    )


def run_sweep(
    strategy: Any,
    combos: Sequence[Combo],
    reader: FrozenPriceReader,
    window_for: Callable[[Combo], Window],
    *,
    slippage_pct: float,
    cost_model: Any,
    workers: int,
) -> list[ComboResult]:
    tasks = [(combo, window_for(combo)) for combo in combos]
    if workers <= 1:
        _init_worker(strategy, reader, slippage_pct, cost_model)
        return [_run_task(task) for task in tasks]
    # spawn: the only start method on Windows, and the same everywhere else,
    # so a run behaves identically on the laptop and on a Linux runner.
    context = multiprocessing.get_context("spawn")
    with context.Pool(
        workers, initializer=_init_worker,
        initargs=(strategy, reader, slippage_pct, cost_model),
    ) as pool:
        return pool.map(_run_task, tasks, chunksize=4)


def count_results(results: Sequence[ComboResult]) -> SweepCounts:
    tested = [r for r in results if r.skipped_reason is None]
    return SweepCounts(
        tested=len(tested),
        skipped=len(results) - len(tested),
        profitable=sum(1 for r in tested if r.net_pnl > 0),
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_sweep.py -v`
Expected: all PASS. The `workers=2` test takes a few seconds (process start-up).

- [ ] **Step 5: Commit**

```bash
git add research/sweep.py tests/test_research_sweep.py
git commit -m "feat(research): sweep one strategy over every combination in parallel

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: ₹1 lakh compounding and the locked verdict

**Files:**
- Create: `research/lakh.py`, `tests/research_helpers.py`
- Test: `tests/test_research_lakh.py`

- [ ] **Step 1: Create the shared test helper**

Create `tests/research_helpers.py`:
```python
"""Trade and fee-model builders shared by the research tests."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from backtest_types import SimTrade
from costs import CostModel, DeliveryCostModel, HoldingCostModel

IST = ZoneInfo("Asia/Kolkata")


def ist(y: int, mo: int, d: int, h: int = 10, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=IST)


def trade(
    *, entry: datetime, exit_: datetime, entry_price: float = 100.0,
    exit_price: float = 110.0, quantity: int = 1000, position_type: str = "long",
) -> SimTrade:
    if position_type == "long":
        gross = (exit_price - entry_price) * quantity
    else:
        gross = (entry_price - exit_price) * quantity
    return SimTrade(
        entry_signal_ts=entry, entry_fill_ts=entry, exit_signal_ts=exit_, exit_fill_ts=exit_,
        position_type=position_type, quantity=quantity,
        intended_entry_price=entry_price, entry_price=entry_price,
        intended_exit_price=exit_price, exit_price=exit_price,
        exit_reason="signal", gross_pnl=gross, costs=0.0, net_pnl=gross,
    )


def same_day(day: int, pct: float) -> SimTrade:
    """A trade on 2025-01-{day} returning `pct` percent gross."""
    return trade(entry=ist(2025, 1, day, 10), exit_=ist(2025, 1, day, 14),
                 entry_price=100.0, exit_price=100.0 * (1 + pct / 100))


FREE = HoldingCostModel(
    intraday=CostModel(brokerage_per_order=0.0, brokerage_pct=0.0, stt_sell=0.0,
                       exchange_txn=0.0, sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0),
    delivery=DeliveryCostModel(stt=0.0, exchange_txn=0.0, sebi_fees=0.0,
                               stamp_duty_buy=0.0, gst_rate=0.0, dp_charge_per_sell=0.0),
)
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_research_lakh.py`:
```python
"""What Rs 1 lakh becomes: compounding, fees on the real balance, holding."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import CostModel, DeliveryCostModel, HoldingCostModel  # noqa: E402
from research.lakh import LakhResult, compound, just_holding, passed  # noqa: E402
from research_helpers import FREE, ist, same_day, trade  # noqa: E402

UTC = ZoneInfo("UTC")


def test_no_trades_leaves_the_money_where_it_was():
    got = compound([], FREE, window_days=365)
    assert (got.end_value, got.trades, got.worst_dip_pct, got.cagr_pct) == (100_000.0, 0, 0.0, 0.0)


def test_gains_build_on_each_other():
    assert compound([same_day(2, 10), same_day(3, 10)], FREE, window_days=365).end_value == pytest.approx(121_000.0)


def test_worst_dip_is_measured_from_the_running_high():
    got = compound([same_day(2, 10), same_day(3, -20), same_day(6, 25)], FREE, window_days=365)
    assert got.worst_dip_pct == pytest.approx(20.0)
    assert got.end_value == pytest.approx(110_000.0)


def test_trades_are_applied_in_entry_order_whatever_order_they_arrive():
    a = compound([same_day(3, -20), same_day(2, 10)], FREE, window_days=365)
    b = compound([same_day(2, 10), same_day(3, -20)], FREE, window_days=365)
    assert a == b


def test_overnight_fees_are_recomputed_on_the_current_balance():
    stt_only = HoldingCostModel(
        intraday=FREE.intraday,
        delivery=DeliveryCostModel(stt=0.001, exchange_txn=0.0, sebi_fees=0.0,
                                   stamp_duty_buy=0.0, gst_rate=0.0, dp_charge_per_sell=0.0),
    )
    flat_overnight = [
        trade(entry=ist(2025, 1, 2, 15), exit_=ist(2025, 1, 3, 10), exit_price=100.0),
        trade(entry=ist(2025, 1, 6, 15), exit_=ist(2025, 1, 7, 10), exit_price=100.0),
    ]
    # fee 1 = 100,000 x 2 legs x 0.1% = 200 ; fee 2 = 99,800 x 2 x 0.1% = 199.6
    assert compound(flat_overnight, stt_only, window_days=365).end_value == pytest.approx(99_600.4)


def test_same_day_trades_use_intraday_fees():
    stt_sell_only = HoldingCostModel(
        intraday=CostModel(brokerage_per_order=0.0, brokerage_pct=0.0, exchange_txn=0.0,
                           sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0),
        delivery=FREE.delivery,
    )
    got = compound([same_day(2, 0)], stt_sell_only, window_days=365)
    assert got.end_value == pytest.approx(100_000.0 - 100_000.0 * 0.00025)


def test_a_short_trade_compounds_on_its_own_gross_return():
    short = trade(entry=ist(2025, 1, 2, 10), exit_=ist(2025, 1, 2, 14),
                  entry_price=100.0, exit_price=90.0, position_type="short")
    assert compound([short], FREE, window_days=365).end_value == pytest.approx(110_000.0)


def test_cagr_over_one_year_is_about_the_total_return():
    got = compound([same_day(2, 10)], FREE, window_days=365)
    assert got.cagr_pct == pytest.approx(10.0, abs=0.05)


def test_winning_trades_are_counted():
    got = compound([same_day(2, 10), same_day(3, -5), same_day(6, 1)], FREE, window_days=365)
    assert (got.trades, got.winning_trades) == (3, 2)


def day_candles(closes):
    start = datetime(2025, 8, 28, 0, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    idx = pd.DatetimeIndex([(start + timedelta(days=i)).astimezone(UTC) for i in range(len(closes))])
    return pd.DataFrame({"open": closes, "high": closes, "low": closes,
                         "close": [float(c) for c in closes], "volume": [0.0] * len(closes)}, index=idx)


def test_just_holding_buys_the_first_close_and_sells_the_last():
    assert just_holding(day_candles([100, 105, 120]), FREE) == pytest.approx(120_000.0)


def test_just_holding_pays_delivery_fees_once():
    stt_only = HoldingCostModel(intraday=FREE.intraday, delivery=DeliveryCostModel(
        stt=0.001, exchange_txn=0.0, sebi_fees=0.0, stamp_duty_buy=0.0, gst_rate=0.0, dp_charge_per_sell=0.0))
    # 1,000 units: fees = 1,000 x (100 + 120) x 0.1% = 220
    assert just_holding(day_candles([100, 120]), stt_only) == pytest.approx(119_780.0)


def test_just_holding_needs_two_candles():
    assert just_holding(day_candles([100]), FREE) is None


def result(end, trades, dip):
    return LakhResult(start_value=100_000.0, end_value=end, trades=trades,
                      winning_trades=0, worst_dip_pct=dip, cagr_pct=None)


def test_the_verdict_needs_profit_ten_trades_and_a_dip_no_deeper_than_20():
    assert passed(result(100_001.0, 10, 20.0))
    assert not passed(result(100_000.0, 10, 5.0))
    assert not passed(result(120_000.0, 9, 5.0))
    assert not passed(result(120_000.0, 30, 20.01))
```

- [ ] **Step 3: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_lakh.py -v`
Expected: `ModuleNotFoundError: No module named 'research.lakh'`.

- [ ] **Step 4: Implement**

Create `research/lakh.py`:
```python
"""What Rs 1 lakh would have become.

Trades are replayed in entry order. Each uses the whole balance at that
moment, and its fees are recomputed on that balance - so a strategy that has
doubled its money pays fees on double the turnover, exactly as it would in a
real account. Money earns nothing between trades.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from backtest_types import SimTrade
from costs import HoldingCostModel

START_VALUE = 100_000.0
MIN_LOCKED_TRADES = 10
MAX_LOCKED_DIP_PCT = 20.0


@dataclass(frozen=True)
class LakhResult:
    start_value: float
    end_value: float
    trades: int
    winning_trades: int
    worst_dip_pct: float        # 8.0 means the balance once fell 8% from a high
    cagr_pct: float | None


def compound(
    trades: Sequence[SimTrade],
    cost_model: HoldingCostModel,
    *,
    window_days: int,
    start_value: float = START_VALUE,
) -> LakhResult:
    balance = peak = start_value
    worst_dip = 0.0
    wins = 0

    for t in sorted(trades, key=lambda tr: tr.entry_fill_ts):
        notional = t.entry_price * t.quantity
        gross_return = t.gross_pnl / notional if notional else 0.0
        units = balance / t.entry_price
        fees = cost_model.round_trip(
            t.entry_price, t.exit_price, units,
            entry_ts=t.entry_fill_ts, exit_ts=t.exit_fill_ts,
        )
        change = balance * gross_return - fees
        if change > 0:
            wins += 1
        balance = max(balance + change, 0.0)
        peak = max(peak, balance)
        worst_dip = max(worst_dip, (peak - balance) / peak * 100 if peak > 0 else 0.0)
        if balance == 0.0:
            break

    years = window_days / 365.25
    cagr = None
    if years > 0 and balance > 0:
        cagr = round(((balance / start_value) ** (1 / years) - 1) * 100, 4)

    return LakhResult(
        start_value=start_value,
        end_value=round(balance, 2),
        trades=len(trades),
        winning_trades=wins,
        worst_dip_pct=round(worst_dip, 2),
        cagr_pct=cagr,
    )


def just_holding(
    day_candles: pd.DataFrame, cost_model: HoldingCostModel, *, start_value: float = START_VALUE
) -> float | None:
    """Buy at the first daily close, sell at the last, delivery fees once."""
    if len(day_candles) < 2:
        return None
    first = float(day_candles["close"].iloc[0])
    last = float(day_candles["close"].iloc[-1])
    units = start_value / first
    fees = cost_model.delivery.round_trip(first, last, units)
    return round(start_value + units * (last - first) - fees, 2)


def passed(locked: LakhResult) -> bool:
    """The locked-year verdict (spec §5.5)."""
    return (
        locked.end_value > locked.start_value
        and locked.trades >= MIN_LOCKED_TRADES
        and locked.worst_dip_pct <= MAX_LOCKED_DIP_PCT
    )
```

- [ ] **Step 5: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_lakh.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add research/lakh.py tests/research_helpers.py tests/test_research_lakh.py
git commit -m "feat(research): what Rs 1 lakh becomes, and the locked-year verdict

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: The pick rule

**Files:**
- Create: `research/picker.py`
- Test: `tests/test_research_picker.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_picker.py`:
```python
"""The fixed rule that picks one stock x timeframe from the training results."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.picker import pick_best  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402


def trades(n: int, pct: float, *, extra: tuple[float, ...] = ()):
    """n same-day trades returning `pct` percent each, then any `extra` returns."""
    returns = [pct] * n + list(extra)
    start = ist(2023, 1, 2, 10)
    return tuple(
        trade(entry=start + timedelta(days=i), exit_=start + timedelta(days=i, hours=4),
              entry_price=100.0, exit_price=100.0 * (1 + r / 100))
        for i, r in enumerate(returns)
    )


def combo(symbol, timeframe, trade_list, skipped=None):
    return ComboResult(symbol, timeframe, False, trade_list, skipped)


def pick(results):
    return pick_best(results, FREE, window_days_for=lambda r: 730)


def test_fewer_than_30_training_trades_never_qualifies():
    assert pick([combo("A", "day", trades(29, 5.0))]) is None


def test_a_training_dip_deeper_than_30_percent_disqualifies():
    assert pick([combo("A", "day", trades(30, 0.1, extra=(-40.0,)))]) is None


def test_the_highest_compounded_annual_return_wins():
    got = pick([combo("A", "day", trades(30, 0.5)), combo("B", "60m", trades(30, 1.0))])
    assert (got.result.symbol, got.result.timeframe) == ("B", "60m")


def test_a_tie_goes_to_more_trades():
    got = pick([combo("A", "day", trades(30, 1.0)), combo("B", "day", trades(30, 1.0, extra=(0.0,)))])
    assert got.result.symbol == "B"


def test_skipped_combinations_are_ignored():
    assert pick([combo("A", "day", (), "no candles in window")]) is None


def test_the_pick_carries_its_training_figures():
    got = pick([combo("A", "day", trades(30, 1.0))])
    assert got.training.trades == 30 and got.training.cagr_pct > 0
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_picker.py -v`
Expected: `ModuleNotFoundError: No module named 'research.picker'`.

- [ ] **Step 3: Implement**

Create `research/picker.py`:
```python
"""Pick one stock x timeframe from a version's TRAINING results (spec §5.3).

A fixed rule, not a judgement: among combinations with enough trades and a
survivable worst dip, the highest compounded annual return after fees. Ties go
to more trades, then to the earlier combination, so the same results always
produce the same pick.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from costs import HoldingCostModel
from research.lakh import LakhResult, compound
from research.sweep import ComboResult

MIN_TRAINING_TRADES = 30
MAX_TRAINING_DIP_PCT = 30.0


@dataclass(frozen=True)
class Pick:
    result: ComboResult
    training: LakhResult


def pick_best(
    results: Sequence[ComboResult],
    cost_model: HoldingCostModel,
    *,
    window_days_for: Callable[[ComboResult], int],
) -> Pick | None:
    candidates: list[tuple[int, Pick]] = []
    for order, result in enumerate(results):
        if result.skipped_reason is not None or len(result.trades) < MIN_TRAINING_TRADES:
            continue
        lakh = compound(result.trades, cost_model, window_days=window_days_for(result))
        if lakh.cagr_pct is None or lakh.worst_dip_pct > MAX_TRAINING_DIP_PCT:
            continue
        candidates.append((order, Pick(result, lakh)))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[1].training.cagr_pct, -item[1].training.trades, item[0]))
    return candidates[0][1]
```

- [ ] **Step 4: Run to verify pass**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_picker.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add research/picker.py tests/test_research_picker.py
git commit -m "feat(research): a fixed rule picks the stock and timeframe

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: One-time index history download

Time-sensitive: Yahoo's 60m index history is a rolling 730 days and shrinks
daily. Run this task as early as possible.

**Files:**
- Create: `scripts/download_index_history.py`

- [ ] **Step 1: Write the script**

Create `scripts/download_index_history.py`:
```python
"""Download index price history from Yahoo into the parquet store, once.

WHY
---
Dhan's data subscription has lapsed, and research tests nine indexes as well
as the NIFTY200 stocks. Yahoo serves index history free: daily back to
2007-2011, and 60-minute for a rolling 730 days. Intraday index history older
than that is not available free at all, so indexes are tested on 60m and day
only (research/universe.py).

Candles after --data-end are dropped, so indexes stop on the same day as the
frozen stock history. Re-running is safe: writes merge on timestamp.

    python scripts/download_index_history.py --data-end 2026-08-27

Afterwards, back the new files up:

    python scripts/backup_candles_to_storage.py
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import get_settings, use_utf8_stdout  # noqa: E402

DOWNLOADS = (("day", "1d", "max"), ("60m", "60m", "730d"))


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-end", required=True, type=date.fromisoformat,
                        help="last date to keep, YYYY-MM-DD (the frozen DATA_END)")
    parser.add_argument("--root", default="data/candles", help="parquet store root")
    args = parser.parse_args(argv)

    import yfinance as yf

    from db import SupabaseStore
    from parquet_candle_backend import ParquetCandleBackend
    from research.prices import load_instrument_ids
    from research.universe import INDEXES
    from research.windows import trim_to_data_end
    from yfinance_client import normalize_yf_frame

    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    store = SupabaseStore.connect(get_settings(require_supabase=True))
    ids = load_instrument_ids(store._client, INDEXES)
    backend = ParquetCandleBackend(None, args.root)

    failures = 0
    for symbol, yahoo_symbol in INDEXES.items():
        for timeframe, interval, period in DOWNLOADS:
            try:
                raw = yf.download(yahoo_symbol, interval=interval, period=period,
                                  progress=False, auto_adjust=False)
                frame = trim_to_data_end(normalize_yf_frame(raw), args.data_end)
            except Exception as exc:        # noqa: BLE001 - report and continue
                print(f"FAIL  {symbol:22s} {timeframe:4s} {exc!r}")
                failures += 1
                continue
            if frame.empty:
                print(f"FAIL  {symbol:22s} {timeframe:4s} Yahoo returned nothing")
                failures += 1
                continue
            frame["volume"] = frame["volume"].fillna(0.0)
            backend.write_candles(ids[symbol], timeframe, frame)
            print(f"OK    {symbol:22s} {timeframe:4s} {len(frame):6d} candles  "
                  f"{frame.index.min().date()} -> {frame.index.max().date()}")

    print("\nNext: python scripts/backup_candles_to_storage.py")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it**

Run: `./.venv/Scripts/python.exe scripts/download_index_history.py --data-end 2026-08-27`
Expected: 18 `OK` lines, no `FAIL`. Daily lines start 2007–2011; 60m lines start around 2023-10 (as far back as Yahoo's rolling window reached when measured on 2026-09-11 — later runs start later). Daily lines end `2026-08-26` and 60m lines end `2026-08-27`: the dates printed are UTC, and India midnight is 18:30 UTC the day before.

- [ ] **Step 3: Check the files landed**

Run: `ls data/candles/60m/10322 data/candles/day/10322`
Expected: year files, e.g. `2024.parquet 2025.parquet 2026.parquet` under 60m.

- [ ] **Step 4: Back up to Supabase Storage**

Run: `./.venv/Scripts/python.exe scripts/backup_candles_to_storage.py`
Expected: uploads only the new index files; ends without errors.

- [ ] **Step 5: Commit**

```bash
git add scripts/download_index_history.py
git commit -m "feat(data): download index history from Yahoo, frozen at DATA_END

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: The evaluate CLI

**Files:**
- Create: `research/evaluate.py`
- Test: `tests/test_research_evaluate.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_research_evaluate.py`:
```python
"""The pure parts of the hand-run evaluation command."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.evaluate import strategy_document  # noqa: E402


def test_bookkeeping_fields_are_stripped_before_parsing():
    docs = [{"name": "A", "status": "valid", "raw_source": "x", "validation_errors": [], "timeframe": "day"}]
    assert strategy_document(docs, "A") == {"name": "A", "timeframe": "day"}


def test_a_missing_strategy_names_the_available_ones():
    with pytest.raises(KeyError, match="B"):
        strategy_document([{"name": "B", "status": "valid"}], "A")


def test_a_draft_is_refused():
    with pytest.raises(ValueError, match="draft"):
        strategy_document([{"name": "A", "status": "draft"}], "A")
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_evaluate.py -v`
Expected: `ModuleNotFoundError: No module named 'research.evaluate'`.

- [ ] **Step 3: Implement**

Create `research/evaluate.py` with the helpers below, followed by the `main`
function and `__main__` guard shown after them:
```python
"""Evaluate one stored strategy the way the research loop will.

    python -m research.evaluate --strategy N200-PULLBACK-DAY
    python -m research.evaluate --strategy N200-PULLBACK-DAY --max-stocks 5 --stock-timeframes day

1. Test every stock x timeframe (and index x timeframe) on TRAINING years only.
2. Pick one combination by the fixed rule.
3. Open the locked final year ONCE, for that pick: what Rs 1 lakh became.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from config import IST, UTC, get_settings, use_utf8_stdout

BOOKKEEPING = ("status", "raw_source", "validation_errors")
FAR_PAST = datetime(2000, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
# How far back to scan 5-minute candles when placing DATA_END. Only the tail
# of a history can carry the answer, and reading nine years for 200 stocks
# costs about seven minutes before the sweep even starts.
SCAN_DAYS = 180


def strategy_document(docs: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    """The stored definition of `name`, ready for the parser."""
    for doc in docs:
        if doc.get("name") == name:
            if doc.get("status", "valid") != "valid":
                raise ValueError(f"strategy {name!r} is a draft and cannot be evaluated")
            return {k: v for k, v in doc.items() if k not in BOOKKEEPING}
    available = ", ".join(sorted(str(d.get("name")) for d in docs)) or "(none)"
    raise KeyError(f"no strategy named {name!r}. Available: {available}")


def _rupees(value: float | None) -> str:
    return "n/a" if value is None else f"Rs {value:,.0f}"
```

Then, in the same file:
```python
def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Evaluate one strategy across every combination.")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--max-stocks", type=int, default=0, help="test only the first N stocks (quick run)")
    parser.add_argument("--stock-timeframes", default="", help="comma-separated, e.g. day,60m (quick run)")
    args = parser.parse_args(argv)

    from backtest import simulate_any
    from costs import HoldingCostModel
    from db import SupabaseStore
    from research.lakh import compound, just_holding, passed
    from research.picker import pick_best
    from research.prices import FrozenPriceReader, load_adjustments, load_instrument_ids
    from research.sweep import count_results, run_sweep
    from research.universe import INDEXES, STOCK_TIMEFRAMES, document_uses_volume, research_combos
    from research.windows import (
        LOCKED_DAYS,
        ResearchWindows,
        data_end_from,
        universe_data_end,
    )
    from strategy.v3 import is_v3_document, parse_machine
    from strategy_schema import parse_strategy_dict
    from universes import newest_snapshot, parse_constituent_csv
    from walk_forward import split_trades

    settings = get_settings(require_supabase=True)
    store = SupabaseStore.connect(settings)

    doc = strategy_document(store.list_strategy_documents(), args.strategy)
    strategy = parse_machine(doc) if is_v3_document(doc) else parse_strategy_dict(doc)

    snapshot_path, as_of = newest_snapshot("NIFTY200")
    stocks = list(parse_constituent_csv(snapshot_path.read_text(encoding="utf-8")))
    if args.max_stocks:
        stocks = stocks[: args.max_stocks]
    timeframes = tuple(t.strip() for t in args.stock_timeframes.split(",") if t.strip()) or STOCK_TIMEFRAMES

    ids = load_instrument_ids(store._client, [*stocks, *INDEXES])
    stock_ids = [ids[s] for s in stocks]
    reader = FrozenPriceReader(settings.candle_root, ids, load_adjustments(store._client, stock_ids),
                               frozenset(INDEXES))

    # DATA_END comes from the whole universe, never one reference symbol. In
    # August 2026, 184 of 200 stocks lost their closing candles every day; a
    # single symbol would have let that month into the locked year.
    #
    # Daily candles are small, so they are read in full; the 5-minute scan
    # starts SCAN_DAYS before the newest daily candle, because only the tail
    # of a history can decide where it ends.
    day_index = {s: reader.candles(s, "day", FAR_PAST, FAR_FUTURE).index for s in stocks}
    newest_daily = max((idx.max() for idx in day_index.values() if len(idx)), default=None)
    if newest_daily is None:
        print("ERROR: no daily candles for any stock, so DATA_END cannot be placed",
              file=sys.stderr)
        return 1
    scan_from = newest_daily - timedelta(days=SCAN_DAYS)

    symbol_ends = []
    for symbol in stocks:
        try:
            symbol_ends.append(data_end_from(
                reader.candles(symbol, "5m", scan_from, FAR_FUTURE).index,
                day_index[symbol],
            ))
        except ValueError:
            continue        # no complete session in the scan window: counts against coverage
    data_end = universe_data_end(symbol_ends)
    windows = ResearchWindows(data_end)
    volume_rules = document_uses_volume(doc)
    combos = research_combos(stocks, include_indexes=not volume_rules, stock_timeframes=timeframes)
    cost_model = HoldingCostModel()

    print(f"strategy   {args.strategy}")
    print(f"prices     frozen at {data_end} (DATA_END, complete for "
          f"{len(symbol_ends)} of {len(stocks)} stocks)")
    print(f"training   before {windows.locked_from}")
    print(f"locked     {windows.locked_from} -> {data_end}  (opened once, at the end)")
    print(f"universe   NIFTY200 as of {as_of} ({len(stocks)} stocks)"
          + ("" if not volume_rules else "; indexes skipped: rules read volume"))
    print(f"testing    {len(combos)} combinations on {args.workers} worker(s)...")

    started = datetime.now(UTC)
    results = run_sweep(
        strategy, combos, reader,
        lambda c: windows.training(is_index=c.is_index, timeframe=c.timeframe),
        slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
    )
    elapsed = (datetime.now(UTC) - started).total_seconds()
    counts = count_results(results)
    print(f"\nTRAINING   {counts.tested} tested, {counts.skipped} skipped, "
          f"{counts.profitable} profitable after fees  ({elapsed:.0f}s)")
    for r in sorted((r for r in results if r.skipped_reason is None), key=lambda r: -r.net_pnl)[:10]:
        print(f"  {r.symbol:22s} {r.timeframe:4s} trades={len(r.trades):5d}  net={_rupees(r.net_pnl)}")

    pick = pick_best(
        results, cost_model,
        window_days_for=lambda r: windows.training_days(
            is_index=r.is_index, timeframe=r.timeframe,
            data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
        ),
    )
    if pick is None:
        print("\nNo qualifying pick (needs 30+ training trades and a worst dip within 30%). "
              "Locked year not opened.")
        return 0

    p = pick.result
    print(f"\nPICK       {p.symbol} · {p.timeframe}  (training: {pick.training.trades} trades, "
          f"CAGR {pick.training.cagr_pct:.2f}%, worst dip {pick.training.worst_dip_pct:.1f}%)")

    frame = reader.candles(p.symbol, p.timeframe, *windows.full(is_index=p.is_index, timeframe=p.timeframe))
    simulated = simulate_any(frame, strategy, slippage_pct=settings.slippage_pct,
                             cost_per_trade_inr=0.0, cost_model=cost_model)
    _, locked_trades = split_trades(simulated.trades, windows.locked_from_utc)
    locked = compound(locked_trades, cost_model, window_days=LOCKED_DAYS)
    hold = just_holding(reader.candles(p.symbol, "day", windows.locked_from_utc, windows.end_utc), cost_model)
    win_rate = 100 * locked.winning_trades / locked.trades if locked.trades else 0.0

    print("\nLOCKED YEAR")
    print(f"  Rs 1,00,000 -> {_rupees(locked.end_value)}")
    print(f"  just holding {p.symbol} -> {_rupees(hold)}"
          + ("  (the market's move; this strategy is short)" if strategy.position_type == "short" else ""))
    print(f"  {locked.trades} trades ({locked.trades / 12:.1f}/month) · won {win_rate:.0f}% · "
          f"worst dip {locked.worst_dip_pct:.1f}%")
    # Coverage is a floor, so up to 10% of stocks may end before DATA_END. If
    # the winner is one of them, its locked year is partly empty while still
    # being judged over a full 365 days.
    pick_last = frame.index[-1].astimezone(IST).date() if len(frame) else None
    if pick_last is not None and pick_last < data_end:
        print(f"  NOTE: this combination's candles stop {pick_last}, before DATA_END "
              f"{data_end} - its locked year is only partly covered")

    beat = hold is not None and locked.end_value > hold
    print(f"  verdict: {'PASSED' if passed(locked) else 'FAILED'} · beat holding: {'yes' if beat else 'no'}")
    print(f"  broad or lucky: profitable in {counts.profitable} of {counts.tested} training combinations")

    print("\nWARNINGS")
    print(f"  prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)")
    print(f"  {counts.tested} combinations tried: some look good in training by luck alone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the unit tests**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_research_evaluate.py -v`
Expected: all PASS.

- [ ] **Step 5: Quick real run**

Run: `./.venv/Scripts/python.exe -m research.evaluate --strategy N200-PULLBACK-DAY --max-stocks 5 --stock-timeframes day --workers 2`
Expected, in order: `prices     frozen at 2026-07-31`, `locked     2025-08-01 -> 2026-07-31`, `testing    23 combinations` (5 stocks × day + 9 indexes × 2), a `TRAINING` line, then either a `PICK` block with `LOCKED YEAR` or `No qualifying pick`. No traceback.

`--max-stocks 5` places DATA_END from those five stocks alone, so a quick run can legitimately print a later date than the full run. If the FULL run (Step 6) prints anything other than `2026-07-31`, STOP and report: the stored data has changed since it was measured on 2026-09-12.

- [ ] **Step 6: Full run**

Run: `./.venv/Scripts/python.exe -m research.evaluate --strategy N200-PULLBACK-DAY`
Expected: `testing    1218 combinations on 8 worker(s)...` (worker count = this machine's logical CPUs), then the full report. Note the `TRAINING` elapsed seconds — piece 4 needs it for the time budget.

- [ ] **Step 7: Commit**

```bash
git add research/evaluate.py tests/test_research_evaluate.py
git commit -m "feat(research): evaluate a strategy on training years, then the locked year once

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Final check

- [ ] **Step 1: Full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider`
Expected: 0 failed.

- [ ] **Step 2: Record the measured timing in the spec**

In `docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md`, change
the `**Status:**` line to:
```markdown
**Status:** Approved; piece 1 built · **Date:** 2026-09-11
```
and add under §10 item 1, after its *Done when* sentence:
```markdown
   *Measured:* one full evaluation on this laptop (8 logical CPUs) took
   <SECONDS> s for the training sweep.
```
replacing `<SECONDS>` with the number from Task 12 Step 6.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-09-11-autonomous-research-loop-design.md
git commit -m "docs(spec): piece 1 built, with the measured sweep time

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Amendments after review

Recorded here because the task texts above were written before review; where
they differ, the committed code and this section win.

- **Task 4** (`8bbbab0`): `VOLUME_INPUTS` is a public frozenset and the volume
  regex is built from it; a guard test fails if `strategy/vocabulary.py` gains
  an indicator that is not classified as volume or not-volume.
  `research_combos` rejects a bare string or an unknown timeframe.
- **Task 5**: a day is complete only with a candle starting exactly 15:25 IST;
  `DATA_END` is the latest date complete in BOTH stored timeframes;
  `ResearchWindows` rejects a non-`date` `data_end`; `training_start` rejects
  an unknown timeframe; `training_days(..., data_from=None)` shortens the
  window to when a combination's candles really begin.
- **Task 8** (text above already amended): `ComboResult.first_candle` records
  the first candle read for the combination.
- **Task 12** (text above already amended): the pick annualises each
  combination over `training_days(..., data_from=first candle's IST date)`.
- **Watch in Task 12 review:** the locked evaluation simulates the full window
  and keeps trades entered in the locked year, so a position still open at the
  boundary can delay the first locked-year entry. Accepted for piece 1; noted
  so it is judged deliberately.

- **DATA_END is 2026-07-31, decided across the universe** (owner's decision,
  2026-09-12). Measured from the stored files: every stock has all 23 July
  sessions complete, but in August 161 stocks have no complete session at all,
  23 have one, and only 16 run to the end of the month; 50 stocks' daily
  candles stop on 2026-08-21. The locked year is therefore
  **2025-08-01 → 2026-07-31**. `research/windows.py` gained
  `universe_data_end(symbol_ends, coverage=0.9)`, and Task 12 places DATA_END
  from every stock rather than from NSE:RELIANCE.
- **Task 11 still downloads index history through 2026-08-27.** Candles after
  DATA_END are simply never read, and keeping them costs nothing if the August
  stock data is ever repaired.
- **Task 12 places DATA_END from the tail only** (`SCAN_DAYS = 180` before the
  newest daily candle). Reading every stock's full 5-minute history cost about
  seven minutes per run; daily candles are small enough to read whole.
- **Task 12 warns when the pick's own candles stop before DATA_END.** Coverage
  is a floor, so up to 10% of stocks may end earlier; such a winner would be
  scored over a locked year it does not fully have.
