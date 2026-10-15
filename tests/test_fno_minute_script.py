"""scripts/fno_minute.py wired end to end against a fake Upstox: ledger, closes, store."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fno_minute  # noqa: E402
from fno import minute, spot, store  # noqa: E402
from test_fno_minute import FakeUpstox, candles, contract, EXPIRY  # noqa: E402


def test_options_fetches_every_due_expiry_and_records_it(tmp_path, monkeypatch, capsys):
    sessions = [EXPIRY - timedelta(days=i) for i in range(20)]
    for d in sessions:
        store.record_session(tmp_path, d, "ok", 1)
    spot.write(tmp_path, "NIFTY", pd.DataFrame({"trade_date": sessions, "open": 22000.0, "high": 22000.0,
                                                "low": 22000.0, "close": 22000.0}))
    near = contract(22000)

    class Client(FakeUpstox):
        def expiries(self, underlying):
            return [EXPIRY, EXPIRY + timedelta(days=7)]          # the second has not expired yet

    fake = Client([near, contract(40000)], {near.key: candles(EXPIRY, [5.0, 6.0])})
    monkeypatch.setattr(fno_minute.upstox, "token_from_env", lambda: "t")
    monkeypatch.setattr(fno_minute.upstox, "UpstoxClient", lambda token: fake)
    monkeypatch.setattr(fno_minute.spot, "refresh", lambda root, u: None)

    fno_minute.options(tmp_path, ["NIFTY"], EXPIRY + timedelta(days=3))
    assert minute.load_ledger(tmp_path) == {("NIFTY", EXPIRY): "ok"}
    assert len(minute.read_expiry(tmp_path, "NIFTY", EXPIRY)) == 2
    out = capsys.readouterr().out
    assert "Upstox serves 2 expiries" in out and "1 to fetch" in out

    fake.asked.clear()
    fno_minute.options(tmp_path, ["NIFTY"], EXPIRY + timedelta(days=3))
    assert fake.asked == []                                       # nothing asked twice


def test_a_time_budget_stops_between_expiries_and_leaves_the_rest_for_next_time(tmp_path, monkeypatch, capsys):
    """The daily GitHub job has a 2-hour limit; killed mid-run, it would never reach the
    step that publishes to R2. So it stops cleanly between expiries instead."""
    sessions = [EXPIRY - timedelta(days=i) for i in range(30)]
    for d in sessions:
        store.record_session(tmp_path, d, "ok", 1)
    spot.write(tmp_path, "NIFTY", pd.DataFrame({"trade_date": sessions, "open": 22000.0, "high": 22000.0,
                                                "low": 22000.0, "close": 22000.0}))

    class Client(FakeUpstox):
        def expiries(self, underlying):
            return [EXPIRY - timedelta(days=14), EXPIRY - timedelta(days=7), EXPIRY]

    fake = Client([contract(22000)], {})
    clock = iter([0.0, 0.0, 100.0, 200.0, 300.0])               # seconds, one reading per check
    monkeypatch.setattr(fno_minute.upstox, "token_from_env", lambda: "t")
    monkeypatch.setattr(fno_minute.upstox, "UpstoxClient", lambda token: fake)
    monkeypatch.setattr(fno_minute.spot, "refresh", lambda root, u: None)
    monkeypatch.setattr(fno_minute.time, "monotonic", lambda: next(clock))

    fno_minute.options(tmp_path, ["NIFTY"], EXPIRY + timedelta(days=3), budget_seconds=150)
    assert len(minute.load_ledger(tmp_path)) == 2
    assert "time budget" in capsys.readouterr().out
