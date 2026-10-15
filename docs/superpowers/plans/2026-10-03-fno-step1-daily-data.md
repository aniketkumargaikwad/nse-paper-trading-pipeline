# F&O Step 1 — Daily Data Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local, resumable store of NSE's daily F&O data for five index underlyings since 2016 — every option and future, normalised from both NSE file formats — plus lot-size history, India VIX and FII/DII participant open interest, with data-quality checks.

**Architecture:** A new package `fno/` beside the stock engine. `nse_files` downloads and normalises NSE's daily F&O file (old format to 5 Jul 2024, UDiFF from 8 Jul 2024) into one schema; `store` keeps one Parquet file per underlying per year under `data/fno/` with a ledger of fetched days; `quality`, `participants`, `vix` and `contracts` (lot sizes) sit beside it. `scripts/fno_daily.py` fills the store and is safe to re-run. Nothing here needs the owner's accounts; publishing to R2 is step 3.

**Tech Stack:** Python 3.11, pandas, pyarrow (Parquet, zstd), requests, yfinance, PyYAML, pytest. All already in `requirements.txt`.

**Spec:** `docs/superpowers/specs/2026-10-02-fno-options-design.md` (§2, §12 step 1).

**Facts verified on 2–3 Oct 2026 that this plan relies on:**
- Old file URL `https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip`; header `INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,` (trailing comma). Futures have `OPTION_TYP` `XX` and `STRIKE_PR` `0`. Dates like `04-JAN-2016`, `28-Jan-2016`.
- UDiFF URL `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{YYYYMMDD}_F_0000.csv.zip`, first day 2024-07-08. Types `IDO` (index option), `IDF` (index future). `TtlTradgVol` is in contracts; `OpnIntrst` is in units; `NewBrdLotQty` is the lot; `UndrlygPric` the index.
- An untraded contract still carries a `CLOSE`/`ClsPric` (the previous close). Whether it traded comes from volume only.
- A day with no session returns HTTP 404 in both formats. Special Saturday sessions exist (20 Jan 2024 returned a file).
- Lot check: NIFTY near-month future on 2016-01-04: `VAL_INLAKH*1e5 / (CONTRACTS*CLOSE)` = 75.45, true lot 75.
- Participant OI `https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{DDMMYYYY}.csv`: line 1 a title, line 2 the header, rows `Client, DII, FII, Pro, TOTAL`; available back to 2016.
- `*.csv` is git-ignored repo-wide, so test fixtures are inline strings, not files.

**Two deviations from the spec, recorded in it by Task 1:** F&O quality checks go to a report beside the data (`data/fno/quality/<YEAR>.csv`), because `data_quality_flags` requires a stock/index `instrument_id` and allows only three flag types; and the charges and interest-rate tables move to step 2, where the code that reads them is built and tested.

---

## File structure

| File | Responsibility |
|---|---|
| `fno/__init__.py` | Package docstring pointing at the spec |
| `fno/contracts.py` | Which underlyings; lot-size estimation, change points, the `LotSizes` table |
| `fno/lot_sizes.yaml` | The reviewed lot-size history (generated in Task 11, committed) |
| `fno/nse_files.py` | URL per date, download, parse both formats into `COLUMNS` |
| `fno/store.py` | Local store paths, atomic per-day replace, read by range, the session ledger |
| `fno/quality.py` | Checks on one day's normalised rows; append issues to the report |
| `fno/participants.py` | Download/parse/store participant-wise OI |
| `fno/vix.py` | India VIX daily from Yahoo, normalised and stored |
| `scripts/fno_daily.py` | Fill the store for a date range; resumable |
| `scripts/fno_lot_sizes.py` | Derive lot sizes from the store, compare with NSE's own, write the YAML |
| `tests/test_fno_nse_files.py`, `tests/test_fno_store.py`, `tests/test_fno_quality.py`, `tests/test_fno_participants.py`, `tests/test_fno_vix.py`, `tests/test_fno_contracts.py`, `tests/test_fno_daily_script.py` | One test file per module |

Run every command from the repository root with the venv: on this Windows machine `.venv/Scripts/python.exe -m pytest ...`. The repository uses LF line endings: edit with the editor, or write files with `write_bytes`; never `Path.write_text` (it writes CRLF on Windows).

---

### Task 1: Package skeleton, ignore the data, record the spec deviations

**Files:**
- Create: `fno/__init__.py`, `fno/contracts.py`, `tests/test_fno_contracts.py`
- Modify: `.gitignore`, `docs/superpowers/specs/2026-10-02-fno-options-design.md`

- [ ] **Step 1: Write the failing test**

`tests/test_fno_contracts.py`:

```python
"""Which F&O contracts are researched, and their lot sizes."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno.contracts import ALWAYS_LISTED, INDEX_UNDERLYINGS, MINUTE_UNDERLYINGS  # noqa: E402


def test_minute_underlyings_are_a_subset_of_the_daily_ones():
    assert set(MINUTE_UNDERLYINGS) <= set(INDEX_UNDERLYINGS)
    assert MINUTE_UNDERLYINGS == ("NIFTY", "BANKNIFTY")


def test_only_contracts_listed_since_before_2016_are_expected_every_day():
    assert ALWAYS_LISTED == ("NIFTY", "BANKNIFTY")
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_contracts.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fno'`

- [ ] **Step 3: Create the package**

`fno/__init__.py`:

```python
"""F&O options research: data, pricing, the options engine and paper trading.

Design: docs/superpowers/specs/2026-10-02-fno-options-design.md
"""
```

`fno/contracts.py`:

```python
"""Which F&O contracts this platform researches, and their lot sizes.

Index derivatives only (design §0). Five underlyings are kept end-of-day for
positional research; two of them are also kept minute by minute (step 3).
"""

from __future__ import annotations

INDEX_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50")
MINUTE_UNDERLYINGS: tuple[str, ...] = ("NIFTY", "BANKNIFTY")

# Listed before 2016 and traded every session since, so a day's file without
# them is a fault rather than history. FINNIFTY (2021), MIDCPNIFTY (2022) and
# NIFTYNXT50 (2024) started later.
ALWAYS_LISTED: tuple[str, ...] = ("NIFTY", "BANKNIFTY")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_contracts.py -q`
Expected: `2 passed`

- [ ] **Step 5: Ignore the F&O store**

In `.gitignore`, directly under the existing line `data/candles/`, add:

```
# The F&O store (design 2026-10-02 §2.2): data, regenerable from NSE.
data/fno/
```

- [ ] **Step 6: Record the two deviations in the spec**

In `docs/superpowers/specs/2026-10-02-fno-options-design.md`, §2.6, replace the sentence
`Flags go to the existing \`data_quality_flags\` table.` with:

```
  Issues go to a report beside the data, `data/fno/quality/<YEAR>.csv`:
  `data_quality_flags` requires a stock or index `instrument_id` and allows
  only three flag types, so it cannot hold a contract.
```

In §12, step 1, replace `lot-size history, VIX, participant OI, charges and rates tables.` with
`lot-size history, VIX, participant OI.` and in step 2 replace `pricing, chain, features,` with
`charges and rates tables, pricing, chain, features,`.

- [ ] **Step 7: Commit**

```bash
git add fno/__init__.py fno/contracts.py tests/test_fno_contracts.py .gitignore docs/superpowers/specs/2026-10-02-fno-options-design.md
git commit -m "feat(fno): package skeleton and the underlyings it researches"
```

---

### Task 2: Which URL serves which day

**Files:**
- Create: `fno/nse_files.py`, `tests/test_fno_nse_files.py`

