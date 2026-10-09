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


def test_no_minutes_at_all_for_a_traded_contract_is_reported_not_crashed():
    empty = pd.DataFrame(columns=list(minute.COLUMNS))
    daily = nse_rows([(EXPIRY, 22000.0, "CE", 12.0, 500)])
    issues = minute.check_expiry("NIFTY", EXPIRY, empty, daily, [EXPIRY], {(22000.0, "CE")})
    assert [(i["check"], i["count"]) for i in issues] == [("minute_day_missing", 1)]
