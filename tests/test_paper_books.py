"""Paper books (research/paper_books.py): replayed forward from a fixed start, nothing before it counts."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from costs import HoldingCostModel  # noqa: E402
from research import paper_books as pb  # noqa: E402
from research.checker import check_proposal  # noqa: E402
from research.rotation import Variant  # noqa: E402
from research.run_day import DRY_RUN_YAML  # noqa: E402


def ohlcv(days=700, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=days).date
    parts = []
    for i, t in enumerate(["AAA.NS", "BBB.NS", "CCC.NS", "^NSEI"]):
        close = 100 * np.exp(np.cumsum(0.0006 * (i + 1) + 0.02 * rng.standard_normal(days)))
        f = pd.DataFrame({"Open": close * 0.999, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                          "Volume": 1e6}, index=idx)
        f.columns = pd.MultiIndex.from_product([[t], list(f.columns)])
        parts.append(f)
    return pd.concat(parts, axis=1)


@pytest.fixture
def ema_rules(tmp_path, monkeypatch):
    doc = check_proposal(DRY_RUN_YAML, idea="ema", day=date(2026, 1, 1), version=1, timeframes=("day",)).document
    (tmp_path / "ema.json").write_text(json.dumps({"name": doc["name"], "document": doc}), encoding="utf-8")
    monkeypatch.setattr(pb, "RULES", tmp_path)
    return pb.Book("t-stock", "stock", "test stock", pb.SLOTS * pb.NOTIONAL, "t", rules="ema.json")


def test_frames_are_stamped_at_ist_midnight_in_lower_case():
    f = pb.frames(ohlcv())
    assert set(f) == {"AAA", "BBB", "CCC"}
    first = f["AAA"].index[0]
    assert str(first.tz) == "UTC" and (first.hour, first.minute) == (18, 30)
    assert list(f["AAA"].columns) == ["open", "high", "low", "close", "volume"]


def test_a_stock_book_counts_only_trades_entered_from_the_start(ema_rules):
    data = ohlcv()
    start = data.index[500]
    b = pb.stock_book(ema_rules, data, start=start, slippage_pct=0.05, cost_model=HoldingCostModel())
    assert b["state"] == "running" and b["complete_through"] == data.index[-1].isoformat()
    for t in b["closed"] + b["open"]:
        assert date.fromisoformat(t["entered"]) >= start
    assert b["equity"][0][0] == start.isoformat() and len(b["equity"]) == len(data.index[500:])
    assert len(b["open"]) <= pb.SLOTS


def test_before_the_start_every_book_says_not_started(ema_rules):
    data = ohlcv()
    late = date(2030, 1, 1)
    assert pb.stock_book(ema_rules, data, start=late, slippage_pct=0.05, cost_model=HoldingCostModel())["state"] == "not started"
    closes = data.xs("Close", axis=1, level=1)
    rot = pb.Book("t-rot", "rotation", "t", 500_000.0, "t", variant=Variant("t", 6, 0, 2, False))
    assert pb.rotation_book(rot, closes, start=late)["state"] == "not started"


def test_a_rotation_book_holds_the_variant_from_its_first_month_end():
    closes = ohlcv().xs("Close", axis=1, level=1)
    rot = pb.Book("t-rot", "rotation", "t", 500_000.0, "t", variant=Variant("t", 6, 0, 2, False))
    b = pb.rotation_book(rot, closes, start=closes.index[500])
    assert b["state"] == "running" and len(b["open"]) == 2
    assert b["nifty_hold"] == pytest.approx(500_000.0 * closes["^NSEI"].iloc[-1] / closes["^NSEI"].iloc[500])


def test_the_frozen_rules_are_in_the_repository():
    for book in pb.BOOKS:
        if book.rules:
            assert pb.rules(book)["document"], book.key


def test_an_index_book_holds_the_index_in_an_uptrend_and_waits_before_its_start():
    idx = pd.bdate_range("2024-01-01", periods=600).date
    tri = pd.DataFrame({"NIFTY 50": 100 * np.exp(0.001 * np.arange(600)),
                        "MID": 100 * np.exp(0.002 * np.arange(600))}, index=idx)
    book = pb.Book("t-idx", "index", "t", 500_000.0, "t", index="MID")
    b = pb.index_book(book, tri, start=idx[400])
    assert b["state"] == "running" and b["open"] == [{"holding": "MID"}]
    assert b["closed"][0]["action"] == "buy the index"
    assert pb.index_book(book, tri, start=date(2030, 1, 1))["state"] == "not started"