- [ ] **Step 1: Write the failing test**

`tests/test_fno_nse_files.py`:

```python
"""NSE's daily F&O files, both formats, normalised to one schema."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import nse_files  # noqa: E402


def test_days_before_the_switch_use_the_old_archive():
    assert nse_files.file_url(date(2016, 1, 4)) == (
        "https://nsearchives.nseindia.com/content/historical/DERIVATIVES/2016/JAN/fo04JAN2016bhav.csv.zip"
    )
    assert "/2024/JUL/fo05JUL2024bhav" in nse_files.file_url(date(2024, 7, 5))


def test_days_from_8_july_2024_use_udiff():
    assert nse_files.file_url(date(2024, 7, 8)) == (
        "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20240708_F_0000.csv.zip"
    )
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: FAIL with `ImportError: cannot import name 'nse_files'`

- [ ] **Step 3: Write the minimal implementation**

`fno/nse_files.py`:

```python
"""NSE's daily F&O files ("bhavcopy"), both formats, normalised to one schema.

Two formats, switched on 8 Jul 2024 (design §2.1). Both are read into
COLUMNS so nothing downstream knows which one a day came from.

Units, verified against the real files on 2 Oct 2026: volume is in
CONTRACTS, open interest in UNITS (shares of the index), turnover in rupees.
An untraded contract still carries a close - yesterday's - so whether a
contract traded is read from volume, never from its close.
"""

from __future__ import annotations

from datetime import date

UDIFF_FROM = date(2024, 7, 8)

_OLD_URL = ("https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
            "{year}/{mon}/fo{dd}{mon}{year}bhav.csv.zip")
_UDIFF_URL = ("https://nsearchives.nseindia.com/content/fo/"
              "BhavCopy_NSE_FO_0_0_0_{ymd}_F_0000.csv.zip")


def file_url(day: date) -> str:
    """Where NSE publishes `day`'s F&O file."""
    if day >= UDIFF_FROM:
        return _UDIFF_URL.format(ymd=day.strftime("%Y%m%d"))
    mon = day.strftime("%b").upper()
    return _OLD_URL.format(year=day.year, mon=mon, dd=day.strftime("%d"))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/nse_files.py tests/test_fno_nse_files.py
git commit -m "feat(fno): the NSE file URL for a day, in either format"
```

---

### Task 3: Parse the old format

**Files:**
- Modify: `fno/nse_files.py`, `tests/test_fno_nse_files.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fno_nse_files.py`:

```python
# Real rows from fo04JAN2016bhav.csv, trimmed. NIFTYIT and the stock option
# are there to be filtered out; the trailing comma is NSE's.
OLD = """INSTRUMENT,SYMBOL,EXPIRY_DT,STRIKE_PR,OPTION_TYP,OPEN,HIGH,LOW,CLOSE,SETTLE_PR,CONTRACTS,VAL_INLAKH,OPEN_INT,CHG_IN_OI,TIMESTAMP,
FUTIDX,NIFTY,28-Jan-2016,0,XX,7934.9,7947.75,7792.6,7809.8,7809.8,217442,1281367.25,20242200,804525,04-JAN-2016,
OPTIDX,NIFTY,28-Jan-2016,6350,CE,0,0,0,1533.9,1471.3,0,0,0,0,04-JAN-2016,
OPTIDX,NIFTY,28-Jan-2016,7800,PE,95.5,120,90.1,112.35,112.35,52000,302000.5,3100000,200000,04-JAN-2016,
FUTIDX,NIFTYIT,28-Jan-2016,0,XX,11300,11320,11200,11250,11250,10,28.1,500,0,04-JAN-2016,
OPTSTK,RELIANCE,28-Jan-2016,1000,CE,10,11,9,10.5,10.5,100,500,1000,0,04-JAN-2016,
"""


def test_old_format_keeps_index_futures_and_options_of_our_underlyings():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    assert list(frame.columns) == list(nse_files.COLUMNS)
    assert len(frame) == 3
    assert set(frame["underlying"]) == {"NIFTY"}


def test_old_format_future_row_is_normalised():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    fut = frame[frame["instrument"] == "FUT"].iloc[0]
    assert fut["trade_date"] == date(2016, 1, 4)
    assert fut["expiry"] == date(2016, 1, 28)
    assert fut["strike"] != fut["strike"]                 # NaN: a future has no strike
    assert fut["option_type"] != fut["option_type"]       # NaN, not "XX"
    assert fut["close"] == 7809.8
    assert fut["volume_contracts"] == 217442
    assert fut["oi_units"] == 20242200
    assert fut["turnover_inr"] == pytest.approx(1281367.25 * 100_000)
    assert fut["lot_size"] != fut["lot_size"]             # the old format has none


def test_old_format_untraded_option_keeps_its_published_close_and_zero_volume():
    frame = nse_files.parse(OLD, date(2016, 1, 4))
    idle = frame[(frame["instrument"] == "OPT") & (frame["strike"] == 6350)].iloc[0]
    assert idle["option_type"] == "CE"
    assert idle["volume_contracts"] == 0
    assert idle["close"] == 1533.9
    assert idle["settle"] == 1471.3


def test_a_file_for_another_day_is_refused():
    with pytest.raises(nse_files.NseFileError, match="2016-01-05"):
        nse_files.parse(OLD, date(2016, 1, 5))
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: FAIL with `AttributeError: module 'fno.nse_files' has no attribute 'parse'`

- [ ] **Step 3: Write the implementation**

In `fno/nse_files.py`, replace the import block with:

```python
from __future__ import annotations

import io
from datetime import date

import pandas as pd

from fno.contracts import INDEX_UNDERLYINGS
```

and append:

```python
COLUMNS: tuple[str, ...] = (
    "trade_date", "underlying", "instrument", "expiry", "strike", "option_type",
    "open", "high", "low", "close", "settle",
    "volume_contracts", "oi_units", "oi_change_units", "turnover_inr",
    "lot_size", "underlying_price",
)


class NseFileError(RuntimeError):
    """A file NSE served that cannot be trusted as the day's F&O data."""


def _num(col: pd.Series) -> pd.Series:
    """Text to numbers: blanks become NaN, anything else unparsable is an error."""
    col = col.str.strip()
    return pd.to_numeric(col.mask(col == ""), errors="raise")


def _dates(col: pd.Series, fmt: str) -> pd.Series:
    return pd.to_datetime(col.str.strip(), format=fmt).dt.date


def _parse_old(raw: pd.DataFrame) -> pd.DataFrame:
    kind = raw["INSTRUMENT"].str.strip()
    r = raw[kind.isin(("OPTIDX", "FUTIDX"))]
    is_opt = r["INSTRUMENT"].str.strip() == "OPTIDX"
    nan = float("nan")
    return pd.DataFrame({
        "trade_date": _dates(r["TIMESTAMP"], "%d-%b-%Y"),
        "underlying": r["SYMBOL"].str.strip(),
        "instrument": is_opt.map({True: "OPT", False: "FUT"}),
        "expiry": _dates(r["EXPIRY_DT"], "%d-%b-%Y"),
        "strike": _num(r["STRIKE_PR"]).where(is_opt),
        "option_type": r["OPTION_TYP"].str.strip().where(is_opt),
        "open": _num(r["OPEN"]),
        "high": _num(r["HIGH"]),
        "low": _num(r["LOW"]),
        "close": _num(r["CLOSE"]),
        "settle": _num(r["SETTLE_PR"]),
        "volume_contracts": _num(r["CONTRACTS"]).astype("int64"),
        "oi_units": _num(r["OPEN_INT"]).astype("int64"),
        "oi_change_units": _num(r["CHG_IN_OI"]).astype("int64"),
        "turnover_inr": _num(r["VAL_INLAKH"]) * 100_000,
        "lot_size": nan,
        "underlying_price": nan,
    })


def parse(text: str, day: date) -> pd.DataFrame:
    """`day`'s file as COLUMNS: index futures and options of INDEX_UNDERLYINGS only."""
    raw = pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    if "INSTRUMENT" in raw.columns:
        out = _parse_old(raw)
    else:
        raise NseFileError(f"{day}: unrecognised header {list(raw.columns)[:5]}")
    out = out[out["underlying"].isin(INDEX_UNDERLYINGS)].reset_index(drop=True)
    wrong = out[out["trade_date"] != day]
    if not wrong.empty:
        raise NseFileError(
            f"asked for {day}, the file is for {wrong['trade_date'].iloc[0]}: "
            "refusing to store one day's prices under another")
    return out[list(COLUMNS)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/nse_files.py tests/test_fno_nse_files.py
git commit -m "feat(fno): parse NSE's old-format daily F&O file"
```

