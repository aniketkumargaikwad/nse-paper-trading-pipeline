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


def book_at(values, *, kind="rotation", closed=0, start="2026-10-21", through="2026-12-01", bench=None):
    return {"state": "running", "kind": kind, "start": start, "complete_through": through, "capital": 100.0,
            "value": values[-1], "nifty_hold": bench if bench is not None else 100.0,
            "equity": [["d", v] for v in values], "closed": [{}] * closed}


@pytest.mark.parametrize("values,today,kind,closed,bench,verdict", [
    ([100, 110, 75], date(2026, 12, 1), "rotation", 0, 100, "STOP"),               # fell 31.8% from 110
    ([100, 104], date(2026, 12, 1), "rotation", 0, 100, "too early"),
    ([100, 90], date(2027, 4, 21), "rotation", 0, 100, "DROP"),                   # 10 points behind
    ([100, 104], date(2027, 4, 21), "rotation", 0, 100, "on track"),
    ([100, 104], date(2027, 4, 21), "stock", 12, 100, "waiting"),                 # too few trades
    ([100, 112], date(2027, 10, 21), "stock", 40, 105, "PASSED"),
    ([100, 103], date(2027, 10, 21), "rotation", 0, 105, "FAILED"),
])
def test_the_review_rule_fixed_in_advance(values, today, kind, closed, bench, verdict):
    card = pb.scorecard(book_at(values, kind=kind, closed=closed, bench=bench), today=today)
    assert card["verdict"] == verdict, card


def test_a_book_that_has_not_started_has_no_verdict_yet():
    assert pb.scorecard({"state": "not started", "equity": []})["verdict"] == "not started"


def test_the_monthly_message_has_a_heading_and_a_short_table_per_book():
    from research.blocks import as_html, as_text
    running = {**book_at([100, 104], closed=3, kind="stock"), "title": "A book", "complete_through": "2026-12-01"}
    blocks = pb.message([running, {"title": "Later", "state": "not started"}], date(2026, 12, 1))
    text = as_text(blocks)
    assert "A book\npaper account  +4.0%" in text and "review         TOO EARLY" in text
    assert "Later\nNot started." in text
    html_text = as_html(blocks)
    assert "<b>A book</b>\n<pre>" in html_text


def test_a_book_added_later_starts_on_its_own_day():
    added = next(b for b in pb.BOOKS if b.key == "rotation-12-own-trend")
    assert added.start == date(2026, 10, 23) and pb.START == date(2026, 10, 21)
    closes = ohlcv().xs("Close", axis=1, level=1)
    late = pb.Book("t-rot", "rotation", "t", 500_000.0, "t", variant=Variant("t", 6, 0, 2, False),
                   start=closes.index[500])
    assert pb.rotation_book(late, closes)["start"] == closes.index[500].isoformat()


def test_the_monthly_summary_is_sent_once_per_month_and_only_marked_when_delivered(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "paper_books_script", Path(__file__).resolve().parent.parent / "scripts" / "paper_books.py")
    spb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spb)
    calls = []
    deliver = {"ok": False}

    def fake_send(subject, blocks, **k):
        calls.append(subject)
        return deliver["ok"]

    import research.blocks
    monkeypatch.setattr(research.blocks, "send", fake_send)
    monkeypatch.setattr(pb, "OUT", tmp_path)
    monkeypatch.setattr(pb, "PROMOTED", tmp_path / "no-promoted.json")
    data = ohlcv()
    monkeypatch.setattr(spb, "_load", lambda args: data)
    monkeypatch.setattr(pb, "BOOKS", (pb.Book("t-rot", "rotation", "t", 500_000.0, "t",
                                              variant=Variant("t", 6, 0, 2, False), start=data.index[500]),))
    spb.main(["--skip-download", "--notify"])            # Telegram fails: nothing remembered
    assert len(calls) == 1 and not (tmp_path / spb.MONTHLY_MARK).exists()
    deliver["ok"] = True
    spb.main(["--skip-download", "--notify"])            # delivered: remembered
    spb.main(["--skip-download", "--notify"])            # same month: not sent again
    assert len(calls) == 2 and (tmp_path / spb.MONTHLY_MARK).read_text().strip() == f"{data.index[-1]:%Y-%m}"



def tri_frame(days=700):
    idx = pd.bdate_range("2024-01-01", periods=days).date
    n = np.arange(days)
    return pd.DataFrame({"NIFTY 50": 100 * np.exp(0.0005 * n), "NIFTY200 MOMENTUM 30": 100 * np.exp(0.001 * n),
                         "NIFTY IT": 100 * np.exp(-0.001 * n)}, index=idx)


