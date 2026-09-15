"""What the morning message says, for every shape a run can take.

Mostly about readability rather than arithmetic: a number without its period, a
label that runs into its value, or a version with no verdict are all things
that only show up on a phone at 9am.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.message import (  # noqa: E402
    LABEL_WIDTH,
    TELEGRAM_LIMIT,
    best_version,
    edge_verdict,
    first_sentence,
    idea_shape,
    locked_months,
    locked_rows,
    plain_text,
    rupees,
    telegram_html,
    version_rows,
)


def a_run(**overrides):
    """The 14 September 2026 run, which is the shape these were written for."""
    run = {
        "started_at": "2026-09-14T09:10:41+00:00",
        "status": "completed",
        "data_end": date(2026, 7, 31),
        "locked_from": date(2025, 8, 1),
        "locked_to": date(2026, 7, 31),
        "final_strategy_name": "R-v7",
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


def a_version(version=1, *, beat=141, profitable=761, tested=1177, **over):
    """A version carrying the enriched training figures a new run stores."""
    row = {
        "idea_no": 1, "version_no": version, "valid": True, "decision": "next_version",
        "strategy_name": f"R-v{version}", "change_note": "" if version == 1 else "widened the stop",
        "training_summary": {
            "combos_tested": tested, "combos_profitable": profitable,
            "combos_beating_hold": beat,
            "top": [{
                "symbol": "NSE:CGPOWER", "timeframe": "30m", "trades": 350,
                "trades_per_month": 3.5, "win_rate_pct": 51.0, "end_value": 345000.0,
                "holding_value": 210000.0, "cagr_pct": over.pop("cagr_pct", 16.2),
                "window_years": 8.4, "worst_dip_pct": 22.1,
                "segment": "swing", "held_days": 4.0,
            }],
        },
    }
    row.update(over)
    return row


def seven_versions():
    beats = [141, 122, 39, 106, 26, 66, 131]
    rows = [a_version(version=n, beat=b) for n, b in enumerate(beats, start=1)]
    rows[-1]["decision"] = "stop"
    return rows


def labelled(rows):
    return dict(rows)


def test_rupees_are_grouped_the_way_they_are_read_here():
    assert rupees(100000) == "₹1,00,000"
    assert rupees(89684.61) == "₹89,685"
    assert rupees(12345678) == "₹1,23,45,678"
    assert rupees(None) == "n/a"


# --- a version block is the FULL training backtest ----------------------------


def test_the_message_warns_that_best_of_1177_is_selection_not_skill():
    """The 83x figure that prompted this: one lucky stock out of 1,177 tries."""
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "single best of ~1,177 combinations" in body
    assert "often a different stock" in body


def test_a_version_reports_the_whole_training_window_not_one_year():
    rows = labelled(version_rows(a_version()))
    assert rows["Tested over"] == "8.4 years of history"
    assert rows["Luckiest"] == "NSE:CGPOWER, 30m bars"


def test_a_version_gives_trades_as_a_total_and_a_rate():
    """3.5 a month is under the swing floor of 4, and the row says so."""
    assert labelled(version_rows(a_version()))["Total trades"] == (
        "350  (3.5 a month, below the 4 floor)")


def test_a_combination_clearing_its_floor_is_not_flagged():
    fast = a_version()
    fast["training_summary"]["top"][0]["trades_per_month"] = 9.0
    assert labelled(version_rows(fast))["Total trades"] == "350  (9.0 a month)"


def test_a_version_gives_the_money_and_the_benchmark():
    rows = labelled(version_rows(a_version()))
    assert rows["Started with"] == "₹1,00,000"
    assert rows["Ended with"] == "₹3,45,000"
    assert rows["Just holding"] == "₹2,10,000"


def test_a_version_says_which_segment_it_trades():
    """Measured from the holding period, not from what the strategy claims."""
    rows = labelled(version_rows(a_version()))
    assert rows["Built for"] == "swing (held ~4 days)"


def test_a_version_in_the_target_band_says_so():
    rows = labelled(version_rows(a_version(cagr_pct=70.0)))
    assert "IN the 4-7% target" in rows["Return a month"]


def test_a_version_gives_yearly_and_monthly_return():
    rows = labelled(version_rows(a_version()))
    assert rows["Return a year"] == "+16.2%"
    # 16.2% a year compounds from about 1.26% a month, not 16.2/12 = 1.35%,
    # and the line says where that sits against the 4-7% the owner wants.
    assert rows["Return a month"] == "+1.26%  (below the 4-7% target)"


def test_the_monthly_return_compounds_rather_than_divides():
    rows = labelled(version_rows(a_version(training_summary={
        "combos_tested": 10, "combos_profitable": 5, "combos_beating_hold": 1,
        "top": [{"symbol": "A", "timeframe": "day", "trades": 30, "cagr_pct": 12.0}],
    })))
    assert rows["Return a month"].startswith("+0.95%")      # 12% a year, not 1.00%


def test_every_version_carries_a_verdict_on_one_scale():
    assert labelled(version_rows(a_version(beat=700)))["VERDICT"].startswith("REAL EDGE")
    assert labelled(version_rows(a_version(beat=400)))["VERDICT"].startswith("mixed")
    assert labelled(version_rows(a_version(beat=141)))["VERDICT"].startswith("no edge")


def test_a_rejected_version_says_so_rather_than_showing_zeros():
    rows = labelled(version_rows(
        {"idea_no": 1, "version_no": 2, "valid": False, "error": "unknown indicator 'foo'"}))
    assert "unknown indicator" in rows["Rejected"]
    assert rows["VERDICT"] == "never tested"


def test_a_version_stored_before_the_figures_existed_omits_them():
    """An older run has no end_value; a missing row beats "n/a" beside money."""
    old = a_version(training_summary={
        "combos_tested": 1177, "combos_profitable": 761, "combos_beating_hold": 141,
        "top": [{"symbol": "NSE:X", "timeframe": "day", "trades": 35}],
    })
    rows = labelled(version_rows(old))
    assert "Ended with" not in rows and "Return a year" not in rows
    assert rows["VERDICT"].startswith("no edge")


def test_every_label_fits_its_column():
    """An overflowing label silently runs into its value."""
    for compact in (False, True):
        for label, _ in version_rows(a_version(), compact=compact):
            assert len(label) < LABEL_WIDTH, f"{label!r} does not fit"
    for label, _ in locked_rows(a_run()):
        assert len(label) < LABEL_WIDTH, f"{label!r} does not fit"


# --- the locked year is the exam, and only the chosen version sits it ---------


def test_the_locked_window_is_measured_from_its_own_dates():
    assert round(locked_months(a_run())) == 12
    assert round(locked_months(a_run(locked_to=date(2026, 1, 31)))) == 6


def test_the_locked_block_names_the_gain_and_its_period():
    rows = labelled(locked_rows(a_run(locked_trades=6)))
    assert rows["You lost"] == "₹10,315  (-10.3% over 12 months)"
    assert rows["Trades"] == "6 in 12 months  (0.5 per month)"
    assert rows["Wins"] == "2 of 6  (33%)"


def test_the_locked_verdict_explains_itself():
    """With enough trades to judge; below that see the too-few-trades tests."""
    assert labelled(locked_rows(a_run(locked_trades=12)))["VERDICT"] == (
        "FAILED - lost money; holding gained instead")
    assert labelled(locked_rows(a_run(locked_trades=12, verdict_passed=True,
                                      beat_holding=True)))["VERDICT"] == (
        "PASSED - made money and beat holding")


def test_a_day_with_no_pick_still_carries_a_verdict():
    rows = labelled(locked_rows(a_run(pick_symbol=None, lakh_end_value=None,
                                      hold_end_value=None)))
    assert rows["Best stock"] == "none qualified"
    assert "beat simply holding" in rows["Why none"]


def test_the_locked_year_appears_once_not_per_version():
    """Seven locked years to choose from would be fitting to the exam."""
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert body.count("THE LOCKED YEAR") == 1
    # The locked year's own figures appear once, not beside every version.
    assert body.count("You lost") == 1
    assert body.count("NSE:KEI") == 1


# --- one idea, or several -----------------------------------------------------


def test_seven_tweaks_of_one_idea_say_so():
    said = idea_shape(seven_versions())
    assert "7 versions of this ONE idea" in said and "not separate strategies" in said


def test_several_ideas_say_how_many_of_each():
    versions = [a_version(1), a_version(2), a_version(3)]
    versions[1]["idea_no"] = 2
    versions[2]["idea_no"] = 2
    assert "2 separate ideas, 3 versions" in idea_shape(versions)


# --- the day's own verdict ----------------------------------------------------


def test_the_best_version_is_the_one_that_beat_holding_most():
    assert best_version(seven_versions())["version_no"] == 1      # 141 is the highest


def test_the_verdict_block_names_that_version():
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "VERDICT FOR THE DAY" in body
    assert "Best of the 7: v1.1" in body


def test_it_says_when_the_day_ended_on_a_different_version():
    body = plain_text(a_run(final_strategy_name="R-v7"), title="t", versions=seven_versions())
    assert "The day ended on a different version" in body


def test_the_edge_scale_is_the_same_everywhere():
    assert edge_verdict(600, 1177).startswith("REAL EDGE")
    assert edge_verdict(400, 1177).startswith("mixed")
    assert edge_verdict(131, 1177).startswith("no edge")
    assert edge_verdict(None, 1177) == "not measured"
    assert edge_verdict(5, 0) == "not measured"


# --- assembling it ------------------------------------------------------------


def test_the_message_says_the_versions_are_the_full_backtest():
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "FULL backtest: every training year" in body


def test_each_version_heading_says_what_changed():
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "v1.1 - the first version" in body
    assert "v1.2 - changed: widened the stop" in body


def test_a_long_change_note_is_cut_rather_than_wrapped_forever():
    versions = seven_versions()
    versions[1]["change_note"] = "x" * 400
    body = plain_text(a_run(), title="t", versions=versions)
    assert "x" * 200 not in body


def test_seven_full_version_blocks_fit_inside_telegrams_limit():
    """Seven at full detail is the case that overflowed and lost six of them."""
    for render in (plain_text, telegram_html):
        body = render(a_run(), title="t", versions=seven_versions(),
                      dashboard_url="https://example.test")
        assert len(body) <= TELEGRAM_LIMIT
        for tag in ("v1.1", "v1.2", "v1.3", "v1.4", "v1.5", "v1.6", "v1.7"):
            assert tag in body, f"{tag} was dropped"


def test_a_title_with_an_angle_bracket_cannot_break_the_markup():
    """EMA20>EMA50 would otherwise make Telegram reject the whole message."""
    body = telegram_html(a_run(), title="EMA20>EMA50 uptrend & pullback")
    assert "EMA20&gt;EMA50 uptrend &amp; pullback" in body
    assert "EMA20>EMA50" not in body


def test_the_dashboard_link_is_included_when_there_is_one():
    assert "https://example.test/x" in telegram_html(
        a_run(), title="t", dashboard_url="https://example.test/x")
    assert "Full detail" not in telegram_html(a_run(), title="t")


def test_a_day_cut_short_ends_with_the_marker_rather_than_a_banner():
    body = plain_text(a_run(status="stopped_limit"), title="t")
    assert body.rstrip().endswith("limit expired.........................")


def test_only_the_headline_of_a_review_reaches_the_phone():
    review = ("1) Judge only on combos_beating_hold. 2) Average hold return of "
              "~418% means these are bull-market years. 3) Check gross first.")
    body = plain_text(a_run(ai_review=review), title="t")
    assert "1) Judge only on combos_beating_hold." in body
    assert "418%" not in body


def test_a_review_with_no_sentence_end_is_cut_with_an_ellipsis():
    assert first_sentence("x" * 400).endswith("…")
    assert len(first_sentence("x" * 400)) <= 220


# --- a day the allowance cut short -------------------------------------------


def test_a_cut_short_day_ends_at_the_marker():
    """It stops where the run stopped rather than describing a day that finished."""
    body = plain_text(a_run(status="stopped_limit"), title="t", versions=seven_versions())
    assert body.rstrip().endswith("limit expired.........................")
    assert "VERDICT FOR THE DAY" not in body
    assert "THE LOCKED YEAR" not in body


def test_the_marker_is_preceded_by_a_blank_line():
    body = plain_text(a_run(status="stopped_limit"), title="t", versions=seven_versions())
    tail = body.rstrip().splitlines()
    assert tail[-1] == "limit expired........................."
    assert tail[-2] == "" and tail[-3] == ""


def test_the_versions_it_finished_are_all_still_there():
    body = plain_text(a_run(status="stopped_limit"), title="t", versions=seven_versions())
    for tag in ("v1.1", "v1.4", "v1.7"):
        assert tag in body


def test_a_finished_day_carries_no_marker():
    body = plain_text(a_run(), title="t", versions=seven_versions())
    assert "limit expired" not in body
    assert "THE LOCKED YEAR" in body


def test_the_marker_survives_html_escaping():
    body = telegram_html(a_run(status="stopped_limit"), title="t", versions=seven_versions())
    assert "limit expired........................." in body


# --- too few trades is not a failure -----------------------------------------


def test_one_trade_in_the_locked_year_is_not_a_verdict():
    """A single trade is a coin toss; calling it FAILED reads as evidence."""
    rows = labelled(locked_rows(a_run(locked_trades=1, win_rate_pct=0.0)))
    assert rows["VERDICT"] == "TOO FEW TRADES to judge - 1 in 12 months, needs 10+"


def test_enough_trades_gets_a_real_verdict():
    rows = labelled(locked_rows(a_run(locked_trades=12)))
    assert rows["VERDICT"].startswith("FAILED")


def test_too_few_trades_still_shows_what_happened():
    """The numbers are still worth seeing, they just do not settle anything."""
    rows = labelled(locked_rows(a_run(locked_trades=1, win_rate_pct=0.0)))
    assert rows["Ended with"] == "\u20b989,685"
    assert rows["Trades"] == "1 in 12 months  (0.1 per month)"