---

### Task 4: Parse UDiFF

**Files:**
- Modify: `fno/nse_files.py`, `tests/test_fno_nse_files.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fno_nse_files.py`:

```python
UDIFF_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,"
                "FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,"
                "LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,"
                "TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")
# Real rows from BhavCopy_NSE_FO_0_0_0_20260930_F_0000.csv, plus a stock
# option (STO) that must be filtered out.
UDIFF = UDIFF_HEADER + """
2026-09-30,2026-09-30,FO,NSE,IDF,58875,,NIFTY,,2026-12-29,2026-12-29,,,NIFTY26DECFUT,23054.00,23185.00,22955.00,22973.40,22955.00,23054.00,22620.45,22973.40,89050,89050,1933,2898870338.00,1709,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,IDO,53339,,NIFTY,,2026-11-03,2026-11-03,20550.00,PE,NIFTY26N0320550PE,0.00,0.00,0.00,2.35,0.00,2.35,22620.45,6.67,0,0,0,0.00,0,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,IDO,67782,,NIFTY,,2028-12-26,2028-12-26,24000.00,CE,NIFTY28DEC24000CE,3064.95,3065.00,3064.95,3065.00,3065.00,3015.00,22620.45,3078.16,2665,-65,2,3518446.75,2,F1,65,,,,,
2026-09-30,2026-09-30,FO,NSE,STO,99999,,RELIANCE,,2026-10-27,2026-10-27,1400.00,CE,RELIANCE26OCT1400CE,10.00,11.00,9.00,10.50,10.50,10.00,1395.00,10.50,1000,0,5,100.00,5,F1,500,,,,,
"""


def test_udiff_keeps_index_futures_and_options_of_our_underlyings():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    assert list(frame.columns) == list(nse_files.COLUMNS)
    assert len(frame) == 3
    assert set(frame["instrument"]) == {"FUT", "OPT"}


def test_udiff_carries_lot_size_and_the_index_level():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    fut = frame[frame["instrument"] == "FUT"].iloc[0]
    assert fut["lot_size"] == 65
    assert fut["underlying_price"] == 22620.45
    assert fut["expiry"] == date(2026, 12, 29)
    assert fut["volume_contracts"] == 1933
    assert fut["oi_units"] == 89050
    assert fut["strike"] != fut["strike"]


def test_udiff_option_row_is_normalised():
    frame = nse_files.parse(UDIFF, date(2026, 9, 30))
    idle = frame[(frame["instrument"] == "OPT") & (frame["strike"] == 20550)].iloc[0]
    assert idle["option_type"] == "PE"
    assert idle["volume_contracts"] == 0
    assert idle["close"] == 2.35
    assert idle["settle"] == 6.67
    assert idle["expiry"] == date(2026, 11, 3)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: 3 FAIL with `NseFileError: 2026-09-30: unrecognised header`

- [ ] **Step 3: Write the implementation**

In `fno/nse_files.py`, add above `parse`:

```python
def _parse_udiff(raw: pd.DataFrame) -> pd.DataFrame:
    kind = raw["FinInstrmTp"].str.strip()
    r = raw[kind.isin(("IDO", "IDF"))]
    is_opt = r["FinInstrmTp"].str.strip() == "IDO"
    # The ACTUAL expiry, which moves when the scheduled day is a holiday.
    actual = r["FininstrmActlXpryDt"].str.strip()
    expiry = actual.mask(actual == "", r["XpryDt"].str.strip())
    return pd.DataFrame({
        "trade_date": _dates(r["TradDt"], "%Y-%m-%d"),
        "underlying": r["TckrSymb"].str.strip(),
        "instrument": is_opt.map({True: "OPT", False: "FUT"}),
        "expiry": _dates(expiry, "%Y-%m-%d"),
        "strike": _num(r["StrkPric"]).where(is_opt),
        "option_type": r["OptnTp"].str.strip().where(is_opt),
        "open": _num(r["OpnPric"]),
        "high": _num(r["HghPric"]),
        "low": _num(r["LwPric"]),
        "close": _num(r["ClsPric"]),
        "settle": _num(r["SttlmPric"]),
        "volume_contracts": _num(r["TtlTradgVol"]).astype("int64"),
        "oi_units": _num(r["OpnIntrst"]).astype("int64"),
        "oi_change_units": _num(r["ChngInOpnIntrst"]).astype("int64"),
        "turnover_inr": _num(r["TtlTrfVal"]),
        "lot_size": _num(r["NewBrdLotQty"]),
        "underlying_price": _num(r["UndrlygPric"]),
    })
```

and in `parse`, replace

```python
    if "INSTRUMENT" in raw.columns:
        out = _parse_old(raw)
    else:
```

with

```python
    if "INSTRUMENT" in raw.columns:
        out = _parse_old(raw)
    elif "TckrSymb" in raw.columns:
        out = _parse_udiff(raw)
    else:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/nse_files.py tests/test_fno_nse_files.py
git commit -m "feat(fno): parse NSE's UDiFF daily F&O file into the same schema"
```

---

### Task 5: Download a day's file

**Files:**
- Modify: `fno/nse_files.py`, `tests/test_fno_nse_files.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fno_nse_files.py`:

```python
import io  # noqa: E402
import zipfile  # noqa: E402


class FakeResponse:
    def __init__(self, status, content):
        self.status_code, self.content = status, content


class FakeSession:
    def __init__(self, response):
        self.response, self.urls = response, []

    def get(self, url, **kwargs):
        self.urls.append(url)
        return self.response


