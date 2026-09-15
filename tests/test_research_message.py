"""What the morning message says, for every shape a run can take.

These tests are mostly about readability rather than arithmetic: a number
without its period, a label that runs into its value, or a version row with no
verdict are all things that only show up on a phone at 9am.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.message import (  # noqa: E402
    LABEL_WIDTH,
    TELEGRAM_LIMIT,
    edge_verdict,
    first_sentence,
    idea_shape,
    locked_months,
    plain_text,
    rupees,
    summary_rows,
    telegram_html,
    version_table,
)


def a_run(**overrides):
    """The 14 September 2026 run, which is the shape these were written for."""
    run = {
        "started_at": "2026-09-14T09:10:41+00:00",
        "status": "completed",
        "data_end": date(2026, 7, 31),
        "locked_from": date(2025, 8, 1),
        "locked_to": date(2026, 7, 31),
        "final_strategy_name": "R-20260914-washout-v7",
        "versions_tried": 7,
        "ideas_dropped": 0,
        "pick_symbol": "NSE:KEI",
        "pick_timeframe": "day",
        "lakh_end_value": 89684.61,
        "hold_end_value": 130721.39,
        "locked_trades": 6,
        "win_rate_pct": 33.33,
        "worst_dip_pct": 16.94,
        "verdict_passed": False,
        "beat_holding": False,
        "ai_review": "Judge only on combos_beating_hold. Net PnL here is noise.",
    }
    run.update(overrides)
    return run


def a_version(idea=1, version=1, tested=1177, profitable=761, beat=141, **over):
    row = {
        "idea_no": idea, "version_no": version, "valid": True,
        "decision": "next_version", "strategy_name": f"R-x-v{version}",
        "training_summary": {"combos_tested": tested, "combos_profitable": profitable,
                             "combos_beating_hold": beat},
    }
    row.update(over)
    return row


def seven_versions():
    counts = [(761, 141), (875, 122), (254, 39), (680, 106), (636, 26), (455, 66), (916, 131)]
    rows = [a_version(version=n, profitable=p, beat=b)
            for n, (p, b) in enumerate(counts, start=1)]
    rows[-1]["decision"] = "stop"
    rows[-1]["strategy_name"] = "R-20260914-washout-v7"
    return rows


def labelled(run):
    return dict(summary_rows(run))


def test_rupees_are_grouped_the_way_they_are_read_here():
    assert rupees(100000) == "₹1,00,000"
    assert rupees(89684.61) == "₹89,685"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(None) == "n/a"


# --- every number says over how long ------------------------------------------


def test_the_locked_window_is_measured_from_its_own_dates():
    """Measured, not assumed: a topped-up DATA_END moves the window."""
    assert round(locked_months(a_run())) == 12
    assert round(locked_months(a_run(locked_to=date(2026, 1, 31)))) == 6
    assert locked_months(a_run(locked_from=None, locked_to=None)) == 12.0


def test_the_balance_says_what_was_put_in_and_what_came_out():
    rows = labelled(a_run())
    assert rows["Started with"] == "₹1,00,000"
    assert rows["Ended with"] == "₹89,685"


def test_the_gain_or_loss_names_the_period():
    """-10.3% is unreadable without knowing it took a year."""
    assert labelled(a_run())["You lost"] == "₹10,315  (-10.3% over 12 months)"
    gained = labelled(a_run(lakh_end_value=143000))
    assert gained["You gained"] == "₹43,000  (+43.0% over 12 months)"


def test_trades_are_given_as_a_rate_not_only_a_total():
    """Six trades means nothing; half a trade a month is a sentence."""
    assert labelled(a_run())["Trades"] == "6 in 12 months  (0.5 per month)"
    busy = labelled(a_run(locked_trades=48))
    assert busy["Trades"] == "48 in 12 months  (4.0 per month)"


def test_wins_are_counted_not_only_given_as_a_percentage():
    assert labelled(a_run())["Wins"] == "2 of 6  (33%)"


def test_every_label_fits_its_column():
    """An overflowing label silently runs into its value, only visible on a phone."""
    for run in (a_run(), a_run(lakh_end_value=143000, verdict_passed=True),
                a_run(pick_symbol=None, lakh_end_value=None, hold_end_value=None)):
        for label, _ in summary_rows(run):
            assert len(label) < LABEL_WIDTH, f"{label!r} does not fit"


# --- the verdict --------------------------------------------------------------


def test_the_verdict_explains_itself_rather_than_stating_a_word():
    assert labelled(a_run())["VERDICT"] == "FAILED - lost money; holding gained instead"
    assert labelled(a_run(verdict_passed=True, beat_holding=True))["VERDICT"] == (
        "PASSED - made money and beat holding")
    assert "holding made more" in labelled(a_run(verdict_passed=True))["VERDICT"]
    assert "holding lost more" in labelled(a_run(beat_holding=True))["VERDICT"]


def test_a_day_with_no_pick_still_carries_a_verdict():
    rows = labelled(a_run(pick_symbol=None, lakh_end_value=None, hold_end_value=None))
    assert rows["Best stock"] == "none qualified"
    assert rows["VERDICT"] == "no strategy worth measuring"


# --- one idea, or several -----------------------------------------------------


def test_seven_tweaks_of_one_idea_say_so():
    """Seven rows look like seven strategies unless the message says otherwise."""
    said = idea_shape(seven_versions())
    assert "7 versions of this ONE idea" in said
    assert "not separate strategies" in said


def test_several_ideas_say_how_many_of_each():
    versions = [a_version(idea=1, version=1), a_version(idea=2, version=1),
                a_version(idea=2, version=2)]
    said = idea_shape(versions)
    assert "2 separate ideas, 3 versions" in said


def test_a_single_version_day_says_that_plainly():
    assert idea_shape([a_version()]) == "One idea, tested once."


# --- every version gets a verdict ---------------------------------------------


def test_the_edge_scale_is_the_same_for_every_version():
    assert edge_verdict(600, 1177) == "REAL EDGE"
    assert edge_verdict(400, 1177) == "mixed"
    assert edge_verdict(131, 1177) == "no edge"
    assert edge_verdict(None, 1177) == "not measured"
    assert edge_verdict(5, 0) == "not measured"


def test_every_version_row_carries_a_verdict():
    rows = version_table(seven_versions(), chosen="R-20260914-washout-v7")
    assert rows[0].startswith("Ver")
    body = rows[1:]
    assert len(body) == 7
    for line in body:
        assert any(word in line for word in ("no edge", "mixed", "REAL EDGE"))
    assert "CHOSEN" in body[-1]
    assert sum("CHOSEN" in line for line in body) == 1


def test_a_version_row_shows_both_counts_as_shares():
    row = version_table([a_version(profitable=761, beat=141)])[1]
    assert "761 (65%)" in row and "141 (12%)" in row


def test_a_rejected_version_says_so_rather_than_showing_zeros():
    rows = version_table([a_version(), a_version(version=2, valid=False,
                                                 training_summary=None)])
    assert "rejected, not tested" in rows[2]


# --- assembling it ------------------------------------------------------------


def test_the_result_block_names_the_window_it_was_measured_on():
    body = plain_text(a_run(), title="t")
    assert "01 Aug 2025 to 31 Jul 2026" in body
    assert "never saw while being designed" in body


def test_the_versions_and_their_legend_reach_both_renderings():
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "THE 7 VERSIONS" in body and "v1.4" in body
    assert "Beat holding\" is the one that matters" in body
    marked = telegram_html(a_run(), title="t", versions=seven_versions())
    assert marked.count("<pre>") == 2 and "CHOSEN" in marked


def test_a_single_version_day_adds_no_version_table():
    assert "VERSIONS" not in plain_text(a_run(), title="t", versions=[a_version()])


def test_a_title_with_an_angle_bracket_cannot_break_the_markup():
    """EMA20>EMA50 would otherwise make Telegram reject the whole message."""
    body = telegram_html(a_run(), title="EMA20>EMA50 uptrend & pullback")
    assert "EMA20&gt;EMA50 uptrend &amp; pullback" in body
    assert "EMA20>EMA50" not in body


def test_the_dashboard_link_is_included_when_there_is_one():
    assert "https://example.test/x" in telegram_html(
        a_run(), title="t", dashboard_url="https://example.test/x")
    assert "Full detail" not in telegram_html(a_run(), title="t")


def test_a_day_cut_short_says_why():
    assert "cut short" in telegram_html(a_run(status="stopped_limit"), title="t").lower()


def test_only_the_headline_of_a_review_reaches_the_phone():
    review = ("1) Judge only on combos_beating_hold. 2) Average hold return of "
              "~418% means these are bull-market years. 3) Check gross first.")
    body = plain_text(a_run(ai_review=review), title="t")
    assert "What the AI concluded: 1) Judge only on combos_beating_hold." in body
    assert "418%" not in body


def test_both_renderings_are_capped_at_telegrams_limit():
    long_title = "x" * 8000
    assert len(telegram_html(a_run(), title=long_title,
                             versions=seven_versions())) <= TELEGRAM_LIMIT
    assert len(plain_text(a_run(), title=long_title,
                          versions=seven_versions())) <= TELEGRAM_LIMIT


def test_a_review_with_no_sentence_end_is_cut_with_an_ellipsis():
    assert first_sentence("x" * 400).endswith("…")
    assert len(first_sentence("x" * 400)) <= 260
