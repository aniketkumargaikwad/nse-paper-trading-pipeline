"""Upstox: expired NIFTY / BANKNIFTY option contracts and their minute candles. READ-ONLY.

Three endpoints, all GET, all on Upstox Plus (design §2.1):

    /v2/expired-instruments/expiries                      the expiries Upstox still serves
    /v2/expired-instruments/option/contract               an expiry's contracts
    /v2/expired-instruments/historical-candle/{key}/1minute/{to}/{from}

and one for the indexes themselves (NIFTY, Nifty Bank, India VIX), which the
same token reads - verified 19 Oct 2026 for days in 2022 and 2026:

    /v3/historical-candle/{key}/minutes/1/{to}/{from}

There is no order code here and none may be added. The token is the owner's
one-year analytics token (docs/FNO_SETUP.md 1b) - which, measured 14 Oct 2026,
Upstox DOES accept for expired contracts, though its documents never say so.
Messages name the fix, never the token.

How far back: Upstox's page says "up to six months of historical expiries";
on 14 Oct 2026 it served 107 NIFTY expiries back to 3 Oct 2024 - two years.
The minutes are genuine: each day's first candle opens at NSE's official open
and the minutes' volume adds up to NSE's daily volume exactly. From 3 Aug 2026
they run 09:15-15:39, because NSE moved the F&O close to 15:40 (circular
NSE/FAOP/74467); before that 09:15-15:29.

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
BASE_V3 = "https://api.upstox.com/v3"
UNDERLYING_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}
INDEX_KEYS = {**UNDERLYING_KEYS, "INDIAVIX": "NSE_INDEX|India VIX"}
CANDLE_COLUMNS = ("ts", "open", "high", "low", "close", "volume", "oi")
MAX_DAYS_PER_REQUEST = 30
# Upstox's limits: 50 a second, 500 a minute, 2,000 per 30 minutes. The last is
# the one that binds - just over one a second - and the first fill (14 Oct 2026)
# hit it at four a second. So: one a second, and a refusal is waited out in
# minutes, since Upstox says only that access "might be temporarily suspended".
PAUSE_SECONDS = 1.0
RATE_LIMIT_WAITS = (60, 180, 600, 1200)      # seconds after each HTTP 429: 34 min, past one window
ATTEMPTS = len(RATE_LIMIT_WAITS) + 1
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
    if not rows:
        return pd.DataFrame(columns=list(CANDLE_COLUMNS))
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

    def _get(self, path: str, params: dict[str, str] | None, doing: str, *, base: str = BASE) -> Any:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        last = ""
        limited = 0
        for attempt in range(1, ATTEMPTS + 1):
            if attempt == 1:
                self._sleep(PAUSE_SECONDS)
            elif last == "HTTP 429":
                self._sleep(RATE_LIMIT_WAITS[limited - 1])
            else:
                self._sleep(2 ** attempt)
            try:
                response = self._http.get(base + path, headers=headers, params=params,
                                          timeout=TIMEOUT_SECONDS)
            except requests.exceptions.RequestException as exc:
                last = type(exc).__name__          # never the exception: it carries the request
                continue
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            errors = payload.get("errors") or []
            codes = {e.get("errorCode") for e in errors if isinstance(e, dict)}
            if PLUS_REQUIRED in codes:
                raise UpstoxPlusRequired(f"Upstox says {doing} needs an Upstox Plus subscription "
                                         f"({PLUS_REQUIRED}): activate Plus (docs/FNO_SETUP.md 1a)")
            status = response.status_code
            if status == 401:
                raise UpstoxAuthError(
                    f"Upstox refused the token (HTTP 401) while {doing}: it has expired or been "
                    f"revoked - make a new one and replace {TOKEN_ENV} (docs/FNO_SETUP.md 1b).")
            if status == 429 or status >= 500:
                last = f"HTTP {status}"
                limited += status == 429
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

    def index_minutes(self, name: str, start: date, end: date) -> pd.DataFrame:
        """1-minute candles of NIFTY, BANKNIFTY or INDIAVIX, start..end inclusive, oldest first."""
        key = INDEX_KEYS[name]
        frames = []
        day = start
        while day <= end:
            last = min(day + timedelta(days=MAX_DAYS_PER_REQUEST - 1), end)
            path = f"/historical-candle/{quote(key, safe='')}/minutes/1/{last.isoformat()}/{day.isoformat()}"
            data = self._get(path, None, f"fetching {name} minutes", base=BASE_V3) or {}
            frames.append(_candles(data.get("candles") or []))
            day = last + timedelta(days=1)
        frames = [f for f in frames if not f.empty]
        if not frames:
            return pd.DataFrame(columns=list(CANDLE_COLUMNS))
        out = pd.concat(frames).drop_duplicates("ts").sort_values("ts")
        return out.reset_index(drop=True)
