"""The basket, wired through: summary, prompt, records, message, and the day's choices."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.lakh import LakhResult  # noqa: E402
from research.message import basket_rows, best_basket, locked_rows, version_rows  # noqa: E402
from research.portfolio import build_basket  # noqa: E402
from research.prompts import review_prompt  # noqa: E402
from research.records import (  # noqa: E402
    MAX_STORED_LOCKED_TRADES,
    basket_columns,
    basket_trade_rows,
    run_row,
)
from research.run_day import choose_final, idea_line  # noqa: E402
from research.summary import build_summary  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

UTC = timezone.utc
LOCKED_WORDS = ("locked", "lakh", "hold_end", "verdict", "beat_holding", "just holding")


def a_trade(month, pct, day=10, year=2024):
    return trade(entry=ist(year, month, day, 10), exit_=ist(year, month, day, 14),
                 entry_price=100.0, exit_price=100.0 * (1 + pct / 100), quantity=1000)


def sleeve(symbol="A", timeframe="day", months=30, pct=1.0, per_month=12, hold=0.0):
    trades, closes, price = [], [], 100.0
    for i in range(months):
        year, month = 2015 + i // 12, i % 12 + 1
        # A touch of variation, so a steady sleeve still has a measurable spread.
        for k in range(per_month):
            trades.append(a_trade(month, pct * (1 + 0.01 * (i % 3)) / per_month,
                                  day=1 + k % 20, year=year))
        first = price
        price = price * (1 + hold / 100)
        closes.append((f"{year}-{month:02d}", first, price))
    return ComboResult(symbol, timeframe, False, tuple(trades), hold_return_pct=hold,
                       month_closes=tuple(closes))


# --- the summary Opus reads ------------------------------------------------------


def test_the_summary_carries_one_basket_per_stock_timeframe_and_says_why_not():
    got = build_summary([sleeve(timeframe="day"), sleeve(timeframe="60m", pct=-0.2)],
                        FREE, window_days_for=lambda r: 900)
    by_tf = {a["timeframe"]: a for a in got.accounts}
    assert by_tf["day"]["qualifies"] is True and by_tf["day"]["why_not"] is None
    assert by_tf["60m"]["qualifies"] is False and "did not beat holding" in by_tf["60m"]["why_not"]
    assert got.best_account()["timeframe"] == "day"
    assert {b["timeframe"] for b in got.baskets} == {"day", "60m"}
    assert "qualifies" not in got.baskets[0]


def test_the_baskets_carry_no_locked_year_word():
    got = build_summary([sleeve()], FREE, window_days_for=lambda r: 900)
    blob = json.dumps(got.as_dict()["baskets"] + got.as_dict()["accounts"]).lower()
    assert not any(word in blob for word in LOCKED_WORDS)


def test_the_review_prompt_tells_opus_to_judge_on_the_baskets():
    text = review_prompt(summary=build_summary([sleeve()], FREE, window_days_for=lambda r: 900),
                         version=1, versions_left=3)
    assert "`accounts`" in text and "avg_month_pct" in text and "luck_check" in text
    assert "luckiest" in text.lower()


# --- what is stored -------------------------------------------------------------


def test_run_row_carries_the_basket_columns_when_there_is_a_basket():
    basket = build_basket([sleeve(pct=5.0)], timeframe="day")
    row = run_row(
        started_at=datetime(2026, 9, 16, 1, 0, tzinfo=UTC), finished_at=None, status="completed",
        data_end=date(2026, 7, 31), locked_from=date(2025, 8, 1), strategy_name="R",
        pick_symbol="NIFTY200 basket (1 stocks)", pick_timeframe="day",
        locked=LakhResult(100_000.0, 150_000.0, 360, 360, 0.0, 50.0), hold_end_value=100_000.0,
        combos_profitable=1, combos_tested=1, warnings=[], basket=basket, training_basket=basket,
    )
    assert row["basket_stocks"] == 1
    assert row["locked_avg_month_pct"] == pytest.approx(5.0, abs=0.1)
    assert row["locked_target_met"] is True
    assert row["locked_months"][0]["month"] == "2015-01"
    assert row["training_months"] == 30 and row["training_edge_t"] is not None


def test_run_row_without_a_basket_keeps_the_columns_null():
    assert basket_columns(None, None)["locked_avg_month_pct"] is None
    assert basket_columns(None, None)["locked_target_met"] is None


def test_target_met_needs_five_percent_a_month():
    assert basket_columns(build_basket([sleeve(pct=4.8)], timeframe="day"), None)["locked_target_met"] is False
    assert basket_columns(build_basket([sleeve(pct=5.0)], timeframe="day"), None)["locked_target_met"] is True


def test_basket_trade_rows_weight_each_trade_by_the_basket_size():
    trades = [a_trade(8, 2.0, day=4, year=2025), a_trade(8, -1.0, day=5, year=2025)]
    rows = basket_trade_rows("run-1", trades, stocks=2)
    assert rows[0]["balance_after"] == pytest.approx(101_000.0)     # +2% of one of two sleeves
    assert rows[1]["balance_after"] == pytest.approx(100_500.0)
    assert rows[0]["net_return_pct"] == pytest.approx(2.0)


def test_too_many_basket_trades_are_not_stored():
    trades = [a_trade(1, 0.1, day=1 + i % 20) for i in range(MAX_STORED_LOCKED_TRADES + 1)]
    assert basket_trade_rows("run-1", trades, stocks=200) == []


# --- the morning message ----------------------------------------------------------


def a_version_with_baskets(*, qualifies=True, avg=1.2):
    return {
        "idea_no": 1, "version_no": 1, "valid": True, "decision": "stop",
        "training_summary": {
            "combos_tested": 1177, "combos_profitable": 700, "combos_beating_hold": 300,
            "top": [{"symbol": "NSE:X", "timeframe": "day", "trades": 40, "trades_per_month": 2.0,
                     "segment": "swing", "cagr_pct": 10.0, "end_value": 150000.0}],
            "baskets": [
                {"timeframe": "day", "stocks": 200, "avg_month_pct": avg, "months_positive_pct": 61.0,
                 "worst_month_pct": -4.2, "luck_check": "could be luck", "qualifies": qualifies,
                 "why_not": None if qualifies else "too slow: 3.0 trades a month across the basket (needs 10)"},
                {"timeframe": "5m", "stocks": 200, "avg_month_pct": -0.4, "months_positive_pct": 40.0,
                 "worst_month_pct": -6.0, "luck_check": "cannot be told from luck", "qualifies": False,
                 "why_not": "lost money on average: -0.40% a month"},
            ],
        },
    }


def test_the_best_basket_is_the_pickable_one_with_the_best_month():
    assert best_basket(a_version_with_baskets()["training_summary"])["timeframe"] == "day"


def test_a_version_block_says_how_the_whole_basket_did_per_month():
    rows = dict(version_rows(a_version_with_baskets()))
    assert rows["Whole basket"] == "day bars, all 200 stocks"
    assert rows["Basket/month"].startswith("+1.20%") and "below the 5-7% target" in rows["Basket/month"]
    assert rows["Months up"].startswith("61%") and "worst month -4.2%" in rows["Months up"]
    assert rows["Luck check"] == "could be luck"


def test_an_unpickable_basket_says_so_and_why():
    rows = dict(version_rows(a_version_with_baskets(qualifies=False)))
    assert rows["Whole basket"].endswith("(not pickable)")
    assert rows["Not picked"].startswith("too slow")


def test_an_account_reading_is_labelled_as_one():
    facts = {"accounts": [{"timeframe": "day", "stocks": 200, "slots": 10, "avg_month_pct": 5.5,
                           "months_positive_pct": 70.0, "worst_month_pct": -2.0,
                           "luck_check": "unlikely to be luck", "qualifies": True, "why_not": None}]}
    rows = dict(basket_rows(facts))
    assert rows["Account"] == "day bars, 10 slots over 200 stocks"
    assert rows["Account/month"].startswith("+5.50%") and "IN the 5-7% target" in rows["Account/month"]


def test_a_version_without_baskets_has_no_basket_rows():
    assert basket_rows({"top": []}) == []


def test_the_locked_block_reports_the_average_month_against_the_target():
    run = {
        "locked_from": date(2025, 8, 1), "locked_to": date(2026, 7, 31),
        "pick_symbol": "NIFTY200 basket (200 stocks)", "pick_timeframe": "day",
        "lakh_end_value": 152_000.0, "hold_end_value": 120_000.0, "locked_trades": 2400,
        "win_rate_pct": 55.0, "worst_dip_pct": 9.0, "verdict_passed": True, "beat_holding": True,
        "locked_avg_month_pct": 5.3, "locked_months_positive_pct": 75.0, "locked_worst_month_pct": -3.1,
    }
    rows = dict(locked_rows(run))
    assert rows["Traded on"] == "NIFTY200 basket (200 stocks), day bars"
    assert rows["Average month"].startswith("+5.30%") and "IN the 5-7% target" in rows["Average month"]
    assert rows["Months up"] == "75% · worst month -3.1%"
    assert rows["VERDICT"].startswith("PASSED")


# --- the day's choices -------------------------------------------------------------


def test_the_ideas_index_carries_the_basket_numbers():
    line = idea_line({
        "strategy_name": "R-20260915-x-v1", "lessons": "fees ate it",
        "training_summary": a_version_with_baskets()["training_summary"],
    })
    assert "best account day: +1.20%/month, 61% months up" in line
    assert "300 of 1177 beat holding" in line and line.endswith("fees ate it")


def test_the_ideas_index_survives_a_summary_stored_as_text_or_missing():
    assert idea_line({"strategy_name": "R", "training_summary": "not json"}) == "R"
    assert idea_line({"strategy_name": "R", "training_summary": None, "lessons": "l"}) == "R — l"


class _Version:
    def __init__(self, decision):
        self.review = {"decision": decision}


def test_the_cut_short_final_is_chosen_by_the_pick_rule_not_by_yearly_return():
    """Version B rose more but was beaten by holding; A is the honest choice."""
    beta = [sleeve("A", pct=3.0, hold=200.0)]         # bigger month, no edge
    edge = [sleeve("A", pct=1.0, hold=0.0)]
    from research.picker import best_score

    tested = [(_Version("next_version"), beta), (_Version("next_version"), edge)]
    assert choose_final(tested, best_score)[1] is edge


def test_opus_own_stop_still_wins_over_the_score():
    from research.picker import best_score

    a, b = [sleeve("A", pct=1.0)], [sleeve("A", pct=2.0)]
    tested = [(_Version("stop"), a), (_Version("next_version"), b)]
    assert choose_final(tested, best_score)[1] is a