def test_an_allocation_book_replays_its_plan_from_its_own_start():
    from research.alloc import Plan
    tri = tri_frame()
    plan = Plan("p", ("NIFTY200 MOMENTUM 30", "NIFTY IT"), 2, rank_by="none", own_average=100)
    book = pb.Book("alloc-t", "alloc", "t", 500_000.0, "t", plan=plan, start=tri.index[500])
    b = pb.alloc_book(book, tri)
    assert b["state"] == "running" and b["start"] == tri.index[500].isoformat()
    # NIFTY IT is below its own average, so its half of the account waits in cash.
    assert b["open"] == [{"holding": "NIFTY200 MOMENTUM 30", "share": "50%"},
                         {"holding": "cash (6% a year)", "share": "50%"}]
    assert b["closed"][0]["filled"] == tri.index[500].isoformat()          # in position from its first close
    assert b["equity"][0][0] == tri.index[500].isoformat() and b["value"] > 500_000.0
    assert b["nifty_hold"] == pytest.approx(500_000.0 * tri["NIFTY 50"].iloc[-1] / tri["NIFTY 50"].iloc[500])
    assert pb.alloc_book(book, tri, start=date(2030, 1, 1))["state"] == "not started"
    assert pb.scorecard(b)["verdict"] == "too early"


def test_a_book_added_later_is_reviewed_six_and_twelve_months_from_its_own_start():
    assert pb.review_dates(date(2026, 10, 21)) == (pb.REVIEW_6M, pb.REVIEW_12M)
    assert pb.review_dates(date(2026, 10, 23)) == (pb.REVIEW_6M, pb.REVIEW_12M)     # the October books: unchanged
    assert pb.review_dates(date(2026, 11, 18)) == (date(2027, 5, 19), date(2027, 11, 18))
    late = book_at([100, 90], start="2026-11-18")
    assert pb.scorecard(late, today=date(2027, 4, 21))["verdict"] == "too early"   # not yet its six months
    assert pb.scorecard(late, today=date(2027, 5, 19))["verdict"] == "DROP"
    assert pb.scorecard(book_at([100, 112], start="2026-11-18", bench=105), today=date(2027, 11, 18))["verdict"] == "PASSED"


def test_the_books_include_every_plan_the_research_promoted(tmp_path, monkeypatch):
    doc = {"version": "alloc/1", "universe": ["NIFTY200 MOMENTUM 30"], "rank": {"by": "none"},
           "market_filter": {"index": "NIFTY IT", "average": 200}}
    rows = [{"key": "alloc-20261117-1", "title": "Plan A", "day": "2026-11-17", "start": "2026-11-18",
             "document": doc, "training": "words"},
            {"key": "alloc-bad", "title": "Gone", "day": "2026-11-17", "start": "2026-11-18",
             "document": {**doc, "universe": ["NIFTY NO SUCH INDEX"]}, "training": "words"}]
    path = tmp_path / "promoted.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(pb, "PROMOTED", path)
    books = pb.all_books()
    assert books[:len(pb.BOOKS)] == pb.BOOKS
    extra = books[len(pb.BOOKS):]
    assert [(b.key, b.kind, b.title, b.start) for b in extra] == [
        ("alloc-20261117-1", "alloc", "Plan A (AI, 2026-11-17)", date(2026, 11, 18))]   # the unreadable one is skipped
    assert {"NIFTY 50", "NIFTY200 MOMENTUM 30", "NIFTY IT"} <= set(pb.indexes_needed(extra))


def test_the_job_updates_a_promoted_book_from_nse_levels(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "paper_books_script2", Path(__file__).resolve().parent.parent / "scripts" / "paper_books.py")
    spb = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(spb)
    tri = tri_frame()
    doc = {"version": "alloc/1", "universe": ["NIFTY200 MOMENTUM 30"], "rank": {"by": "none"}}
    path = tmp_path / "promoted.json"
    path.write_text(json.dumps([{"key": "alloc-x", "title": "Plan X", "day": "2025-11-01",
                                 "start": tri.index[500].isoformat(), "document": doc, "training": "w"}]),
                    encoding="utf-8")
    asked = {}

    def fake_fetch(indexes, first_year, log=print):
        asked["indexes"], asked["first_year"] = indexes, first_year
        return tri

    import nse_index_history
    monkeypatch.setattr(nse_index_history, "fetch_whole", fake_fetch)
    monkeypatch.setattr(pb, "PROMOTED", path)
    monkeypatch.setattr(pb, "BOOKS", ())
    monkeypatch.setattr(pb, "OUT", tmp_path / "out")
    monkeypatch.setattr(spb, "_load", lambda args: ohlcv())
    assert spb.main(["--skip-download"]) == 0
    assert asked == {"indexes": ["NIFTY 50", "NIFTY200 MOMENTUM 30"], "first_year": pb.START.year - 2}
    saved = json.loads((tmp_path / "out" / "alloc-x.json").read_text(encoding="utf-8"))
    assert saved["kind"] == "alloc" and saved["state"] == "running" and saved["open"][0]["share"] == "100%"