def zipped(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("fo04JAN2016bhav.csv", text)
    return buf.getvalue()


def test_download_returns_the_csv_text_from_the_zip():
    session = FakeSession(FakeResponse(200, zipped(OLD)))
    assert nse_files.download(date(2016, 1, 4), session) == OLD
    assert session.urls == [nse_files.file_url(date(2016, 1, 4))]


def test_no_file_means_no_session_that_day():
    assert nse_files.download(date(2024, 1, 26), FakeSession(FakeResponse(404, b""))) is None


def test_anything_else_is_an_error_not_a_holiday():
    """NSE answers a blocked client with 200 and an HTML page, not a zip."""
    with pytest.raises(nse_files.NseFileError, match="HTTP 200"):
        nse_files.download(date(2016, 1, 4), FakeSession(FakeResponse(200, b"<html>")))
    with pytest.raises(nse_files.NseFileError, match="HTTP 403"):
        nse_files.download(date(2016, 1, 4), FakeSession(FakeResponse(403, b"")))
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: 3 FAIL with `AttributeError: module 'fno.nse_files' has no attribute 'download'`

- [ ] **Step 3: Write the implementation**

In `fno/nse_files.py` add `import zipfile` and `from typing import Any` to the imports, and append:

```python
# Browser-like headers: NSE's archive host rejects default client user agents
# (the same finding as universes.py).
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}
TIMEOUT_SECONDS = 60


def download(day: date, session: Any) -> str | None:
    """`day`'s file as CSV text, or None when NSE has none (no session that day).

    Only a 404 means "no session". A blocked client is answered with 200 and
    an HTML page, and that must not be recorded as a holiday.
    """
    r = session.get(file_url(day), headers=HEADERS, timeout=TIMEOUT_SECONDS)
    if r.status_code == 404:
        return None
    if r.status_code != 200 or r.content[:2] != b"PK":
        raise NseFileError(f"{day}: HTTP {r.status_code}, {len(r.content)} bytes, not a zip")
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        return z.read(z.namelist()[0]).decode("utf-8")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_nse_files.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/nse_files.py tests/test_fno_nse_files.py
git commit -m "feat(fno): download a day's NSE F&O file; 404 means no session"
```

---

### Task 6: The local store and its ledger

**Files:**
- Create: `fno/store.py`, `tests/test_fno_store.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_store.py`:

```python
"""The local F&O store: one Parquet per underlying per year, and a ledger."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import nse_files, store  # noqa: E402
from test_fno_nse_files import OLD, UDIFF  # noqa: E402 - pytest puts tests/ on the path, as for research_helpers

DAY = date(2016, 1, 4)


def test_a_day_is_written_per_underlying_and_read_back(tmp_path):
    frame = nse_files.parse(OLD, DAY)
    store.write_day(tmp_path, frame, DAY)
    assert store.daily_path(tmp_path, "NIFTY", 2016).exists()
    back = store.read_daily(tmp_path, "NIFTY", DAY, DAY)
    assert len(back) == 3
    assert back["trade_date"].iloc[0] == DAY
    assert list(back.columns) == list(nse_files.COLUMNS)


def test_writing_the_same_day_twice_replaces_it(tmp_path):
    frame = nse_files.parse(OLD, DAY)
    store.write_day(tmp_path, frame, DAY)
    store.write_day(tmp_path, frame, DAY)
    assert len(store.read_daily(tmp_path, "NIFTY", DAY, DAY)) == 3


def test_reading_a_range_spans_years_and_trims_the_edges(tmp_path):
    store.write_day(tmp_path, nse_files.parse(OLD, DAY), DAY)
    sep30 = date(2026, 9, 30)
    store.write_day(tmp_path, nse_files.parse(UDIFF, sep30), sep30)
    both = store.read_daily(tmp_path, "NIFTY", date(2016, 1, 1), date(2026, 12, 31))
    assert set(both["trade_date"]) == {DAY, sep30}
    assert store.read_daily(tmp_path, "NIFTY", date(2017, 1, 1), date(2025, 1, 1)).empty


def test_nothing_stored_reads_as_an_empty_frame_with_the_columns(tmp_path):
    empty = store.read_daily(tmp_path, "BANKNIFTY", DAY, DAY)
    assert empty.empty and list(empty.columns) == list(nse_files.COLUMNS)


def test_the_ledger_remembers_each_day_and_its_outcome(tmp_path):
    assert store.load_ledger(tmp_path) == {}
    store.record_session(tmp_path, date(2024, 1, 26), "no_session", 0)
    store.record_session(tmp_path, DAY, "ok", 3)
    assert store.load_ledger(tmp_path) == {date(2024, 1, 26): "no_session", DAY: "ok"}


def test_no_temporary_file_is_left_behind(tmp_path):
    store.write_day(tmp_path, nse_files.parse(OLD, DAY), DAY)
    assert not list(tmp_path.rglob("*.tmp"))
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_store.py -q`
Expected: FAIL with `ImportError: cannot import name 'store'`

- [ ] **Step 3: Write the implementation**

`fno/store.py`:

```python
"""The local F&O store: one Parquet file per underlying per year, and a ledger.

Layout under FNO_ROOT (default data/fno), mirroring the R2 bucket of step 3
so publishing is a copy (design §2.2):

    daily/<UNDERLYING>/<YEAR>.parquet
    daily/_sessions.csv            every day fetched, and what NSE said

Each write replaces one day whole, through a temporary file and an atomic
rename, so a run stopped halfway leaves every file either old or new.
"""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

import pandas as pd

from fno.nse_files import COLUMNS

DEFAULT_ROOT = "data/fno"
LEDGER = Path("daily") / "_sessions.csv"
SORT = ["trade_date", "instrument", "expiry", "option_type", "strike"]


def root_dir() -> Path:
    return Path(os.environ.get("FNO_ROOT", DEFAULT_ROOT))


def daily_path(root: Path, underlying: str, year: int) -> Path:
    return Path(root) / "daily" / underlying / f"{year}.parquet"


def replace_day(path: Path, frame: pd.DataFrame, day: date, *, sort: list[str]) -> None:
    """Swap `day`'s rows in the file at `path` for `frame`, atomically."""
    old = pd.read_parquet(path) if path.exists() else None
    if old is not None:
        old = old[old["trade_date"] != day]
    merged = frame if old is None or old.empty else pd.concat([old, frame], ignore_index=True)
    merged = merged.sort_values(sort, na_position="first", kind="stable").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    merged.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def write_day(root: Path, frame: pd.DataFrame, day: date) -> None:
    """Store one day's normalised rows, one file per underlying."""
    if not frame.empty and (frame["trade_date"] != day).any():
        raise ValueError(f"rows for another day handed to write_day({day})")
    for underlying, part in frame.groupby("underlying"):
        replace_day(daily_path(root, underlying, day.year), part[list(COLUMNS)], day, sort=SORT)


def read_daily(root: Path, underlying: str, start: date, end: date) -> pd.DataFrame:
    """Every stored row for `underlying` with start <= trade_date <= end."""
    frames = [pd.read_parquet(p) for year in range(start.year, end.year + 1)
              if (p := daily_path(root, underlying, year)).exists()]
    if not frames:
        return pd.DataFrame(columns=list(COLUMNS))
    out = pd.concat(frames, ignore_index=True)
    keep = (out["trade_date"] >= start) & (out["trade_date"] <= end)
    return out[keep].reset_index(drop=True)[list(COLUMNS)]


def load_ledger(root: Path) -> dict[date, str]:
    path = Path(root) / LEDGER
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {date.fromisoformat(r["date"]): r["status"] for r in csv.DictReader(f)}


def record_session(root: Path, day: date, status: str, rows: int) -> None:
    path = Path(root) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(["date", "status", "rows"])
        w.writerow([day.isoformat(), status, rows])
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_store.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/store.py tests/test_fno_store.py
git commit -m "feat(fno): local store - atomic per-day replace, range reads, a ledger"
```

---

### Task 7: Quality checks on a day

**Files:**
- Create: `fno/quality.py`, `tests/test_fno_quality.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_quality.py`:

```python
"""Checks on one day's normalised F&O rows (design §2.6)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from fno import nse_files, quality  # noqa: E402
from test_fno_nse_files import OLD  # noqa: E402

DAY = date(2016, 1, 4)


def checks(issues):
    return {(i["check"], i["underlying"]) for i in issues}


def test_a_clean_day_has_only_the_missing_banknifty_of_this_trimmed_sample():
    issues = quality.check_day(nse_files.parse(OLD, DAY), DAY)
    assert checks(issues) == {("missing_underlying", "BANKNIFTY")}


def test_a_traded_row_whose_low_is_above_its_close_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    frame.loc[frame["instrument"] == "FUT", "low"] = 9000.0
    assert ("ohlc_invalid", "NIFTY") in checks(quality.check_day(frame, DAY))


def test_untraded_rows_are_not_held_to_ohlc_rules():
    """An untraded option's open/high/low are 0 by NSE's convention."""
    frame = nse_files.parse(OLD, DAY)
    assert ("ohlc_invalid", "NIFTY") not in checks(quality.check_day(frame, DAY))


def test_open_interest_without_a_settlement_price_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    frame.loc[frame["strike"] == 7800, "settle"] = 0.0
    assert ("settle_missing", "NIFTY") in checks(quality.check_day(frame, DAY))


def test_the_same_contract_twice_is_flagged():
    frame = nse_files.parse(OLD, DAY)
    doubled = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    assert ("duplicate_contract", "NIFTY") in checks(quality.check_day(doubled, DAY))


def test_issues_are_appended_to_a_yearly_report(tmp_path):
    issues = quality.check_day(nse_files.parse(OLD, DAY), DAY)
    quality.write_issues(tmp_path, issues)
    quality.write_issues(tmp_path, issues)
    text = (tmp_path / "quality" / "2016.csv").read_text(encoding="utf-8")
    assert text.count("missing_underlying") == 2
    assert text.splitlines()[0] == "trade_date,underlying,check,count,example"
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_quality.py -q`
Expected: FAIL with `ImportError: cannot import name 'quality'`

- [ ] **Step 3: Write the implementation**

`fno/quality.py`:

```python
"""Checks on one day's normalised F&O rows (design §2.6).

Issues are reported, never repaired: nothing is filled in or invented. They
go to data/fno/quality/<YEAR>.csv rather than data_quality_flags, which
needs a stock or index instrument_id (design §2.6 note).
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from fno.contracts import ALWAYS_LISTED

REPORT_COLUMNS = ("trade_date", "underlying", "check", "count", "example")
CONTRACT_KEY = ["underlying", "instrument", "expiry", "strike", "option_type"]


def _label(row: pd.Series) -> str:
    return f"{row['instrument']} {row['expiry']} {row['strike']} {row['option_type']}"


def check_day(frame: pd.DataFrame, day: date) -> list[dict[str, Any]]:
    """Everything suspicious about `day`'s rows, one entry per check and underlying."""
    issues: list[dict[str, Any]] = []

    def add(check: str, underlying: str, rows: pd.DataFrame | None) -> None:
        issues.append({
            "trade_date": day, "underlying": underlying, "check": check,
            "count": 1 if rows is None else len(rows),
            "example": "" if rows is None else _label(rows.iloc[0]),
        })

    for underlying in ALWAYS_LISTED:
        if not (frame["underlying"] == underlying).any():
            add("missing_underlying", underlying, None)

    traded = frame[frame["volume_contracts"] > 0]
    body_low = traded[["open", "close"]].min(axis=1)
    body_high = traded[["open", "close"]].max(axis=1)
    bad = traded[(traded["low"] > body_low) | (traded["high"] < body_high) | (traded["low"] <= 0)]
    for underlying, rows in bad.groupby("underlying"):
        add("ohlc_invalid", underlying, rows)

    unsettled = frame[(frame["oi_units"] > 0) & ~(frame["settle"] > 0)]
    for underlying, rows in unsettled.groupby("underlying"):
        add("settle_missing", underlying, rows)

    dup = frame[frame.duplicated(CONTRACT_KEY, keep=False)]
    for underlying, rows in dup.groupby("underlying"):
        add("duplicate_contract", underlying, rows)
    return issues


def write_issues(root: Path, issues: list[dict[str, Any]]) -> None:
    """Append `issues` to the report for their year."""
    for issue in issues:
        path = Path(root) / "quality" / f"{issue['trade_date'].year}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, lineterminator="\n")
            if new:
                w.writeheader()
            w.writerow({**issue, "trade_date": issue["trade_date"].isoformat()})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_quality.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/quality.py tests/test_fno_quality.py
git commit -m "feat(fno): data-quality checks on a day's F&O rows, reported not repaired"
```

---

### Task 8: Participant-wise open interest

**Files:**
- Create: `fno/participants.py`, `tests/test_fno_participants.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_participants.py`:

```python
"""FII / DII / Pro / Client open interest from NSE's daily file."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import participants  # noqa: E402

DAY = date(2026, 9, 30)
# The real file for 30 Sep 2026, verbatim (title row, trailing spaces in
# some header names, CRLF line ends).
FILE = (
    '""Participant wise Open Interest (no. of contracts) in Equity Derivatives as on Sep 30, 2026"",,,,,,,,,,,,,,\r\n'
    "Client Type,Future Index Long,Future Index Short,Future Stock Long,Future Stock Short       ,"
    "Option Index Call Long,Option Index Put Long,Option Index Call Short,Option Index Put Short,"
    "Option Stock Call Long,Option Stock Put Long,Option Stock Call Short,Option Stock Put Short,"
    "Total Long Contracts      ,Total Short Contracts\r\n"
    "Client,301040,58614,3453975,154613,3334467,1980918,2900690,2823611,1368687,543310,770156,834210,10982397,7541894\r\n"
    "DII,49642,17592,245443,4554998,8483,46958,2932,300,2112,43447,177732,20228,396085,4773782\r\n"
    "FII,29394,312264,3360170,2870621,466513,1032048,932096,363923,92210,198506,179833,71258,5178842,4729996\r\n"
    "Pro,35278,26884,826666,306022,1052809,789280,1026555,661370,594084,752690,929372,612257,4050807,3562460\r\n"
    "TOTAL,415354,415354,7886254,7886254,4862273,3849205,4862273,3849205,2057093,1537953,2057093,1537953,20608131,20608131\r\n"
)


def test_every_participant_is_a_row_with_snake_case_columns():
    frame = participants.parse(FILE, DAY)
    assert list(frame["client_type"]) == ["Client", "DII", "FII", "Pro", "TOTAL"]
    fii = frame[frame["client_type"] == "FII"].iloc[0]
    assert fii["future_index_long"] == 29394
    assert fii["future_index_short"] == 312264
    assert fii["total_short_contracts"] == 4729996
    assert fii["trade_date"] == DAY


def test_a_file_missing_a_column_is_refused():
    broken = FILE.replace("Future Index Short", "Something Else")
    with pytest.raises(participants.ParticipantFileError, match="future_index_short"):
        participants.parse(broken, DAY)


def test_a_day_is_stored_and_replaced(tmp_path):
    frame = participants.parse(FILE, DAY)
    participants.write_day(tmp_path, frame, DAY)
    participants.write_day(tmp_path, frame, DAY)
    back = participants.read(tmp_path, DAY, DAY)
    assert len(back) == 5
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_participants.py -q`
Expected: FAIL with `ImportError: cannot import name 'participants'`

- [ ] **Step 3: Write the implementation**

`fno/participants.py`:

```python
"""FII / DII / Pro / Client open interest in equity derivatives, daily (design §2.1).

NSE's file: a title line, then a header, then one row per participant type
plus TOTAL. Counts are in CONTRACTS. Some header names carry trailing spaces.
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from fno.nse_files import HEADERS, TIMEOUT_SECONDS
from fno.store import replace_day

URL = "https://nsearchives.nseindia.com/content/nsccl/fao_participant_oi_{ddmmyyyy}.csv"
CLIENT_TYPES = ("Client", "DII", "FII", "Pro", "TOTAL")
NUMBERS = (
    "future_index_long", "future_index_short", "future_stock_long", "future_stock_short",
    "option_index_call_long", "option_index_put_long", "option_index_call_short",
    "option_index_put_short", "option_stock_call_long", "option_stock_put_long",
    "option_stock_call_short", "option_stock_put_short",
    "total_long_contracts", "total_short_contracts",
)


class ParticipantFileError(RuntimeError):
    """A participant-OI file that cannot be read as one."""


def download(day: date, session: Any) -> str | None:
    """`day`'s file as text, or None when NSE has none."""
    r = session.get(URL.format(ddmmyyyy=day.strftime("%d%m%Y")),
                    headers=HEADERS, timeout=TIMEOUT_SECONDS)
    if r.status_code == 404:
        return None
    text = r.content.decode("utf-8", "replace")
    if r.status_code != 200 or "Client Type" not in text:
        raise ParticipantFileError(f"{day}: HTTP {r.status_code}, not a participant-OI file")
    return text


def _snake(name: str) -> str:
    return "_".join(name.strip().lower().split())


def parse(text: str, day: date) -> pd.DataFrame:
    raw = pd.read_csv(io.StringIO(text), skiprows=1, dtype=str, keep_default_na=False)
    raw.columns = [_snake(c) for c in raw.columns]
    missing = [c for c in ("client_type", *NUMBERS) if c not in raw.columns]
    if missing:
        raise ParticipantFileError(f"{day}: missing column(s) {', '.join(missing)}")
    raw["client_type"] = raw["client_type"].str.strip()
    rows = raw[raw["client_type"].isin(CLIENT_TYPES)].reset_index(drop=True)
    out = pd.DataFrame({"trade_date": [day] * len(rows), "client_type": rows["client_type"]})
    for col in NUMBERS:
        out[col] = pd.to_numeric(rows[col].str.strip(), errors="raise").astype("int64")
    return out


def path_for(root: Path, year: int) -> Path:
    return Path(root) / "participants" / f"{year}.parquet"


def write_day(root: Path, frame: pd.DataFrame, day: date) -> None:
    replace_day(path_for(root, day.year), frame, day, sort=["trade_date", "client_type"])


def read(root: Path, start: date, end: date) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for year in range(start.year, end.year + 1)
              if (p := path_for(root, year)).exists()]
    if not frames:
        return pd.DataFrame(columns=["trade_date", "client_type", *NUMBERS])
    out = pd.concat(frames, ignore_index=True)
    return out[(out["trade_date"] >= start) & (out["trade_date"] <= end)].reset_index(drop=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_participants.py -q`
Expected: `3 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/participants.py tests/test_fno_participants.py
git commit -m "feat(fno): FII/DII/Pro/Client open interest from NSE, daily"
```

---

### Task 9: India VIX daily

**Files:**
- Create: `fno/vix.py`, `tests/test_fno_vix.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_vix.py`:

```python
"""India VIX daily, from Yahoo (design §2.1)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import vix  # noqa: E402


def yahoo_like() -> pd.DataFrame:
    """yfinance's shape: a (field, ticker) column MultiIndex, a datetime index."""
    idx = pd.DatetimeIndex(["2026-09-29", "2026-09-30", "2026-10-01"], name="Date")
    cols = pd.MultiIndex.from_tuples(
        [(f, "^INDIAVIX") for f in ("Open", "High", "Low", "Close", "Adj Close", "Volume")],
        names=["Price", "Ticker"])
    data = [[13.0, 13.5, 12.8, 13.2, 13.2, 0],
            [13.2, 14.1, 13.0, 14.0, 14.0, 0],
            [float("nan")] * 5 + [0]]
    return pd.DataFrame(data, index=idx, columns=cols)


def test_normalise_gives_dated_ohlc_and_drops_empty_days():
    frame = vix.normalise(yahoo_like())
    assert list(frame.columns) == ["trade_date", "open", "high", "low", "close"]
    assert list(frame["trade_date"]) == [date(2026, 9, 29), date(2026, 9, 30)]
    assert frame["close"].iloc[-1] == 14.0


def test_stored_and_read_back(tmp_path):
    vix.write(tmp_path, vix.normalise(yahoo_like()))
    back = vix.read(tmp_path)
    assert len(back) == 2 and back["trade_date"].iloc[0] == date(2026, 9, 29)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_vix.py -q`
Expected: FAIL with `ImportError: cannot import name 'vix'`

- [ ] **Step 3: Write the implementation**

`fno/vix.py`:

```python
"""India VIX, daily, from Yahoo's ^INDIAVIX (from 2008; design §2.1).

The whole history is small (~4,500 rows), so each refresh rewrites the file.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

SYMBOL = "^INDIAVIX"
COLUMNS = ["trade_date", "open", "high", "low", "close"]


def normalise(raw: pd.DataFrame) -> pd.DataFrame:
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    frame = frame.rename(columns=str.lower)[["open", "high", "low", "close"]]
    frame = frame.dropna(subset=["close"])
    frame.insert(0, "trade_date", pd.DatetimeIndex(frame.index).date)
    return frame.reset_index(drop=True)[COLUMNS]


def fetch_daily() -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(SYMBOL, period="max", interval="1d", auto_adjust=False, progress=False)
    return normalise(raw)


def vix_path(root: Path) -> Path:
    return Path(root) / "vix" / "daily.parquet"


def write(root: Path, frame: pd.DataFrame) -> None:
    path = vix_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.to_parquet(tmp, index=False, compression="zstd")
    os.replace(tmp, path)


def read(root: Path) -> pd.DataFrame:
    path = vix_path(root)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=COLUMNS)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_vix.py -q`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```bash
git add fno/vix.py tests/test_fno_vix.py
git commit -m "feat(fno): India VIX daily from Yahoo, normalised and stored"
```

---

### Task 10: The fill script

**Files:**
- Create: `scripts/fno_daily.py`, `tests/test_fno_daily_script.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fno_daily_script.py`:

```python
"""Which days the fill script asks NSE for, and when 'no file' is final."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fno_daily  # noqa: E402


def test_days_already_in_the_ledger_are_not_asked_for_again():
    ledger = {date(2026, 9, 29): "ok", date(2026, 9, 30): "no_session"}
    days = fno_daily.plan_days(date(2026, 9, 28), date(2026, 10, 1), ledger)
    assert days == [date(2026, 9, 28), date(2026, 10, 1)]


def test_every_calendar_day_is_tried_because_saturdays_can_be_sessions():
    days = fno_daily.plan_days(date(2024, 1, 19), date(2024, 1, 21), {})
    assert days == [date(2024, 1, 19), date(2024, 1, 20), date(2024, 1, 21)]


def test_a_missing_recent_file_is_not_yet_proof_of_no_session():
    """NSE publishes in the evening, sometimes late: only after three days
    does a 404 become 'no session' in the ledger."""
    today = date(2026, 10, 3)
    assert not fno_daily.settled(date(2026, 10, 1), today)
    assert fno_daily.settled(date(2026, 9, 29), today)
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_daily_script.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'fno_daily'`

- [ ] **Step 3: Write the script**

`scripts/fno_daily.py`:

```python
"""Fill the local F&O store from NSE: daily files, participant OI, India VIX.

    python scripts/fno_daily.py                      # 2016-01-01 -> today, missing days only
    python scripts/fno_daily.py --from 2026-09-01    # a narrower range
    python scripts/fno_daily.py --skip-vix

Resumable: data/fno/daily/_sessions.csv records every day already fetched or
found to have no session, and a re-run asks NSE only for the rest. Stopped
by an error, it says which day; run it again to carry on.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests  # noqa: E402

from config import IST, use_utf8_stdout  # noqa: E402
from fno import nse_files, participants, quality, store, vix  # noqa: E402

START = date(2016, 1, 1)
LATE_DAYS = 3            # NSE publishes in the evening, sometimes days late
PAUSE_SECONDS = 0.4      # between days: be a polite client
RETRIES = 3


def plan_days(start: date, end: date, ledger: dict[date, str]) -> list[date]:
    """Every calendar day in [start, end] the ledger does not already settle."""
    out, day = [], start
    while day <= end:
        if day not in ledger:
            out.append(day)
        day += timedelta(days=1)
    return out


def settled(day: date, today: date) -> bool:
    """Whether a missing file for `day` is final rather than not-yet-published."""
    return (today - day).days > LATE_DAYS


def _retrying(fetch, day: date):
    for attempt in range(1, RETRIES + 1):
        try:
            return fetch()
        except (nse_files.NseFileError, participants.ParticipantFileError,
                requests.RequestException) as exc:
            if attempt == RETRIES:
                raise
            print(f"  {day}: {exc} - retrying in {10 * attempt}s", flush=True)
            time.sleep(10 * attempt)
    return None


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=START)
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=None)
    parser.add_argument("--skip-participants", action="store_true")
    parser.add_argument("--skip-vix", action="store_true")
    args = parser.parse_args(argv)

    root = store.root_dir()
    today = datetime.now(IST).date()
    end = args.end or today
    days = plan_days(args.start, end, store.load_ledger(root))
    print(f"F&O store {root}: {len(days)} day(s) to ask NSE for, {args.start} -> {end}", flush=True)

    session = requests.Session()
    sessions = rows = 0
    for i, day in enumerate(days, 1):
        try:
            text = _retrying(lambda: nse_files.download(day, session), day)
            if text is None:
                if settled(day, today):
                    store.record_session(root, day, "no_session", 0)
                continue
            frame = nse_files.parse(text, day)
            store.write_day(root, frame, day)
            quality.write_issues(root, quality.check_day(frame, day))
            if not args.skip_participants:
                ptext = _retrying(lambda: participants.download(day, session), day)
                if ptext is not None:
                    participants.write_day(root, participants.parse(ptext, day), day)
        except Exception as exc:                       # noqa: BLE001 - report and stop
            print(f"STOPPED at {day}: {exc}\nRun again to resume from there.", file=sys.stderr)
            return 1
        store.record_session(root, day, "ok", len(frame))
        sessions += 1
        rows += len(frame)
        if i % 25 == 0:
            print(f"  {day}: {sessions} session(s), {rows:,} rows so far", flush=True)
        time.sleep(PAUSE_SECONDS)

    if not args.skip_vix:
        frame = vix.fetch_daily()
        vix.write(root, frame)
        print(f"India VIX: {len(frame)} days, to {frame['trade_date'].iloc[-1]}")
    print(f"done: {sessions} session(s) stored, {rows:,} rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_daily_script.py -q`
Expected: `3 passed`

- [ ] **Step 5: Try it on one real week**

Run: `FNO_ROOT=data/fno .venv/Scripts/python.exe scripts/fno_daily.py --from 2024-01-15 --to 2024-01-21 --skip-vix`
Expected: about 5–6 sessions stored (Mon–Fri plus Saturday 20 Jan), no `STOPPED` line. Then:
`.venv/Scripts/python.exe -c "from datetime import date; from fno import store; print(store.read_daily(store.root_dir(), 'NIFTY', date(2024,1,15), date(2024,1,21)).groupby('trade_date').size())"`
Expected: one row count per session day, each in the thousands.

- [ ] **Step 6: Commit**

```bash
git add scripts/fno_daily.py tests/test_fno_daily_script.py
git commit -m "feat(fno): resumable fill script for NSE F&O files, participant OI and VIX"
```

---

### Task 11: Lot sizes

**Files:**
- Modify: `fno/contracts.py`, `tests/test_fno_contracts.py`
- Create: `scripts/fno_lot_sizes.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_fno_contracts.py`:

```python
from datetime import date  # noqa: E402

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from fno.contracts import LotSizeError, LotSizes, change_points, estimate_lot_sizes  # noqa: E402


def futures_day(day, lot, *, published=float("nan"), close=10_000.0, contracts=1_000, skew=1.01):
    """One busy future and one quiet one for NIFTY on `day`."""
    turnover = contracts * lot * close * skew          # average price a little off the close
    return pd.DataFrame({
        "trade_date": [day, day], "underlying": ["NIFTY", "NIFTY"], "instrument": ["FUT", "FUT"],
        "expiry": [date(2099, 1, 1), date(2099, 2, 1)], "close": [close, close],
        "volume_contracts": [contracts, 3], "turnover_inr": [turnover, 3 * 999 * close],
        "lot_size": [published, published],
    })


def test_the_lot_is_read_from_the_busiest_future_and_rounded():
    est = estimate_lot_sizes(futures_day(date(2016, 1, 4), 75))
    row = est.iloc[0]
    assert row["lot_size"] == 75 and row["source"] == "derived"


def test_a_published_lot_wins_over_the_estimate():
    est = estimate_lot_sizes(futures_day(date(2026, 9, 30), 65, published=65.0, skew=1.04))
    assert est.iloc[0]["lot_size"] == 65 and est.iloc[0]["source"] == "nse"


def test_a_change_counts_only_after_three_agreeing_sessions():
    days = [date(2021, 6, d) for d in (1, 2, 3, 4, 7, 8, 9, 10)]
    lots = [75, 75, 75, 50, 75, 50, 50, 50]           # one stray 50, then a real change
    est = pd.DataFrame({"underlying": "NIFTY", "trade_date": days, "lot_size": lots})
    assert change_points(est) == {"NIFTY": [(date(2021, 6, 1), 75), (date(2021, 6, 8), 50)]}


def test_the_table_answers_for_a_date_and_refuses_before_its_start():
    table = LotSizes({"NIFTY": ((date(2016, 1, 1), 75), (date(2021, 6, 7), 50))})
    assert table.on("NIFTY", date(2021, 6, 4)) == 75
    assert table.on("NIFTY", date(2021, 6, 7)) == 50
    with pytest.raises(LotSizeError):
        table.on("NIFTY", date(2015, 12, 31))
    with pytest.raises(LotSizeError):
        table.on("SENSEX", date(2021, 6, 7))


def test_the_table_survives_a_round_trip_through_yaml():
    table = LotSizes({"NIFTY": ((date(2016, 1, 1), 75), (date(2021, 6, 7), 50))})
    assert LotSizes.from_yaml(table.to_yaml()) == table
```

- [ ] **Step 2: Run them to make sure they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_contracts.py -q`
Expected: FAIL with `ImportError: cannot import name 'LotSizeError'`

- [ ] **Step 3: Write the implementation**

Replace `fno/contracts.py` with:

```python
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
```

Note on `from_yaml`: PyYAML loads an unquoted `2016-01-01` as a `datetime.date` already; `date.fromisoformat(str(...))` accepts either form.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_fno_contracts.py -q`
Expected: `7 passed`

- [ ] **Step 5: Write the generator script**

`scripts/fno_lot_sizes.py`:

```python
"""Derive lot sizes from the local F&O store and write fno/lot_sizes.yaml.

    python scripts/fno_lot_sizes.py          # print the changes and the check, write the file

The check: on every day since 8 Jul 2024 NSE publishes the lot itself, and
the derivation must agree with it. A disagreement means the method is wrong
for that underlying, and the file is not written.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from config import use_utf8_stdout  # noqa: E402
from fno import store  # noqa: E402
from fno.contracts import (  # noqa: E402
    INDEX_UNDERLYINGS, LOT_SIZES_FILE, LotSizes, change_points, estimate_lot_sizes,
)


def main() -> int:
    use_utf8_stdout()
    root = store.root_dir()
    frames = [store.read_daily(root, u, date(2016, 1, 1), date.today()) for u in INDEX_UNDERLYINGS]
    est = estimate_lot_sizes(pd.concat([f for f in frames if not f.empty], ignore_index=True))

    nse_days = est[est["source"] == "nse"]
    wrong = nse_days[nse_days["derived"] != nse_days["lot_size"]]
    print(f"derivation vs NSE's own lot, {len(nse_days)} day(s): {len(wrong)} disagreement(s)")
    if len(wrong):
        print(wrong.head(20).to_string(index=False))

    table = LotSizes({u: tuple(seq) for u, seq in change_points(est).items()})
    for underlying, seq in sorted(table.changes.items()):
        print(f"\n{underlying}")
        for start, lot in seq:
            print(f"  from {start}: {lot}")
    if len(wrong) > len(nse_days) * 0.01:
        print("\nNOT written: the derivation disagrees with NSE on over 1% of days.", file=sys.stderr)
        return 1
    LOT_SIZES_FILE.write_bytes(
        b"# Lot sizes by date (design 2026-10-02 sec 2.3). Generated by scripts/fno_lot_sizes.py,\n"
        b"# then each change checked against the NSE circular that announced it.\n"
        + table.to_yaml().encode("utf-8"))
    print(f"\nwritten: {LOT_SIZES_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 6: Commit**

```bash
git add fno/contracts.py tests/test_fno_contracts.py scripts/fno_lot_sizes.py
git commit -m "feat(fno): lot sizes - derive, confirm change points, a dated table"
```

---

### Task 12: The real backfill, and the lot-size table

**Files:**
- Create: `fno/lot_sizes.yaml` (generated, then reviewed)

- [ ] **Step 1: Run the full backfill in the background**

Run: `.venv/Scripts/python.exe -u scripts/fno_daily.py` (in the background; roughly 3,900 calendar days at ~0.4 s pause plus download time: one to two hours).
Expected: progress lines every 25 days, a final `done: N session(s) stored`, then `India VIX: ~4,550 days`. If it prints `STOPPED at <day>`, run the same command again; it resumes.

- [ ] **Step 2: Check the store's shape**

Run:

```bash
.venv/Scripts/python.exe -c "
from datetime import date
from fno import store
from fno.contracts import INDEX_UNDERLYINGS
root = store.root_dir()
led = store.load_ledger(root)
print('sessions ok:', sum(1 for s in led.values() if s=='ok'), '| no_session:', sum(1 for s in led.values() if s=='no_session'))
for u in INDEX_UNDERLYINGS:
    f = store.read_daily(root, u, date(2016,1,1), date.today())
    print(u, len(f), 'rows', f['trade_date'].min() if len(f) else '-', '->', f['trade_date'].max() if len(f) else '-')
"
```

Expected: roughly 2,400+ `ok` sessions; NIFTY and BANKNIFTY from 2016-01-01 to the last published day; FINNIFTY from 2021, MIDCPNIFTY from 2022, NIFTYNXT50 from 2024.

- [ ] **Step 3: Read the quality report**

Run: `cat data/fno/quality/*.csv | cut -d, -f3 | sort | uniq -c`
Expected: no `missing_underlying` rows (NIFTY and BANKNIFTY present every session); any `ohlc_invalid` or `settle_missing` counts small. Look at a few examples of each and record what they are in the DATA.md section of Task 13.

- [ ] **Step 4: Generate the lot sizes**

Run: `.venv/Scripts/python.exe scripts/fno_lot_sizes.py`
Expected: `0 disagreement(s)` (or under 1%), a list of change dates per underlying, and `written: .../fno/lot_sizes.yaml`.

- [ ] **Step 5: Check every NIFTY and BANKNIFTY change against a public source**

For each change date the script printed for NIFTY and BANKNIFTY, find the NSE circular or a reliable report of the revision (search "NSE revision in lot size NIFTY <month year>") and confirm the date and size. Where the derived date is a few days off the circular's effective date (the first sessions after a revision are thin), correct the date by hand in `fno/lot_sizes.yaml` and note the source in a comment beside it. Do the same for the other three underlyings where a source is easy to find; mark any unconfirmed change with a `# unconfirmed` comment.

- [ ] **Step 6: Prove the committed table loads**

Run: `.venv/Scripts/python.exe -c "from datetime import date; from fno.contracts import LotSizes; t=LotSizes.load(); print(t.on('NIFTY', date(2016,1,4)), t.on('NIFTY', date(2026,9,30)))"`
Expected: `75 65`

- [ ] **Step 7: Commit**

```bash
git add fno/lot_sizes.yaml
git commit -m "data(fno): lot-size history since 2016, derived and checked against circulars"
```

---

### Task 13: Document it

**Files:**
- Modify: `docs/DATA.md`

- [ ] **Step 1: Add a section after section 8**

Append to `docs/DATA.md`:

```markdown
---

## 9. F&O data

Design: `docs/superpowers/specs/2026-10-02-fno-options-design.md`.

**Where:** `data/fno/` locally (`FNO_ROOT` to move it), git-ignored. Step 3
publishes the same layout to a private Cloudflare R2 bucket.

| Path | What |
|---|---|
| `daily/<UNDERLYING>/<YEAR>.parquet` | every index option and future, end of day |
| `daily/_sessions.csv` | the ledger: each day fetched, `ok` or `no_session` |
| `participants/<YEAR>.parquet` | FII / DII / Pro / Client open interest |
| `vix/daily.parquet` | India VIX from 2008 |
| `quality/<YEAR>.csv` | issues found, never repaired |

**Underlyings:** NIFTY, BANKNIFTY (since 2016); FINNIFTY (2021),
MIDCPNIFTY (2022), NIFTYNXT50 (2024).

**Fill or top up:** `python scripts/fno_daily.py` — resumable; re-run after
any stop. A day NSE has no file for is recorded as `no_session` only once it
is more than three days old, because NSE sometimes publishes late.

**Units:** volume in contracts, open interest in units. An untraded contract
still shows a close (the previous one): whether it traded comes from volume.

**Lot sizes:** `fno/lot_sizes.yaml`, derived from futures turnover before
8 Jul 2024 and checked against NSE circulars; NSE's own column after.

**What the quality report found on the first fill:** (fill in from Task 12
step 3 — the counts per check and what the examples turned out to be.)
```

Replace the last paragraph's parenthesis with the actual findings from Task 12, Step 3 before committing; this plan cannot know them in advance.

- [ ] **Step 2: Run the whole test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q`
Expected: all pass (1,617 before this plan plus the new F&O tests).

- [ ] **Step 3: Check line endings**

Run: `git diff --stat main...HEAD | tail -1; git diff --ignore-cr-at-eol --stat main...HEAD | tail -1`
Expected: the same two totals.

- [ ] **Step 4: Commit**

```bash
git add docs/DATA.md
git commit -m "docs(fno): the F&O data - where it lives, how to fill it, what it found"
```
