# F&O Step 3a — Minute Data: Upstox, Dhan, Cloudflare R2, the daily job

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Everything step 3 needs except the owner's keys: download expired NIFTY / BANKNIFTY option minutes from Upstox, index and India VIX minutes from Dhan, keep the whole F&O store in a private Cloudflare R2 bucket, and two GitHub jobs (daily top-up, manual backfill) — plus a setup check that says which key works without printing any.

**Architecture:** `fno/upstox.py` is a read-only client for Upstox's three expired-instrument endpoints. `fno/minute.py` chooses which contracts to keep (±5% band, final 10 or 20 sessions), stores one Parquet file per expiry with a ledger, and checks minute closes against NSE's daily closes. `fno/index_minutes.py` fetches index minutes through the existing Dhan provider (one new public method). `fno/r2.py` publishes and fetches the store by size and MD5. Scripts tie them together; workflows run them.

**Tech Stack:** Python 3.11, requests, pandas, pyarrow, boto3 (new, for R2's S3 interface), pytest with fake HTTP / fake S3 objects — no test touches the network.

**Spec:** `docs/superpowers/specs/2026-10-02-fno-options-design.md` §2.1, §2.2, §2.6, §10, §11. Owner's steps: `docs/FNO_SETUP.md`.

**Step 3b (intraday engine and intraday atlas) is deliberately NOT in this plan.** Upstox's own page says expired data covers "up to six months of historical expiries"; users report anything from six months to five years. If it is six months, every option minute falls inside the sealed exam year and intraday research needs a different design (minute-priced options from index minutes and daily IV for the training years). The first real fill measures it; 3b is planned after.

**Conventions:** run with `.venv/Scripts/python.exe -m pytest ...`; LF line endings; tests import shared fixtures by module name.

**Decisions made while planning:**
- **Expired-only options.** Minutes are fetched for an expiry once it has expired, through the expired-instrument API — the same path for the backfill and the daily top-up. Live contracts' minutes are only needed for paper trading (step 5).
- **The daily job is what builds history.** If Upstox serves six months, an expiry not saved within six months is lost. So the job fetches every served expiry not yet in the ledger, every weekday.
- **Index and VIX minutes come from Dhan** (NIFTY 13, BANKNIFTY 25, INDIA VIX 21, segment `IDX_I` — Dhan's scrip master, checked 9 Oct 2026), from 3 Apr 2017. Not Upstox: Dhan's archive is longer and already paid for.
- **Band and window from data already stored:** strikes within ±5% of the index's daily close (Yahoo, `fno/spot.py`) on any of the expiry's final 10 sessions (weekly) or 20 (monthly), sessions from the NSE ledger. Weekly/monthly from Upstox's own `weekly` flag.
- **R2 key layout** is `fno/<path under data/fno>`, so the bucket mirrors the local store exactly. Files are compared by size and MD5 (an R2 ETag is the MD5 of a single-part upload; every upload here is single-part below 512 MB). Push never deletes.
- **The daily job pulls only what it reads** (this year's daily files, ledgers, VIX, spot, this year's index minutes; last year's too in January), not the 3 GB store.
- **Errors name the fix, never a secret.** Upstox's `UDAPI1149` means "needs Upstox Plus"; a 401 means the token was refused — and the message points at the analytics-token question in `docs/FNO_SETUP.md`.

---

## File structure

| File | Responsibility |
|---|---|
| `providers/dhan.py` | + `fetch_minutes()`; the paging loop shared with `fetch()` |
| `fno/upstox.py` | Upstox client: expiries, contracts, minute candles; errors that name the fix |
| `fno/minute.py` | Window, band, per-expiry store and ledger, fetch one expiry, quality checks |
| `fno/index_minutes.py` | Index + VIX minutes from Dhan, stored by year, resumable |
| `fno/r2.py` | R2 config from env, push, pull, the daily job's prefixes |
| `scripts/fno_minute.py` | Fill or top up the minute store |
| `scripts/fno_r2.py` | `push` / `pull` |
| `scripts/fno_check_setup.py` | Try each key; print works / fails, never a value |
| `.github/workflows/fno-daily.yml`, `fno-backfill.yml` | The weekday job and the manual backfill |
| `requirements.txt` | + `boto3` |
| `tests/test_fno_upstox.py`, `test_fno_minute.py`, `test_fno_index_minutes.py`, `test_fno_r2.py` | One per module |

---

### Task 1: Dhan one-minute candles

**Files:** Modify `providers/dhan.py` (the body of `fetch`); test in `tests/test_fno_index_minutes.py` (Task 4 adds the rest of that file).

- [ ] **Step 1: Failing test** — append to a new `tests/test_fno_index_minutes.py`:

`tests/test_fno_index_minutes.py`:

```python
"""NIFTY, BANKNIFTY and India VIX minutes from Dhan (design §2.1)."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import index_minutes  # noqa: E402
from providers.dhan import DhanProvider  # noqa: E402
from test_dhan_provider import FakeHttp, FakeResponse  # noqa: E402

UTC = timezone.utc


def minute_payload(start_epoch: int, n: int) -> dict:
    ts = [start_epoch + 60 * i for i in range(n)]
    return {"open": [100.0 + i for i in range(n)], "high": [101.0 + i for i in range(n)],
            "low": [99.0 + i for i in range(n)], "close": [100.5 + i for i in range(n)],
            "volume": [0] * n, "timestamp": ts}


def provider(http) -> DhanProvider:
    class Tokens:
        def get_access_token(self):
            return "tok"

        def invalidate(self):
            pass

    return DhanProvider(token_manager=Tokens(), instrument_resolver=index_minutes.IndexResolver(),
                        client_id="CID", http=http, sleep_fn=lambda s: None)


def test_one_minute_candles_ask_dhan_for_interval_1_on_the_index_segment():
    # 2026-10-01 09:15 IST = 03:45 UTC
    http = FakeHttp([FakeResponse(minute_payload(1790826300, 3))])
    frame = provider(http).fetch_minutes("NIFTY", datetime(2026, 10, 1, tzinfo=UTC),
                                         datetime(2026, 10, 2, tzinfo=UTC))
    body = http.calls[0]["json"]
    assert (body["securityId"], body["exchangeSegment"], body["instrument"], body["interval"]) == (
        "13", "IDX_I", "INDEX", "1")
    assert len(frame) == 3
```

- [ ] **Step 2: Run** — fails: `fno.index_minutes` missing (create an empty `fno/index_minutes.py` with just the resolver from Task 4's code to get past the import, then it fails on `fetch_minutes`).

- [ ] **Step 3: Refactor `fetch` and add `fetch_minutes`** — in `providers/dhan.py`, replace everything in `fetch` from `security_id, segment, instrument_type = ...` to the end of the method with:

```python
        interval = None if timeframe == "day" else TIMEFRAME_TO_DHAN_INTERVAL[timeframe]
        return self._fetch_paged(symbol, timeframe, interval, from_utc, to_utc)

    def fetch_minutes(self, symbol: str, from_utc: datetime, to_utc: datetime) -> pd.DataFrame:
        """1-minute candles for [from_utc, to_utc], paged at 90 days.

        For the F&O engine's index series only (fno.index_minutes). The stock
        store never asks for these: it stores 5-minute and derives the rest.
        """
        return self._fetch_paged(symbol, "1m", TIMEFRAME_TO_DHAN_INTERVAL["1m"], from_utc, to_utc)

    def _fetch_paged(self, symbol: str, label: str, interval: str | None,
                     from_utc: datetime, to_utc: datetime) -> pd.DataFrame:
        """Daily candles when `interval` is None, intraday otherwise, paged and joined."""
        security_id, segment, instrument_type = self._instruments.resolve(symbol)
        daily = interval is None
        endpoint = HISTORICAL_ENDPOINT if daily else INTRADAY_ENDPOINT
        page_days = MAX_DAYS_PER_DAILY_REQUEST if daily else MAX_DAYS_PER_REQUEST

        frames: list[pd.DataFrame] = []
        for i, (window_from, window_to) in enumerate(date_windows(from_utc, to_utc, page_days)):
            if i > 0:
                self._sleep(_SECONDS_BETWEEN_PAGES)
            body: dict[str, Any] = {
                "securityId": security_id,
                "exchangeSegment": segment,
                "instrument": instrument_type,
                # Open interest is an F&O concept; we trade cash equity and
                # never use it, but the API expects the field.
                "oi": False,
            }
            if daily:
                body["expiryCode"] = 0
                body["fromDate"] = window_from.astimezone(IST).strftime("%Y-%m-%d")
                body["toDate"] = window_to.astimezone(IST).strftime("%Y-%m-%d")
            else:
                body["interval"] = interval
                # Intraday windows are datetime-precise, unlike daily.
                body["fromDate"] = window_from.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")
                body["toDate"] = window_to.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S")

            payload = self._post(endpoint, body, f"fetching {label} candles for {symbol}")
            frames.append(parse_candle_payload(payload))

        frames = [f for f in frames if not f.empty]
        if not frames:
            # Every window was a holiday/pre-listing span. Concatenating empty
            # frames is deprecated in pandas and would eventually make the
            # OHLCV columns object-dtype.
            return empty_frame()
        combined = pd.concat(frames)
        # Not trimmed to [from_utc, to_utc]: Dhan returns whole days, and
        # callers own reconciling against the exact requested range.
        return combined[~combined.index.duplicated(keep="first")].sort_index()
```

- [ ] **Step 4: Run** `tests/test_dhan_provider.py tests/test_fno_index_minutes.py` — all pass (the existing Dhan tests prove the refactor changed nothing).

- [ ] **Step 5: Commit** — `feat(dhan): one-minute candles, for the F&O index series`

---

### Task 2: The Upstox client

**Files:** Create `fno/upstox.py`, `tests/test_fno_upstox.py`

- [ ] **Step 1: Failing tests** — `tests/test_fno_upstox.py`:

```python
"""Upstox's expired-instrument API, read-only (design §2.1). No test touches the network."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import upstox  # noqa: E402

TOKEN = "secret-token-value"


class Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload


class Http:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "params": params})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def client(*responses):
    http = Http(*responses)
    return upstox.UpstoxClient(TOKEN, http=http, sleep=lambda s: None), http


def test_expiries_are_dates_for_the_underlying_index():
    c, http = client(Resp({"status": "success", "data": ["2025-04-17", "2025-04-09"]}))
    assert c.expiries("NIFTY") == [date(2025, 4, 9), date(2025, 4, 17)]
    call = http.calls[0]
    assert call["url"].endswith("/v2/expired-instruments/expiries")
    assert call["params"] == {"instrument_key": "NSE_INDEX|Nifty 50"}
    assert call["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_option_contracts_keep_calls_and_puts():
    data = [
        {"instrument_key": "NSE_FO|47983|17-04-2025", "expiry": "2025-04-17", "strike_price": 20400,
         "instrument_type": "PE", "lot_size": 75, "weekly": True},
        {"instrument_key": "NSE_FO|47984|17-04-2025", "expiry": "2025-04-17", "strike_price": 20400,
         "instrument_type": "CE", "lot_size": 75, "weekly": True},
        {"instrument_key": "NSE_FO|1|17-04-2025", "expiry": "2025-04-17", "strike_price": 0,
         "instrument_type": "FUT", "lot_size": 75, "weekly": False},
    ]
    c, http = client(Resp({"status": "success", "data": data}))
    got = c.option_contracts("NIFTY", date(2025, 4, 17))
    assert [(x.option_type, x.strike, x.lot_size, x.weekly) for x in got] == [
        ("PE", 20400.0, 75, True), ("CE", 20400.0, 75, True)]
    assert http.calls[0]["params"] == {"instrument_key": "NSE_INDEX|Nifty 50", "expiry_date": "2025-04-17"}


def test_minute_candles_ask_to_then_from_and_come_back_oldest_first_in_ist():
    candles = [["2025-04-17T09:16:00+05:30", 11, 12, 10, 11.5, 300, 9000],
               ["2025-04-17T09:15:00+05:30", 10, 11, 9, 10.5, 200, 8000]]
    c, http = client(Resp({"status": "success", "data": {"candles": candles}}))
    frame = c.minute_candles("NSE_FO|47983|17-04-2025", date(2025, 4, 3), date(2025, 4, 17))
    assert http.calls[0]["url"].endswith(
        "/v2/expired-instruments/historical-candle/NSE_FO%7C47983%7C17-04-2025/1minute/2025-04-17/2025-04-03")
    assert list(frame.columns) == list(upstox.CANDLE_COLUMNS)
    assert [t.strftime("%H:%M") for t in frame["ts"]] == ["09:15", "09:16"]
    assert str(frame["ts"].dt.tz) == "Asia/Kolkata"
    assert list(frame["oi"]) == [8000, 9000]


def test_a_long_range_is_asked_for_in_pieces():
    empty = Resp({"status": "success", "data": {"candles": []}})
    c, http = client(empty, empty)
    c.minute_candles("K", date(2025, 3, 1), date(2025, 4, 15))
    assert [call["url"].rsplit("/", 2)[-2:] for call in http.calls] == [
        ["2025-03-30", "2025-03-01"], ["2025-04-15", "2025-03-31"]]


def test_needing_upstox_plus_says_so():
    c, _ = client(Resp({"status": "error", "errors": [{"errorCode": "UDAPI1149",
                                                        "message": "Plus required"}]}, 403))
    with pytest.raises(upstox.UpstoxPlusRequired, match="Upstox Plus"):
        c.expiries("NIFTY")


def test_a_refused_token_names_the_fix_and_never_the_token():
    c, _ = client(Resp({"status": "error", "errors": [{"errorCode": "UDAPI100050"}]}, 401))
    with pytest.raises(upstox.UpstoxAuthError) as err:
        c.expiries("NIFTY")
    assert "FNO_SETUP" in str(err.value) and TOKEN not in str(err.value)


def test_rate_limits_and_server_errors_are_retried():
    ok = Resp({"status": "success", "data": ["2025-04-17"]})
    c, http = client(Resp({}, 429), Resp({}, 503), ok)
    assert c.expiries("NIFTY") == [date(2025, 4, 17)]
    assert len(http.calls) == 3


def test_the_token_comes_from_the_environment_or_a_clear_error():
    assert upstox.token_from_env({"UPSTOX_ANALYTICS_TOKEN": " t "}) == "t"
    with pytest.raises(upstox.UpstoxAuthError, match="UPSTOX_ANALYTICS_TOKEN"):
        upstox.token_from_env({})
```

- [ ] **Step 2: Run** — `ModuleNotFoundError: No module named 'fno.upstox'`

- [ ] **Step 3: Write `fno/upstox.py`**

```python
"""Upstox: expired NIFTY / BANKNIFTY option contracts and their minute candles. READ-ONLY.

Three endpoints, all GET, all on Upstox Plus (design §2.1):

    /v2/expired-instruments/expiries                      the expiries Upstox still serves
    /v2/expired-instruments/option/contract               an expiry's contracts
    /v2/expired-instruments/historical-candle/{key}/1minute/{to}/{from}

There is no order code here and none may be added. The token is the owner's
one-year analytics token (docs/FNO_SETUP.md 1b). Whether Upstox accepts that
token for EXPIRED contracts is not stated in its documents; a 401 says so and
points there. Messages name the fix, never the token.

How far back: Upstox's page says "up to six months of historical expiries";
users report more. The first fill measures it (scripts/fno_minute.py prints
the oldest expiry served).

Candles arrive newest first as [timestamp(+05:30), open, high, low, close,
volume, open interest]; they are returned oldest first, timestamps in IST.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests

from config import IST

BASE = "https://api.upstox.com/v2"
UNDERLYING_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}
CANDLE_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "oi")
MAX_DAYS_PER_REQUEST = 30
PAUSE_SECONDS = 0.25          # Upstox allows 50 a second; stay far below
ATTEMPTS = 4
TIMEOUT_SECONDS = 30
TOKEN_ENV = "UPSTOX_ANALYTICS_TOKEN"
PLUS_REQUIRED = "UDAPI1149"


class UpstoxError(RuntimeError):
    """Upstox answered with something this client cannot use."""


class UpstoxAuthError(UpstoxError):
    """No token, or Upstox refused it."""


class UpstoxPlusRequired(UpstoxError):
    """The account does not have Upstox Plus."""


@dataclass(frozen=True)
class Contract:
    key: str
    expiry: date
    strike: float
    option_type: str
    lot_size: int
    weekly: bool


def token_from_env(env: Mapping[str, str] | None = None) -> str:
    token = (os.environ if env is None else env).get(TOKEN_ENV, "").strip()
    if not token:
        raise UpstoxAuthError(f"{TOKEN_ENV} is not set: make it on Upstox's Developer Apps page, "
                              "Analytics tab, and save it in .env (docs/FNO_SETUP.md 1b and 4)")
    return token


def _candles(rows: list[list[Any]]) -> pd.DataFrame:
    padded = [list(r[:7]) + [None] * (7 - len(r)) for r in rows]
    frame = pd.DataFrame(padded, columns=list(CANDLE_COLUMNS))
    frame["ts"] = pd.to_datetime(frame["ts"]).dt.tz_convert(IST)
    for col in CANDLE_COLUMNS[1:5]:
        frame[col] = frame[col].astype(float)
    frame["volume"] = frame["volume"].fillna(0).astype("int64")
    frame["oi"] = frame["oi"].fillna(0).astype("int64")
    return frame


class UpstoxClient:
    def __init__(self, token: str, *, http: Any = requests,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._token = token
        self._http = http
        self._sleep = sleep

    def __repr__(self) -> str:
        return "UpstoxClient(token=<hidden>)"

    def _get(self, path: str, params: dict[str, str] | None, doing: str) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        last = ""
        for attempt in range(1, ATTEMPTS + 1):
            self._sleep(PAUSE_SECONDS if attempt == 1 else 2 ** attempt)
            try:
                response = self._http.get(BASE + path, headers=headers, params=params,
                                          timeout=TIMEOUT_SECONDS)
            except requests.exceptions.RequestException as exc:
                last = type(exc).__name__          # never the exception: it carries the request
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            errors = payload.get("errors") or [] if isinstance(payload, dict) else []
            codes = {e.get("errorCode") for e in errors if isinstance(e, dict)}
            if PLUS_REQUIRED in codes:
                raise UpstoxPlusRequired(f"Upstox says {doing} needs an Upstox Plus subscription "
                                         f"({PLUS_REQUIRED}): activate Plus (docs/FNO_SETUP.md 1a)")
            status = response.status_code
            if status == 401:
                raise UpstoxAuthError(
                    f"Upstox refused the token (HTTP 401) while {doing}. If {TOKEN_ENV} is new, "
                    "Upstox may not accept the analytics token for expired contracts - see "
                    "'One open question' in docs/FNO_SETUP.md; otherwise make a new token (1b).")
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                continue
            if status >= 400 or payload.get("status") != "success":
                message = "; ".join(f"{e.get('errorCode')}: {e.get('message')}" for e in errors)
                raise UpstoxError(f"Upstox HTTP {status} while {doing}: {message or 'no detail'}")
            return payload.get("data")
        raise UpstoxError(f"Upstox did not answer while {doing} after {ATTEMPTS} tries ({last})")

    def expiries(self, underlying: str) -> list[date]:
        data = self._get("/expired-instruments/expiries",
                         {"instrument_key": UNDERLYING_KEYS[underlying]}, f"listing {underlying} expiries")
        return sorted(date.fromisoformat(d) for d in data or [])

    def option_contracts(self, underlying: str, expiry: date) -> list[Contract]:
        data = self._get("/expired-instruments/option/contract",
                         {"instrument_key": UNDERLYING_KEYS[underlying], "expiry_date": expiry.isoformat()},
                         f"listing {underlying} contracts expiring {expiry}")
        return [Contract(row["instrument_key"], date.fromisoformat(row["expiry"]),
                         float(row["strike_price"]), row["instrument_type"], int(row["lot_size"]),
                         bool(row.get("weekly", False)))
                for row in data or [] if row.get("instrument_type") in ("CE", "PE")]

    def minute_candles(self, key: str, start: date, end: date) -> pd.DataFrame:
        """1-minute candles of one expired contract, start..end inclusive, oldest first."""
        frames = []
        day = start
        while day <= end:
            last = min(day + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
            path = (f"/expired-instruments/historical-candle/{quote(key, safe='')}/1minute/"
                    f"{last.isoformat()}/{day.isoformat()}")
            data = self._get(path, None, f"fetching minutes of {key}") or {}
            frames.append(_candles(data.get("candles") or []))
            day = last + timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=list(CANDLE_COLUMNS))
        out = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
        return out.reset_index(drop=True)
```

- [ ] **Step 4: Run** — passes.

- [ ] **Step 5: Commit** — `feat(fno): Upstox client for expired options - errors name the fix, never the token`

---

### Task 3: Which contracts, kept where, and checked

**Files:** Create `fno/minute.py`, `tests/test_fno_minute.py`

- [ ] **Step 1: Failing tests** — `tests/test_fno_minute.py`:

```python
"""Minute prices of expired options: which contracts, where kept, how checked (design §2.2, §2.6)."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST  # noqa: E402
from fno import minute  # noqa: E402
from fno.nse_files import COLUMNS, DTYPES  # noqa: E402
from fno.upstox import CANDLE_COLUMNS, Contract  # noqa: E402

EXPIRY = date(2025, 4, 17)
SESSIONS = [date(2025, 3, 1) + timedelta(days=i) for i in range(48)]   # every day a session, for counting


def contract(strike, typ="CE", weekly=True):
    return Contract(f"NSE_FO|{int(strike)}{typ}|17-04-2025", EXPIRY, float(strike), typ, 75, weekly)


def test_the_window_is_the_final_10_sessions_weekly_or_20_monthly():
    assert minute.window(EXPIRY, SESSIONS, weekly=True) == SESSIONS[38:48]
    assert minute.window(EXPIRY, SESSIONS, weekly=False) == SESSIONS[28:48]


def test_the_band_keeps_strikes_within_5pct_of_any_close_in_the_window():
    days = [date(2025, 4, 16), date(2025, 4, 17)]
    closes = {date(2025, 4, 16): 22000.0, date(2025, 4, 17): 23000.0}
    kept = minute.in_band([contract(s) for s in (20800, 20950, 24100, 24200)], closes, days)
    assert [c.strike for c in kept] == [20950.0, 24100.0]       # 22000 - 4.8%, 23000 + 4.8%


class FakeUpstox:
    def __init__(self, contracts, candles):
        self.contracts, self.candles, self.asked = contracts, candles, []

    def option_contracts(self, underlying, expiry):
        return self.contracts

    def minute_candles(self, key, start, end):
        self.asked.append((key, start, end))
        return self.candles.get(key, pd.DataFrame(columns=list(CANDLE_COLUMNS)))


def candles(day, closes):
    ts = [pd.Timestamp(f"{day} 09:15", tz=IST) + pd.Timedelta(minutes=i) for i in range(len(closes))]
    return pd.DataFrame({"ts": ts, "open": closes, "high": closes, "low": closes, "close": closes,
                         "volume": [10] * len(closes), "oi": [100] * len(closes)})


def nse_rows(rows):
    out = []
    for day, strike, typ, close, volume in rows:
        out.append({"trade_date": day, "underlying": "NIFTY", "instrument": "OPT", "expiry": EXPIRY,
                    "strike": strike, "option_type": typ, "open": close, "high": close, "low": close,
                    "close": close, "settle": close, "volume_contracts": volume, "oi_units": 0,
                    "oi_change_units": 0, "turnover_inr": 0.0, "lot_size": np.nan,
                    "underlying_price": np.nan})
    return pd.DataFrame(out, columns=list(COLUMNS)).astype(DTYPES)


def test_fetching_an_expiry_stores_the_band_and_records_it(tmp_path):
    near, far = contract(22000), contract(30000)
    up = FakeUpstox([near, far], {near.key: candles(EXPIRY, [10.0, 11.0])})
    closes = {d: 22000.0 for d in SESSIONS}
    row = minute.fetch_expiry(up, tmp_path, "NIFTY", EXPIRY, SESSIONS, closes,
                              daily=nse_rows([]), today=date(2025, 4, 20))
    assert (row["status"], row["contracts"], row["rows"], row["first_day"]) == ("ok", 1, 2, SESSIONS[38])
    assert up.asked == [(near.key, SESSIONS[38], EXPIRY)]
    stored = minute.read_expiry(tmp_path, "NIFTY", EXPIRY)
    assert list(stored.columns) == list(minute.COLUMNS)
    assert set(stored["strike"]) == {22000.0} and set(stored["option_type"]) == {"CE"}
    minute.record(tmp_path, row)
    assert minute.load_ledger(tmp_path) == {("NIFTY", EXPIRY): "ok"}


def test_due_is_every_served_expired_expiry_not_yet_in_the_ledger():
    ledger = {("NIFTY", date(2025, 4, 9)): "ok"}
    served = [date(2025, 4, 9), date(2025, 4, 17), date(2025, 4, 24)]
    assert minute.due("NIFTY", served, ledger, today=date(2025, 4, 20)) == [date(2025, 4, 17)]


def test_minute_closes_are_checked_against_nse_s_daily_close():
    mins = candles(EXPIRY, [10.0, 12.0]).assign(strike=22000.0, option_type="CE")
    daily = nse_rows([(EXPIRY, 22000.0, "CE", 12.1, 500),      # agrees within tolerance
                      (EXPIRY, 22000.0, "PE", 40.0, 500)])     # traded, but no minutes fetched
    issues = minute.check_expiry("NIFTY", EXPIRY, mins, daily, [EXPIRY], {(22000.0, "CE"), (22000.0, "PE")})
    assert [(i["check"], i["count"]) for i in issues] == [("minute_day_missing", 1)]
    worse = nse_rows([(EXPIRY, 22000.0, "CE", 20.0, 500)])
    issues = minute.check_expiry("NIFTY", EXPIRY, mins, worse, [EXPIRY], {(22000.0, "CE")})
    assert [(i["check"], i["count"]) for i in issues] == [("minute_close_mismatch", 1)]
```

- [ ] **Step 2: Run** — `ModuleNotFoundError: No module named 'fno.minute'`

- [ ] **Step 3: Write `fno/minute.py`**

```python
"""Minute prices of expired NIFTY / BANKNIFTY options (design §2.2, §2.6).

WHICH: for each expiry, every strike within BAND_PCT of the index's daily
close on any of the expiry's final WEEKLY_SESSIONS (weekly cycles) or
MONTHLY_SESSIONS (monthly) sessions, for those sessions. Sessions come from
the NSE ledger, closes from fno/spot.py, weekly or monthly from Upstox's own
flag on the contracts.

WHERE: data/fno/minute/<UNDERLYING>/<EXPIRY>.parquet, one file per expiry,
written whole; minute/_expiries.csv records each expiry fetched and what it
held, so a stopped fill carries on and nothing is asked for twice.

CHECKED (written to quality/<YEAR>.csv like the daily checks, never repaired):
a contract's last minute close against NSE's close that day, beyond the larger
of Rs 1 and 2%; and a contract NSE says traded on a window day with no
minutes at all.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from fno.quality import write_issues
from fno.upstox import CANDLE_COLUMNS, Contract
from fno.yahoo import write_whole

WEEKLY_SESSIONS = 10
MONTHLY_SESSIONS = 20
BAND_PCT = 5.0
COLUMNS = ("ts", "strike", "option_type", "open", "high", "low", "close", "volume", "oi")
LEDGER = Path("minute") / "_expiries.csv"
LEDGER_COLUMNS = ("underlying", "expiry", "status", "contracts", "rows", "first_day", "fetched_on")
CLOSE_TOLERANCE_RS = 1.0
CLOSE_TOLERANCE_PCT = 2.0


def window(expiry: date, sessions: list[date], *, weekly: bool) -> list[date]:
    """The expiry's final 10 (weekly) or 20 (monthly) sessions, the expiry day included."""
    before = [d for d in sorted(sessions) if d <= expiry]
    return before[-(WEEKLY_SESSIONS if weekly else MONTHLY_SESSIONS):]


def in_band(contracts: Iterable[Contract], closes: Mapping[date, float], days: list[date]) -> list[Contract]:
    levels = [closes[d] for d in days if d in closes and closes[d] == closes[d]]
    return [c for c in contracts
            if any(abs(c.strike / level - 1) * 100 <= BAND_PCT for level in levels)]


def expiry_path(root: Path, underlying: str, expiry: date) -> Path:
    return Path(root) / "minute" / underlying / f"{expiry.isoformat()}.parquet"


def read_expiry(root: Path, underlying: str, expiry: date) -> pd.DataFrame:
    path = expiry_path(root, underlying, expiry)
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=list(COLUMNS))


def load_ledger(root: Path) -> dict[tuple[str, date], str]:
    path = Path(root) / LEDGER
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(r["underlying"], date.fromisoformat(r["expiry"])): r["status"] for r in csv.DictReader(f)}


def record(root: Path, row: Mapping[str, Any]) -> None:
    path = Path(root) / LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS, lineterminator="\n")
        if new:
            w.writeheader()
        w.writerow({k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()})


def due(underlying: str, served: Iterable[date], ledger: Mapping[tuple[str, date], str],
        *, today: date) -> list[date]:
    """Served expiries that have expired and are not in the ledger (whatever they held)."""
    return sorted(e for e in served if e < today and (underlying, e) not in ledger)


def check_expiry(underlying: str, expiry: date, minutes: pd.DataFrame, daily: pd.DataFrame,
                 days: list[date], fetched: set[tuple[float, str]]) -> list[dict[str, Any]]:
    nse = daily[(daily["instrument"] == "OPT") & (daily["expiry"] == expiry)
                & daily["trade_date"].isin(days) & (daily["volume_contracts"] > 0)]
    nse = nse[[(s, t) in fetched for s, t in zip(nse["strike"], nse["option_type"])]]
    if minutes.empty:
        last = pd.DataFrame(columns=["day", "strike", "option_type", "close"])
    else:
        m = minutes.assign(day=minutes["ts"].dt.date).sort_values("ts")
        last = m.groupby(["day", "strike", "option_type"], as_index=False).tail(1)
    issues: list[dict[str, Any]] = []

    merged = last.merge(nse, left_on=["day", "strike", "option_type"],
                        right_on=["trade_date", "strike", "option_type"], suffixes=("_min", "_nse"))
    limit = np.maximum(CLOSE_TOLERANCE_RS, merged["close_nse"] * CLOSE_TOLERANCE_PCT / 100) \
        if len(merged) else pd.Series(dtype=float)
    bad = merged[(merged["close_min"] - merged["close_nse"]).abs() > limit] if len(merged) else merged
    for day, rows in bad.groupby("day"):
        r = rows.iloc[0]
        issues.append({"trade_date": day, "underlying": underlying, "check": "minute_close_mismatch",
                       "count": len(rows), "example": f"OPT {expiry} {r['strike']} {r['option_type']} "
                                                      f"minute {r['close_min']} vs NSE {r['close_nse']}"})

    have = set(zip(last["day"], last["strike"], last["option_type"]))
    missing = nse[[(d, s, t) not in have for d, s, t in
                   zip(nse["trade_date"], nse["strike"], nse["option_type"])]]
    for day, rows in missing.groupby("trade_date"):
        r = rows.iloc[0]
        issues.append({"trade_date": day, "underlying": underlying, "check": "minute_day_missing",
                       "count": len(rows), "example": f"OPT {expiry} {r['strike']} {r['option_type']}"})
    return sorted(issues, key=lambda i: (i["trade_date"], i["check"]))


def fetch_expiry(client: Any, root: Path, underlying: str, expiry: date, sessions: list[date],
                 closes: Mapping[date, float], *, daily: pd.DataFrame, today: date) -> dict[str, Any]:
    """Fetch, store and check one expiry; the ledger row to record (not yet recorded)."""
    row: dict[str, Any] = {"underlying": underlying, "expiry": expiry, "status": "empty",
                           "contracts": 0, "rows": 0, "first_day": "", "fetched_on": today}
    contracts = client.option_contracts(underlying, expiry)
    if not contracts:
        row["status"] = "no contracts"
        return row
    days = window(expiry, sessions, weekly=any(c.weekly for c in contracts))
    if not days:
        row["status"] = "no sessions"
        return row
    chosen = in_band(contracts, closes, days)
    frames = []
    for c in chosen:
        candles = client.minute_candles(c.key, days[0], expiry)
        if not candles.empty:
            frames.append(candles.assign(strike=c.strike, option_type=c.option_type))
    frame = (pd.concat(frames, ignore_index=True)[list(COLUMNS)] if frames
             else pd.DataFrame(columns=list(COLUMNS)))
    row.update(contracts=len(chosen), rows=len(frame), first_day=days[0])
    if not frame.empty:
        write_whole(expiry_path(root, underlying, expiry),
                    frame.sort_values(["strike", "option_type", "ts"]).reset_index(drop=True))
        row["status"] = "ok"
    write_issues(root, check_expiry(underlying, expiry, frame, daily, days,
                                    {(c.strike, c.option_type) for c in chosen}))
    return row
```

- [ ] **Step 4: Run** — passes.

- [ ] **Step 5: Commit** — `feat(fno): minute store for expired options - the band, the window, a ledger, checks`

---

### Task 4: Index and India VIX minutes

**Files:** Create `fno/index_minutes.py`; add to `tests/test_fno_index_minutes.py`

- [ ] **Step 1: Failing tests** — append:

```python
class FakeProvider:
    """Answers fetch_minutes with one 09:15 candle per requested IST day."""

    def __init__(self):
        self.asked = []

    def fetch_minutes(self, symbol, from_utc, to_utc):
        self.asked.append((symbol, from_utc, to_utc))
        days = pd.date_range(from_utc.astimezone(index_minutes.IST).date(),
                             to_utc.astimezone(index_minutes.IST).date(), freq="D")
        idx = pd.DatetimeIndex([pd.Timestamp(f"{d.date()} 09:15", tz=index_minutes.IST) for d in days]).tz_convert("UTC")
        return pd.DataFrame({"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 0}, index=idx)


def test_a_first_fill_starts_at_dhan_s_archive_and_is_stored_by_year(tmp_path):
    p = FakeProvider()
    n = index_minutes.fill(p, tmp_path, "NIFTY", today=date(2017, 12, 31), log=lambda *a: None)
    assert p.asked[0][1].astimezone(index_minutes.IST).date() == index_minutes.ARCHIVE_BEGINS
    assert n == (date(2017, 12, 31) - index_minutes.ARCHIVE_BEGINS).days + 1
    assert index_minutes.path_for(tmp_path, "NIFTY", 2017).exists()
    assert index_minutes.last_stored(tmp_path, "NIFTY") == date(2017, 12, 31)


def test_a_later_fill_refetches_the_last_day_and_never_duplicates(tmp_path):
    p = FakeProvider()
    index_minutes.fill(p, tmp_path, "BANKNIFTY", today=date(2018, 1, 10), log=lambda *a: None)
    p.asked.clear()
    index_minutes.fill(p, tmp_path, "BANKNIFTY", today=date(2018, 1, 12), log=lambda *a: None)
    assert p.asked[0][1].astimezone(index_minutes.IST).date() == date(2018, 1, 10)
    got = index_minutes.read(tmp_path, "BANKNIFTY", date(2018, 1, 1), date(2018, 1, 31))
    assert len(got) == 12 and got["ts"].is_unique
    assert str(got["ts"].dt.tz) == "Asia/Kolkata"


def test_an_unknown_index_is_refused():
    import pytest
    from providers.base import ProviderError
    with pytest.raises(ProviderError, match="FINNIFTY"):
        index_minutes.IndexResolver().resolve("FINNIFTY")
```

- [ ] **Step 2: Run** — fails (`fill` missing).

- [ ] **Step 3: Write `fno/index_minutes.py`**

```python
"""NIFTY, BANKNIFTY and India VIX minute by minute, from Dhan (design §2.1).

Dhan's ids from its scrip master (checked 9 Oct 2026): NIFTY 13, BANKNIFTY 25,
INDIA VIX 21, all on the IDX_I segment. Dhan's intraday archive starts on
3 Apr 2017 (measured for stocks on 22 Aug 2026; the first index fill states
what it found).

Stored as data/fno/index_minute/<NAME>/<YEAR>.parquet with columns ts (IST),
open, high, low, close, volume. Each 90-day chunk is stored as it arrives, so
a stopped fill carries on; a later fill starts again from the last stored
day, because a run during market hours stores a partial day.

Dhan's timestamps are read as true UTC instants (providers/dhan.py says why
that is unverified); the first fill prints the first and last minute of a day
so 09:15 and 15:29 can be checked by eye.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from config import IST, UTC
from fno.yahoo import write_whole
from providers.base import ProviderError

INDEXES = {"NIFTY": ("13", "IDX_I", "INDEX"), "BANKNIFTY": ("25", "IDX_I", "INDEX"),
           "INDIAVIX": ("21", "IDX_I", "INDEX")}
COLUMNS = ("ts", "open", "high", "low", "close", "volume")
ARCHIVE_BEGINS = date(2017, 4, 3)
CHUNK_DAYS = 90


class IndexResolver:
    def resolve(self, symbol: str) -> tuple[str, str, str]:
        if symbol not in INDEXES:
            raise ProviderError(f"{symbol}: no Dhan index id known (have {', '.join(INDEXES)})")
        return INDEXES[symbol]


def path_for(root: Path, name: str, year: int) -> Path:
    return Path(root) / "index_minute" / name / f"{year}.parquet"


def _years(root: Path, name: str) -> list[int]:
    folder = Path(root) / "index_minute" / name
    return sorted(int(p.stem) for p in folder.glob("*.parquet")) if folder.exists() else []


def last_stored(root: Path, name: str) -> date | None:
    for year in reversed(_years(root, name)):
        frame = pd.read_parquet(path_for(root, name, year), columns=["ts"])
        if len(frame):
            return frame["ts"].max().date()
    return None


def read(root: Path, name: str, start: date, end: date) -> pd.DataFrame:
    frames = [pd.read_parquet(p) for y in range(start.year, end.year + 1)
              if (p := path_for(root, name, y)).exists()]
    if not frames:
        return pd.DataFrame(columns=list(COLUMNS))
    out = pd.concat(frames, ignore_index=True)
    days = out["ts"].dt.date
    return out[(days >= start) & (days <= end)].reset_index(drop=True)


def _to_frame(canonical: pd.DataFrame) -> pd.DataFrame:
    frame = canonical.reset_index()
    frame = frame.rename(columns={frame.columns[0]: "ts"})
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True).dt.tz_convert(IST)
    return frame[list(COLUMNS)]


def _store(root: Path, name: str, frame: pd.DataFrame) -> None:
    for year, part in frame.groupby(frame["ts"].dt.year):
        path = path_for(root, name, int(year))
        old = pd.read_parquet(path) if path.exists() else None
        merged = part if old is None else pd.concat([old, part], ignore_index=True)
        merged = merged.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        write_whole(path, merged)


def fill(provider: Any, root: Path, name: str, *, today: date,
         log: Callable[..., None] = print) -> int:
    """Fetch from the last stored day (or the archive start) to `today`; rows stored."""
    day = last_stored(root, name) or ARCHIVE_BEGINS
    total = 0
    while day <= today:
        last = min(day + timedelta(days=CHUNK_DAYS - 1), today)
        canonical = provider.fetch_minutes(
            name, datetime.combine(day, time(0, 0), IST).astimezone(UTC),
            datetime.combine(last, time(23, 59), IST).astimezone(UTC))
        if not canonical.empty:
            frame = _to_frame(canonical)
            _store(root, name, frame)
            total += len(frame)
        log(f"  {name} {day} -> {last}: {total:,} minutes so far", flush=True)
        day = last + timedelta(days=1)
    return total


def create_provider() -> Any:
    """A Dhan provider for the indexes, sharing the stock pipeline's token cache."""
    from db import SupabaseStore
    from dhan_auth import DhanCredentials, DhanTokenManager
    from providers.dhan import DhanProvider
    from supabase_candle_backend import SupabaseCandleBackend

    credentials = DhanCredentials.from_env()
    backend = SupabaseCandleBackend(SupabaseStore.from_env()._client)
    return DhanProvider(token_manager=DhanTokenManager(credentials, backend),
                        instrument_resolver=IndexResolver(), client_id=credentials.client_id)
```

- [ ] **Step 4: Run** `tests/test_fno_index_minutes.py tests/test_dhan_provider.py` — passes.

- [ ] **Step 5: Commit** — `feat(fno): NIFTY, BANKNIFTY and India VIX minutes from Dhan, resumable`

---

### Task 5: Cloudflare R2

**Files:** Create `fno/r2.py`, `tests/test_fno_r2.py`; Modify `requirements.txt`

- [ ] **Step 1: Failing tests** — `tests/test_fno_r2.py`:

```python
"""The F&O store in a private Cloudflare R2 bucket: push, pull, compare (design §2.2)."""

from __future__ import annotations

import hashlib
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import r2  # noqa: E402

ENV = {"R2_ACCOUNT_ID": "acct", "R2_ACCESS_KEY_ID": "kid", "R2_SECRET_ACCESS_KEY": "shh",
       "R2_BUCKET": "trading-tool-fno"}


class FakeS3:
    def __init__(self, objects=None):
        self.objects = dict(objects or {})
        self.uploaded, self.downloaded = [], []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        s3 = self

        class Pages:
            def paginate(self, Bucket, Prefix):
                hits = [{"Key": k, "Size": len(v), "ETag": f'"{hashlib.md5(v).hexdigest()}"'}
                        for k, v in sorted(s3.objects.items()) if k.startswith(Prefix)]
                return [{"Contents": hits}] if hits else [{}]

        return Pages()

    def upload_file(self, Filename, Bucket, Key, Config=None):
        self.objects[Key] = Path(Filename).read_bytes()
        self.uploaded.append(Key)

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])
        self.downloaded.append(Key)


def put(root: Path, rel: str, data: bytes) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_config_from_env_names_what_is_missing_and_hides_the_secret():
    cfg = r2.R2Config.from_env(ENV)
    assert cfg.endpoint == "https://acct.r2.cloudflarestorage.com"
    assert "shh" not in repr(cfg) and "kid" not in repr(cfg)
    with pytest.raises(r2.R2ConfigError, match="R2_BUCKET, R2_SECRET_ACCESS_KEY"):
        r2.R2Config.from_env({"R2_ACCOUNT_ID": "a", "R2_ACCESS_KEY_ID": "k"})


def test_push_uploads_new_and_changed_files_only(tmp_path):
    put(tmp_path, "daily/_sessions.csv", b"same")
    put(tmp_path, "daily/NIFTY/2026.parquet", b"new contents")
    put(tmp_path, "vix/daily.parquet", b"brand new")
    put(tmp_path, "daily/NIFTY/2026.parquet.tmp", b"half written")
    s3 = FakeS3({"fno/daily/_sessions.csv": b"same", "fno/daily/NIFTY/2026.parquet": b"old"})
    assert r2.push(s3, "b", tmp_path) == ["daily/NIFTY/2026.parquet", "vix/daily.parquet"]
    assert s3.objects["fno/daily/NIFTY/2026.parquet"] == b"new contents"
    assert r2.push(s3, "b", tmp_path) == []


def test_pull_fetches_missing_and_changed_files_within_the_prefixes(tmp_path):
    put(tmp_path, "daily/_sessions.csv", b"stale")
    s3 = FakeS3({"fno/daily/_sessions.csv": b"fresh", "fno/vix/daily.parquet": b"v",
                 "fno/minute/NIFTY/2025-04-17.parquet": b"m"})
    got = r2.pull(s3, "b", tmp_path, ["daily/_sessions.csv", "vix/"])
    assert got == ["daily/_sessions.csv", "vix/daily.parquet"]
    assert (tmp_path / "daily/_sessions.csv").read_bytes() == b"fresh"
    assert not (tmp_path / "minute").exists()
    assert r2.pull(s3, "b", tmp_path, ["daily/_sessions.csv", "vix/"]) == []


def test_the_daily_job_reads_this_year_and_last_year_only_in_january():
    june = r2.daily_job_prefixes(date(2026, 6, 15))
    assert "daily/NIFTY/2026.parquet" in june and "daily/NIFTY/2025.parquet" not in june
    assert {"daily/_sessions.csv", "minute/_expiries.csv", "vix/", "spot/",
            "index_minute/INDIAVIX/2026.parquet"} <= set(june)
    assert "participants/2025.parquet" in r2.daily_job_prefixes(date(2026, 1, 5))
```

- [ ] **Step 2: Run** — `ModuleNotFoundError: No module named 'fno.r2'`

- [ ] **Step 3: Write `fno/r2.py`**

```python
"""The F&O store in Cloudflare R2: publish local files, fetch them back (design §2.2).

The bucket is PRIVATE - broker data is licensed for the owner's own use and
the repository is public - and mirrors data/fno exactly under the key prefix
`fno/`. Files are compared by size and MD5: R2's ETag for a single-part upload
is the MD5 of the bytes, and every upload here is single-part (the threshold
is set far above any file this store writes). Push never deletes anything.

Credentials come from the environment (docs/FNO_SETUP.md 3c and 4) and are
never printed: R2Config's repr hides them.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from fno.contracts import INDEX_UNDERLYINGS
from fno.index_minutes import INDEXES

KEY_PREFIX = "fno/"
SINGLE_PART_UP_TO = 512 * 1024 * 1024
ENV_NAMES = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")


class R2ConfigError(RuntimeError):
    """An R2 setting is missing."""


@dataclass(frozen=True)
class R2Config:
    account_id: str
    access_key_id: str = field(repr=False)
    secret_access_key: str = field(repr=False)
    bucket: str

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "R2Config":
        source = os.environ if env is None else env
        values = {name: source.get(name, "").strip() for name in ENV_NAMES}
        missing = sorted(name for name, value in values.items() if not value)
        if missing:
            raise R2ConfigError(f"missing {', '.join(missing)}: see docs/FNO_SETUP.md sections 3 and 4")
        return cls(*(values[name] for name in ENV_NAMES))

    @property
    def endpoint(self) -> str:
        return f"https://{self.account_id}.r2.cloudflarestorage.com"


def connect(config: R2Config) -> Any:
    import boto3

    return boto3.client("s3", endpoint_url=config.endpoint, region_name="auto",
                        aws_access_key_id=config.access_key_id,
                        aws_secret_access_key=config.secret_access_key)


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _wanted(rel: str, prefixes: Iterable[str]) -> bool:
    return any(rel.startswith(p) for p in prefixes)


def local_files(root: Path, prefixes: Iterable[str] = ("",)) -> dict[str, Path]:
    root = Path(root)
    prefixes = list(prefixes)
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() and not p.name.endswith(".tmp")
            and _wanted(p.relative_to(root).as_posix(), prefixes)}


def remote_objects(s3: Any, bucket: str, prefixes: Iterable[str] = ("",)) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for prefix in prefixes:
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=KEY_PREFIX + prefix):
            for obj in page.get("Contents", []):
                out[obj["Key"][len(KEY_PREFIX):]] = (int(obj["Size"]), obj["ETag"].strip('"'))
    return out


def _same(path: Path, remote: tuple[int, str] | None) -> bool:
    return remote is not None and path.stat().st_size == remote[0] and _md5(path) == remote[1]


def push(s3: Any, bucket: str, root: Path, prefixes: Iterable[str] = ("",), *,
         dry_run: bool = False) -> list[str]:
    """Upload every local file that R2 lacks or holds differently; the paths uploaded."""
    from boto3.s3.transfer import TransferConfig

    prefixes = list(prefixes)
    remote = remote_objects(s3, bucket, prefixes)
    sent = []
    for rel, path in local_files(root, prefixes).items():
        if _same(path, remote.get(rel)):
            continue
        if not dry_run:
            s3.upload_file(str(path), bucket, KEY_PREFIX + rel,
                           Config=TransferConfig(multipart_threshold=SINGLE_PART_UP_TO))
        sent.append(rel)
    return sent


def pull(s3: Any, bucket: str, root: Path, prefixes: Iterable[str] = ("",)) -> list[str]:
    """Download every object under `prefixes` that is missing or different locally."""
    got = []
    for rel, remote in sorted(remote_objects(s3, bucket, list(prefixes)).items()):
        path = Path(root) / rel
        if path.exists() and _same(path, remote):
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        s3.download_file(bucket, KEY_PREFIX + rel, str(tmp))
        os.replace(tmp, path)
        got.append(rel)
    return got


def daily_job_prefixes(today: date) -> list[str]:
    """What the weekday job reads and appends to: this year's files (and last year's
    in January, when NSE's late files and the minute window can reach back), the
    ledgers, VIX and the index closes - not the whole 3 GB store."""
    years = [today.year - 1, today.year] if today.month == 1 else [today.year]
    out = ["daily/_sessions.csv", "minute/_expiries.csv", "vix/", "spot/"]
    for y in years:
        out += [f"daily/{u}/{y}.parquet" for u in INDEX_UNDERLYINGS]
        out += [f"participants/{y}.parquet", f"quality/{y}.csv"]
        out += [f"index_minute/{name}/{y}.parquet" for name in INDEXES]
    return out
```

- [ ] **Step 4:** add to `requirements.txt` after the `requests` pin:

```
# Cloudflare R2 (the F&O store, design 2026-10-02 sec 2.2) speaks Amazon's S3
# protocol; boto3 is the standard client for it.
boto3==1.43.110
```

- [ ] **Step 5: Run** — passes. **Commit** — `feat(fno): the F&O store in a private R2 bucket - push, pull, compared by MD5`

---

### Task 6: The scripts

**Files:** Create `scripts/fno_minute.py`, `scripts/fno_r2.py`, `scripts/fno_check_setup.py`

`scripts/fno_minute.py`:

```python
"""Fill or top up the minute store: expired NIFTY / BANKNIFTY options from Upstox,
the indexes and India VIX from Dhan.

    python scripts/fno_minute.py                  # everything due
    python scripts/fno_minute.py --options-only
    python scripts/fno_minute.py --indexes-only
    python scripts/fno_minute.py --only NIFTY

Resumable: minute/_expiries.csv records every expiry fetched, index files say
how far they reach. Run scripts/fno_daily.py first - the strike band reads
the NSE session list and the index closes.

Needs UPSTOX_ANALYTICS_TOKEN (options) and DHAN_* plus SUPABASE_* (indexes).
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, use_utf8_stdout  # noqa: E402
from fno import index_minutes, minute, spot, store, upstox  # noqa: E402
from fno.contracts import MINUTE_UNDERLYINGS  # noqa: E402


def options(root: Path, underlyings: list[str], today) -> int:
    client = upstox.UpstoxClient(upstox.token_from_env())
    sessions = sorted(d for d, status in store.load_ledger(root).items() if status == "ok")
    for underlying in underlyings:
        try:
            spot.refresh(root, underlying)
        except Exception as exc:          # Yahoo hiccup: yesterday's closes still serve
            print(f"{underlying}: index closes not refreshed ({type(exc).__name__}); using stored")
        closes = spot.closes(root, underlying)
        served = client.expiries(underlying)
        todo = minute.due(underlying, served, minute.load_ledger(root), today=today)
        print(f"{underlying}: Upstox serves {len(served)} expiries"
              + (f", {served[0]} -> {served[-1]}" if served else "") + f"; {len(todo)} to fetch", flush=True)
        for expiry in todo:
            # 45 days back covers a monthly expiry's 20-session window
            daily = store.read_daily(root, underlying, expiry - timedelta(days=45), expiry)
            row = minute.fetch_expiry(client, root, underlying, expiry, sessions, closes,
                                      daily=daily, today=today)
            minute.record(root, row)
            print(f"  {expiry}: {row['status']}, {row['contracts']} contracts, {row['rows']:,} minutes",
                  flush=True)
    return 0


def indexes(root: Path, today) -> int:
    provider = index_minutes.create_provider()
    for name in index_minutes.INDEXES:
        n = index_minutes.fill(provider, root, name, today=today)
        print(f"{name}: {n:,} minutes fetched", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=MINUTE_UNDERLYINGS)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--options-only", action="store_true")
    group.add_argument("--indexes-only", action="store_true")
    args = parser.parse_args(argv)
    root = store.root_dir()
    today = datetime.now(IST).date()
    failed = False
    if not args.indexes_only:
        try:
            options(root, [args.only] if args.only else list(MINUTE_UNDERLYINGS), today)
        except upstox.UpstoxError as exc:
            print(f"OPTIONS STOPPED: {exc}", file=sys.stderr)
            failed = True
    if not args.options_only:
        try:
            indexes(root, today)
        except Exception as exc:
            print(f"INDEXES STOPPED: {type(exc).__name__}: {exc}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/fno_r2.py`:

```python
"""Copy the F&O store to and from the private Cloudflare R2 bucket.

    python scripts/fno_r2.py push              # every local file R2 lacks or holds differently
    python scripts/fno_r2.py push --dry-run    # say what would go, send nothing
    python scripts/fno_r2.py pull              # everything in R2 that is missing or different here
    python scripts/fno_r2.py pull --daily-job  # only what the weekday job reads

Needs R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
(docs/FNO_SETUP.md). Never deletes anything on either side.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, use_utf8_stdout  # noqa: E402
from fno import r2, store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("direction", choices=("push", "pull"))
    parser.add_argument("--daily-job", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    config = r2.R2Config.from_env()
    s3 = r2.connect(config)
    root = store.root_dir()
    prefixes = r2.daily_job_prefixes(datetime.now(IST).date()) if args.daily_job else [""]
    if args.direction == "push":
        moved = r2.push(s3, config.bucket, root, prefixes, dry_run=args.dry_run)
        verb = "would upload" if args.dry_run else "uploaded"
    else:
        moved = r2.pull(s3, config.bucket, root, prefixes)
        verb = "downloaded"
    for rel in moved:
        print(f"  {rel}")
    print(f"{verb} {len(moved)} file(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

`scripts/fno_check_setup.py`:

```python
"""Check the owner's step-3 keys, one by one: works, or fails and why. Never prints a value.

    python scripts/fno_check_setup.py

Upstox: lists NIFTY expiries, then asks for one expired contract's minutes -
the second call is the real test, because whether the analytics token covers
expired contracts is the open question in docs/FNO_SETUP.md.
Dhan: one day of NIFTY minutes. R2: lists the bucket, writes and deletes a
small test file.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import IST, UTC, use_utf8_stdout  # noqa: E402
from fno import index_minutes, r2, upstox  # noqa: E402


def check_upstox() -> str:
    client = upstox.UpstoxClient(upstox.token_from_env())
    served = client.expiries("NIFTY")
    if not served:
        return "token accepted, but Upstox lists no expired NIFTY expiries"
    contracts = client.option_contracts("NIFTY", served[-1])
    middle = sorted(contracts, key=lambda c: c.strike)[len(contracts) // 2]
    candles = client.minute_candles(middle.key, served[-1], served[-1])
    return (f"works - {len(served)} NIFTY expiries served, {served[0]} -> {served[-1]}; "
            f"{len(candles)} minutes for one contract on {served[-1]}")


def check_dhan() -> str:
    provider = index_minutes.create_provider()
    end = datetime.now(UTC)
    frame = provider.fetch_minutes("NIFTY", end - timedelta(days=5), end)
    if frame.empty:
        return "token accepted, but no NIFTY minutes in the last 5 days"
    first = frame.index.min().tz_convert(IST)
    return f"works - {len(frame)} NIFTY minutes; the earliest reads {first:%Y-%m-%d %H:%M} IST"


def check_r2() -> str:
    config = r2.R2Config.from_env()
    s3 = r2.connect(config)
    held = len(r2.remote_objects(s3, config.bucket))
    key = r2.KEY_PREFIX + "_setup_check.txt"
    s3.put_object(Bucket=config.bucket, Key=key, Body=b"ok")
    s3.delete_object(Bucket=config.bucket, Key=key)
    return f"works - bucket {config.bucket} holds {held} F&O file(s); write and delete both allowed"


def main() -> int:
    use_utf8_stdout()
    failed = 0
    for name, check in (("Upstox", check_upstox), ("Dhan", check_dhan), ("Cloudflare R2", check_r2)):
        try:
            print(f"{name}: {check()}")
        except Exception as exc:
            failed += 1
            # Exception TEXT only: every error here is written to name a fix, not a value.
            print(f"{name}: FAILS - {type(exc).__name__}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 1:** write the three scripts; **Step 2:** `--help` on each runs; `fno_check_setup.py` with no keys prints three clear FAILS naming the missing settings (and no values). **Step 3: Commit** — `feat(fno): scripts to fill minutes, copy the store to R2, and check the keys`

---

### Task 7: The GitHub jobs

**Files:** Create `.github/workflows/fno-daily.yml`, `.github/workflows/fno-backfill.yml`

`.github/workflows/fno-daily.yml`:

```yaml
# The F&O store's weekday top-up, on GitHub's machines, with the laptop off.
#
# Triggers are `schedule` and `workflow_dispatch` ONLY. Never add
# pull_request or pull_request_target: this repository is public, and those
# would let an outside contribution run with the secrets.
#
# Every weekday evening: fetch what the run reads from R2, add the day's NSE
# file / participant OI / India VIX, every expired option expiry not yet
# stored (Upstox serves about six months - an expiry not saved in time is
# gone), the index minutes, then publish back to R2. Design 2026-10-02 sec 2.6.
name: fno-daily

on:
  schedule:
    # 13:37 UTC = 19:07 IST, after NSE publishes. Off the hour on purpose:
    # GitHub's scheduler delays or drops the busy minutes (see research.yml).
    - cron: "37 13 * * 1-5"
  workflow_dispatch:

concurrency:
  group: fno-data           # shared with fno-backfill: one writer at a time
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  day:
    runs-on: ubuntu-latest
    timeout-minutes: 120
    env:
      FNO_ROOT: data/fno
      UPSTOX_ANALYTICS_TOKEN: ${{ secrets.UPSTOX_ANALYTICS_TOKEN }}
      DHAN_CLIENT_ID: ${{ secrets.DHAN_CLIENT_ID }}
      DHAN_PIN: ${{ secrets.DHAN_PIN }}
      DHAN_TOTP_SECRET: ${{ secrets.DHAN_TOTP_SECRET }}
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
      R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
      R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
      R2_BUCKET: ${{ secrets.R2_BUCKET }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - run: pip install -r requirements.txt

      - name: Fetch what today's run reads
        run: python scripts/fno_r2.py pull --daily-job

      - name: NSE files, participant OI, India VIX (the last two weeks, missing days only)
        run: python scripts/fno_daily.py --from "$(date -u -d '14 days ago' +%F)"

      - name: Minute data - expired options and the indexes
        run: python scripts/fno_minute.py

      # Publish whatever the run got, even if a later step failed: a day's NSE
      # file is worth keeping when Upstox was down.
      - name: Publish to R2
        if: always()
        run: python scripts/fno_r2.py push
```

`.github/workflows/fno-backfill.yml`:

```yaml
# The minute-data backfill, started by hand (Actions -> fno-backfill -> Run
# workflow). Resumable: run it again after a stop and it carries on.
#
# Triggers are workflow_dispatch ONLY (see fno-daily.yml).
name: fno-backfill

on:
  workflow_dispatch:
    inputs:
      what:
        description: "options, indexes or both"
        default: "both"

concurrency:
  group: fno-data
  cancel-in-progress: false

permissions:
  contents: read

jobs:
  fill:
    runs-on: ubuntu-latest
    timeout-minutes: 330
    env:
      FNO_ROOT: data/fno
      UPSTOX_ANALYTICS_TOKEN: ${{ secrets.UPSTOX_ANALYTICS_TOKEN }}
      DHAN_CLIENT_ID: ${{ secrets.DHAN_CLIENT_ID }}
      DHAN_PIN: ${{ secrets.DHAN_PIN }}
      DHAN_TOTP_SECRET: ${{ secrets.DHAN_TOTP_SECRET }}
      SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
      SUPABASE_SERVICE_ROLE_KEY: ${{ secrets.SUPABASE_SERVICE_ROLE_KEY }}
      R2_ACCOUNT_ID: ${{ secrets.R2_ACCOUNT_ID }}
      R2_ACCESS_KEY_ID: ${{ secrets.R2_ACCESS_KEY_ID }}
      R2_SECRET_ACCESS_KEY: ${{ secrets.R2_SECRET_ACCESS_KEY }}
      R2_BUCKET: ${{ secrets.R2_BUCKET }}

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - run: pip install -r requirements.txt

      - name: Fetch the store
        run: python scripts/fno_r2.py pull

      - name: Fill
        run: |
          case "${{ github.event.inputs.what }}" in
            options) python scripts/fno_minute.py --options-only ;;
            indexes) python scripts/fno_minute.py --indexes-only ;;
            *)       python scripts/fno_minute.py ;;
          esac

      - name: Publish to R2
        if: always()
        run: python scripts/fno_r2.py push
```

- [ ] **Step 1:** write both; **Step 2:** parse them (`python -c "import yaml,sys; [yaml.safe_load(open(p)) for p in sys.argv[1:]]" .github/workflows/fno-*.yml`). **Step 3: Commit** — `ci(fno): the weekday F&O top-up and the manual minute backfill`

---

### Task 8: Docs, full suite

- [ ] `docs/DATA.md` section 9: a "Minute data" paragraph — layout (`minute/<U>/<EXPIRY>.parquet`, `minute/_expiries.csv`, `index_minute/<NAME>/<YEAR>.parquet`), the band and window, the six-month question, R2 layout and the two jobs.
- [ ] `docs/FNO_SETUP.md`: section 5 says what "setup done" starts — the check script, the first R2 publish, the first backfill; and a section 6 for switching the GitHub jobs on (they run only from the main branch).
- [ ] Full suite passes; commit `docs(fno): the minute store, R2 and the jobs`.
