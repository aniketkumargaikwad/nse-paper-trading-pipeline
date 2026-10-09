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
