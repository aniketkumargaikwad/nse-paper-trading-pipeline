"""What the morning message says, for every shape a run can take."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.message import (  # noqa: E402
    TELEGRAM_LIMIT,
    first_sentence,
    rupees,
    telegram_text,
)


def a_run(**overrides):
    run = {
        "started_at": "2026-09-13T00:30:00+00:00",
        "status": "completed",
        "data_end": date(2026, 7, 31),
        "locked_from": date(2025, 8, 1),
        "final_strategy_name": "R-20260913-ema20-reclaim-v1",
        "versions_tried": 3,
        "ideas_dropped": 2,
        "pick_symbol": "NSE:VMM",
        "pick_timeframe": "25m",
        "lakh_end_value": 78845.67,
        "hold_end_value": 75707.46,
        "locked_trades": 48,
        "win_rate_pct": 19.0,
        "worst_dip_pct": 21.1,
        "verdict_passed": False,
        "beat_holding": True,
        "ai_review": "Judge every result against hold_return_pct. Gross was negative.",
    }
    run.update(overrides)
    return run


def test_rupees_are_grouped_the_way_they_are_read_here():
    assert rupees(100000) == "₹1,00,000"
    assert rupees(78845.67) == "₹78,846"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(None) == "n/a"


def test_the_message_leads_with_the_two_numbers_that_matter():
    text = telegram_text(a_run(), title="EMA20 reclaim in an uptrend")
    assert "EMA20 reclaim in an uptrend" in text
    assert "₹78,846" in text and "₹75,707" in text
    assert "NSE:VMM" in text and "25m" in text


def test_the_verdict_says_both_halves():
    text = telegram_text(a_run(), title="t")
    assert "Failed" in text and "Beat holding" in text


def test_a_run_that_beat_nothing_says_so():
    text = telegram_text(a_run(verdict_passed=True, beat_holding=False), title="t")
    assert "Passed" in text and "Did not beat holding" in text


def test_a_day_with_no_pick_is_a_result_not_a_gap():
    text = telegram_text(
        a_run(pick_symbol=None, pick_timeframe=None, lakh_end_value=None,
              hold_end_value=None, locked_trades=None),
        title="t",
    )
    assert "No qualifying pick" in text
    assert "Locked year:" not in text


def test_a_day_cut_short_says_why_at_the_top():
    text = telegram_text(a_run(status="stopped_limit"), title="t")
    assert "cut short" in text.lower()


def test_the_dashboard_link_is_included_when_there_is_one():
    assert "https://example.test/x" in telegram_text(
        a_run(), title="t", dashboard_url="https://example.test/x")
    assert "Details" not in telegram_text(a_run(), title="t")


def test_the_whole_message_is_capped_at_telegrams_limit():
    """The review is capped long before this, so the backstop is the title."""
    text = telegram_text(a_run(), title="x" * 8000)
    assert len(text) == TELEGRAM_LIMIT
    assert text.endswith("…")


def test_only_the_headline_of_a_review_reaches_the_phone():
    """The full review is a page of numbered lessons; the dashboard has it."""
    review = ("1) Judge every result against hold_return_pct. 2) Average hold "
              "return of ~418% means these are bull-market years. 3) Check gross first.")
    text = telegram_text(a_run(ai_review=review), title="t")
    assert "Why: 1) Judge every result against hold_return_pct." in text
    assert "418%" not in text


def test_a_review_with_no_sentence_end_is_cut_with_an_ellipsis():
    assert first_sentence("x" * 400).endswith("…")
    assert len(first_sentence("x" * 400)) <= 300


def test_a_short_review_is_left_alone():
    assert first_sentence("Nothing worked today") == "Nothing worked today"
