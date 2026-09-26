"""What Opus is asked, built only from training-side facts."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.prompts import (  # noqa: E402
    PROPOSE_SCHEMA,
    REVIEW_SCHEMA,
    propose_prompt,
    review_prompt,
)
from research.summary import build_summary  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

LOCKED_WORDS = ("locked year", "lakh", "just holding", "verdict", "2026-07-31")


def a_summary():
    trades = tuple(
        trade(entry=ist(2024, 1, 2 + i, 10), exit_=ist(2024, 1, 2 + i, 14),
              entry_price=100.0, exit_price=105.0)
        for i in range(3)
    )
    return build_summary(
        [ComboResult("NSE:A", "day", False, trades, hold_return_pct=2.0)],
        FREE, window_days_for=lambda r: 365,
    )


def test_propose_carries_the_format_docs_and_the_rules():
    text = propose_prompt(formats=["V2 FORMAT DOC", "V3 FORMAT DOC"], notes=[], ideas_tried=[])
    assert "V2 FORMAT DOC" in text and "V3 FORMAT DOC" in text
    assert "notional" in text and "square_off" in text


def test_propose_includes_recent_notes_and_what_was_already_tried():
    text = propose_prompt(
        formats=["F"],
        notes=[{"day": date(2026, 9, 11), "idea_title": "gap fade",
                "lessons": "gaps close less often than expected"}],
        ideas_tried=["2026-09-11 gap fade — lost money in training"],
    )
    assert "gap fade" in text and "gaps close less often" in text
    assert "2026-09-11 gap fade" in text


def test_propose_asks_for_a_first_version_or_a_change():
    first = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    again = propose_prompt(formats=["F"], notes=[], ideas_tried=[],
                           previous=a_summary(), change_hint="make it trade less")
    assert "change_note" in first
    assert "make it trade less" in again


def test_a_rejected_proposal_is_told_exactly_what_to_fix():
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[],
                          error="entry: unknown indicator 'foo'")
    assert "unknown indicator 'foo'" in text


def test_review_shows_the_training_result_and_asks_for_a_decision():
    text = review_prompt(summary=a_summary(), version=2, versions_left=5)
    assert "combos_tested" in text and "decision" in text
    assert "next_version" in text and "new_idea" in text and "stop" in text


def test_the_last_version_is_told_it_must_stop():
    text = review_prompt(summary=a_summary(), version=7, versions_left=0)
    assert "must" in text.lower() and "stop" in text
    assert "last version" in text.lower()


def test_no_prompt_can_mention_the_locked_year():
    """Design 2.4: the exam is not in anything the builder is shown."""
    texts = [
        propose_prompt(formats=["F"], notes=[], ideas_tried=[], previous=a_summary()),
        review_prompt(summary=a_summary(), version=1, versions_left=6),
    ]
    for text in texts:
        lowered = text.lower()
        assert not any(word in lowered for word in LOCKED_WORDS)


def test_the_schemas_require_the_fields_the_loop_reads():
    assert set(PROPOSE_SCHEMA["required"]) == {
        "title", "description", "hypothesis", "strategy_yaml", "change_note"}
    assert set(REVIEW_SCHEMA["required"]) == {
        "why_failed", "why_worked", "lessons", "decision"}
    assert REVIEW_SCHEMA["properties"]["decision"]["enum"] == [
        "next_version", "new_idea", "stop"]


def test_propose_asks_for_one_strategy_not_a_whole_file():
    """The format pages show a whole file, and the first real answer copied it."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "single top-level YAML mapping" in text
    assert "`strategies:` wrapper removed" in text
    assert "version: 3" in text


def test_review_says_the_tables_are_ranked_by_excess_over_holding():
    """Ranked by rupees, the tables showed the most beta-heavy combinations."""
    text = review_prompt(summary=a_summary(), version=1, versions_left=2)
    assert "excess_vs_hold_pct" in text and "combos_beating_hold" in text


# --- what the strategy is being designed for ---------------------------------


def test_propose_states_the_monthly_return_being_aimed_at():
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "5-7% A MONTH" in text
    assert "80-125% a year" in text
    assert "deliberately ambitious" in text


def test_propose_states_the_basket_rule_the_pick_is_made_by():
    """Opus designs for the judge it will face: the whole basket, per month."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "judged as an ACCOUNT" in text
    assert "ten slots" in text and "10+ times a month" in text
    assert "holding the same stocks" in text
    assert "DISCARDED" in text


def test_propose_carries_what_the_data_already_showed():
    """A fact that costs a day to re-learn is a fact worth stating."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "already been measured" in text
    assert "REVERSED out of sample" in text
    assert "survivorship" in text


def test_review_lets_opus_name_an_earlier_version_as_final():
    text = review_prompt(summary=a_summary(), version=3, versions_left=2)
    assert "final_version" in text and '"v1.2"' in text
    assert "final_version" in REVIEW_SCHEMA["properties"]


def test_propose_asks_which_segment_it_is_designing_for():
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "intraday" in text and "swing" in text and "long-term" in text
    assert "MEASURES the segment" in text


def test_propose_rules_out_futures_and_options():
    """The candle store is cash equities; an F&O idea cannot be tested at all."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "FUTURES AND OPTIONS ARE NOT TESTABLE" in text



def test_propose_says_daily_bars_and_long_only():
    """A day that sweeps daily bars must not invite shorts, session times or
    hourly reads - each is refused by the checker and costs a version."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "DAILY BARS ONLY" in text
    assert "LONG ONLY" in text
    assert "never hourly." in text
    assert "5m, 15m, 25m, 30m, 60m and day" not in text
    assert 'MUST set session.square_off' not in text


def test_the_luck_line_counts_the_combinations_a_day_actually_runs():
    from research.universe import SWEEP_TIMEFRAMES, index_timeframes_within, research_combos
    runs = len(research_combos([f"s{i}" for i in range(200)], include_indexes=True,
                               stock_timeframes=SWEEP_TIMEFRAMES,
                               index_timeframes=index_timeframes_within(SWEEP_TIMEFRAMES)))
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert f"~{runs:,} combinations is nearly always luck" in text


def test_propose_says_a_rule_reads_only_its_own_stock():
    """The facts once listed cross-stock ranking as untested; no operand can
    name another symbol, so every such proposal was untestable."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "reads ITS OWN stock only" in text


def test_propose_states_the_consistency_and_luck_rules():
    """Opus must know a boom-only lead is discarded, or it keeps designing one."""
    text = propose_prompt(formats=["F"], notes=[], ideas_tried=[])
    assert "HALF of its years" in text
    assert "luck check t of 2 or more" in text
    assert "2020-24" in text
