"""The weekly paper-trading message (research/paper_weekly.py)."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research import paper_weekly as pw  # noqa: E402
from research.blocks import GREEN, RED, WHITE, as_html, as_text, pack  # noqa: E402


def book(values, *, trips=(), title="A book", kind="stock", bench=None, start="2026-10-21"):
    days = ["2026-10-21", "2026-10-31", "2026-11-14", "2026-11-30", "2026-12-15"][:len(values)]
    return {"key": "k", "kind": kind, "title": title, "start": start, "complete_through": days[-1],
            "capital": 100.0, "value": values[-1], "nifty_hold": bench if bench is not None else values[-1],
            "state": "running", "equity": [[d, v] for d, v in zip(days, values)], "round_trips": list(trips)}


def trip(pnl, closed="2026-11-10", **extra):
    return {"what": "AAA", "opened": "2026-10-22", "closed": closed, "return_pct": pnl, "pnl": pnl, **extra}


def test_the_average_month_is_the_mean_of_its_calendar_months():
    """The owner's own example: 3%, 5% and 1% read as 3%."""
    b = book([100.0, 103.0, 106.0, 108.15, 109.2315])
    months = pw.monthly_returns(b)
    assert [m for m, _ in months] == ["2026-10", "2026-11", "2026-12"]
    assert [round(r, 6) for _, r in months] == [3.0, 5.0, 1.0]
    assert round(pw.numbers(b)["avg_month_pct"], 6) == 3.0


def test_win_rate_profit_factor_and_reward_to_risk_come_from_closed_trades():
    trips = [trip(30), trip(10), trip(-10), trip(-10), trip(99, closed=None)]
    n = pw.numbers(book([100.0, 120.0], trips=trips))
    assert n["closed"] == 4 and n["win_rate_pct"] == 50.0
    assert n["profit_factor"] == 2.0 and n["reward_risk"] == 2.0
    assert len(n["open"]) == 1


def test_each_book_has_its_own_box_with_every_figure_asked_for():
    trips = [trip(5), trip(-2), trip(3, closed=None, bought_at=1398.9, last=1584.21)]
    blocks = pw.message([book([100.0, 102.0, 104.0], trips=trips, bench=110.0)], date(2026, 11, 14))
    text = as_text(blocks)
    for label in ("added on", "worth now", "since added", "avg a month", "NIFTY 50 same", "win rate",
                  "profit factor", "reward : risk", "trades a month", "worst fall", "holding now"):
        assert label in text, label
    assert "AAA            1,398.90 -> 1,584.21 +3.0%" in text
    assert f"{RED} A book" in text                       # behind NIFTY 50 so far
    assert as_html(blocks).count("<pre>") == len(blocks) - 1      # every block but the glossary is boxed


def test_ahead_of_its_benchmark_is_green_and_not_started_is_white():
    ahead = pw.book_block(book([100.0, 110.0], bench=105.0))
    waiting = pw.book_block({"title": "Later", "start": "2026-11-16", "state": "waiting for the first month-end"})
    assert ahead.heading.startswith(GREEN) and waiting.heading.startswith(WHITE)
    assert "waiting for the first month-end" in waiting.note


def test_no_closed_trade_says_so_instead_of_a_number():
    text = as_text([pw.book_block(book([100.0, 101.0]))])
    assert "win rate       no trade closed yet" in text


def test_rupees_use_indian_grouping():
    assert pw.rupees(1051918.16) == "Rs 10,51,918" and pw.rupees(500000) == "Rs 5,00,000"
    assert pw.rupees(999) == "Rs 999"


def test_it_is_due_on_friday_once_a_week():
    assert pw.due(date(2026, 11, 20), "") and not pw.due(date(2026, 11, 20), "2026-W47")
    assert not pw.due(date(2026, 11, 19), "")


def test_a_long_message_is_split_into_whole_blocks():
    many = [book([100.0, 101.0], title=f"Book {i}", trips=[trip(1, closed=None, bought_at=1.0, last=1.01)] * 20)
            for i in range(12)]
    groups = pack(pw.message(many, date(2026, 11, 14)))
    assert len(groups) > 1 and all(len(as_html(g)) <= 4096 for g in groups)
