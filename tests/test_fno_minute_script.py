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
