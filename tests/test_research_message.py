"""What the morning message says, for every shape a run can take."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.message import (  # noqa: E402
    TELEGRAM_LIMIT,
    first_sentence,
    plain_text,
    rupees,
    summary_rows,
    telegram_html,
    version_lines,
)


def some_versions():
    return [
        {"idea_no": 1, "version_no": 1, "valid": True, "decision": "new_idea",
         "training_summary": {"combos_tested": 1164, "combos_profitable": 9,
                              "combos_beating_hold": 0}},
        {"idea_no": 2, "version_no": 1, "valid": True, "decision": "new_idea",
         "training_summary": {"combos_tested": 1164, "combos_profitable": 213,
                              "combos_beating_hold": 0}},
        {"idea_no": 3, "version_no": 1, "valid": True, "decision": "stop",
         "training_summary": {"combos_tested": 1177, "combos_profitable": 53,
                              "combos_beating_hold": 0}},
    ]


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


def labelled(run):
    return dict(summary_rows(run))


def test_rupees_are_grouped_the_way_they_are_read_here():
    assert rupees(100000) == "₹1,00,000"
    assert rupees(78845.67) == "₹78,846"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(None) == "n/a"


# --- the table ---------------------------------------------------------------


def test_the_table_carries_the_numbers_that_matter():
    rows = labelled(a_run())
    assert rows["Pick"] == "NSE:VMM · 25m"
    assert rows["₹1 lakh"] == "became ₹78,846"
    assert rows["Holding"] == "became ₹75,707"
    assert rows["Trades"] == "48 · won 19%"
    assert rows["Worst dip"] == "21.1%"
    assert rows["Versions"] == "3 tried, 2 dropped"


def test_the_result_line_does_the_subtraction_for_you():
    """Reading two balances and subtracting them is the reader's job otherwise."""
    assert labelled(a_run())["Result"] == "-₹21,154 (-21.2%)"
    assert labelled(a_run(lakh_end_value=143000))["Result"] == "+₹43,000 (+43.0%)"


def test_the_verdict_says_both_halves():
    assert labelled(a_run())["Verdict"] == "FAILED · beat holding"
    assert labelled(a_run(verdict_passed=True, beat_holding=False))["Verdict"] == (
        "PASSED · did not beat holding")


def test_a_day_with_no_pick_is_a_result_not_a_gap():
    rows = labelled(a_run(pick_symbol=None, lakh_end_value=None, hold_end_value=None))
    assert rows["Pick"] == "none qualified"
    assert "₹1 lakh" not in rows
    assert rows["Verdict"] == "locked year not opened"


def test_the_labels_line_up_in_a_column():
    table = plain_text(a_run(), title="t")
    columns = {line.index("became") for line in table.splitlines() if "became" in line}
    assert len(columns) == 1       # every value starts at the same offset


# --- Telegram's HTML mode ----------------------------------------------------


def test_a_title_with_an_angle_bracket_cannot_break_the_markup():
    """EMA20>EMA50 would otherwise make Telegram reject the whole message."""
    body = telegram_html(a_run(), title="EMA20>EMA50 uptrend & pullback")
    assert "EMA20&gt;EMA50 uptrend &amp; pullback" in body
    assert "EMA20>EMA50" not in body


def test_the_numbers_sit_in_a_preformatted_block():
    body = telegram_html(a_run(), title="t")
    assert body.count("<pre>") == 1 and body.count("</pre>") == 1
    assert body.index("<pre>") < body.index("NSE:VMM") < body.index("</pre>")


def test_the_dashboard_link_is_included_when_there_is_one():
    assert "https://example.test/x" in telegram_html(
        a_run(), title="t", dashboard_url="https://example.test/x")
    assert "Details" not in telegram_html(a_run(), title="t")


def test_a_day_cut_short_says_why():
    assert "cut short" in telegram_html(a_run(status="stopped_limit"), title="t").lower()


def test_only_the_headline_of_a_review_reaches_the_phone():
    review = ("1) Judge every result against hold_return_pct. 2) Average hold "
              "return of ~418% means these are bull-market years. 3) Check gross first.")
    body = plain_text(a_run(ai_review=review), title="t")
    assert "Why: 1) Judge every result against hold_return_pct." in body
    assert "418%" not in body


def test_both_renderings_are_capped_at_telegrams_limit():
    long_title = "x" * 8000
    assert len(telegram_html(a_run(), title=long_title)) <= TELEGRAM_LIMIT
    assert len(plain_text(a_run(), title=long_title)) <= TELEGRAM_LIMIT


def test_a_review_with_no_sentence_end_is_cut_with_an_ellipsis():
    assert first_sentence("x" * 400).endswith("…")
    assert len(first_sentence("x" * 400)) <= 300


def test_a_short_review_is_left_alone():
    assert first_sentence("Nothing worked today") == "Nothing worked today"


# --- what the other versions did ---------------------------------------------


def test_a_day_that_tried_three_versions_lists_all_three():
    lines = version_lines(some_versions())
    assert len(lines) == 3
    assert lines[0].startswith("v1.1 new_idea")
    assert "9/1164 profitable, 0 beat hold" in lines[0]
    assert lines[2].startswith("v3.1 stop")


def test_a_single_version_day_lists_nothing():
    """The table above already said it; a one-row list is noise."""
    assert version_lines(some_versions()[:1]) == []
    assert version_lines([]) == []


def test_a_rejected_version_says_so_rather_than_showing_zeros():
    lines = version_lines([
        {"idea_no": 1, "version_no": 1, "valid": True, "decision": "next_version",
         "training_summary": {"combos_tested": 10, "combos_profitable": 1}},
        {"idea_no": 1, "version_no": 2, "valid": False, "decision": None,
         "training_summary": None},
    ])
    assert "rejected by the checker" in lines[1]


def test_the_versions_reach_both_renderings():
    body = plain_text(a_run(), title="t", versions=some_versions())
    assert "Versions tried" in body and "v2.1" in body
    html_body = telegram_html(a_run(), title="t", versions=some_versions())
    assert html_body.count("<pre>") == 2
    assert "v2.1" in html_body


def test_a_single_version_day_adds_no_block():
    assert "Versions tried" not in plain_text(
        a_run(), title="t", versions=some_versions()[:1])
