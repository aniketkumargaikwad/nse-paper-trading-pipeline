"""Which symbols and timeframes a research run tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.universe import (  # noqa: E402
    INDEX_TIMEFRAMES,
    INDEXES,
    STOCK_TIMEFRAMES,
    Combo,
    document_uses_volume,
    research_combos,
)


def test_two_hundred_stocks_and_nine_indexes_make_1218_combinations():
    stocks = [f"NSE:S{i}" for i in range(200)]
    combos = research_combos(stocks, include_indexes=True)
    assert len(INDEXES) == 9
    assert len(combos) == 200 * len(STOCK_TIMEFRAMES) + 9 * len(INDEX_TIMEFRAMES) == 1218


def test_indexes_are_only_tested_on_hourly_and_daily():
    combos = research_combos([], include_indexes=True)
    assert {c.timeframe for c in combos} == {"60m", "day"}
    assert all(c.is_index for c in combos)


def test_indexes_can_be_left_out():
    combos = research_combos(["NSE:A"], include_indexes=False)
    assert combos == [Combo("NSE:A", tf, False) for tf in STOCK_TIMEFRAMES]


def test_stock_timeframes_can_be_narrowed_for_a_quick_run():
    combos = research_combos(["NSE:A"], include_indexes=False, stock_timeframes=("day",))
    assert combos == [Combo("NSE:A", "day", False)]


def test_v2_volume_operand_counts_as_a_volume_rule():
    doc = {"entry": {"all": [{"indicator": "volume", "operator": ">",
                              "compare_to": {"indicator": "sma", "source": "volume",
                                             "params": {"period": 20}}}]},
           "exit": {"any": []}}
    assert document_uses_volume(doc)


def test_v3_vwap_expression_counts_as_a_volume_rule():
    doc = {"version": 3, "states": [{"name": "a", "transitions": [
        {"when": "close > vwap()", "goto": "a"}]}]}
    assert document_uses_volume(doc)


def test_a_name_mentioning_volume_does_not_count():
    doc = {"name": "VOLUME-IDEA", "description": "volume someday",
           "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
           "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]}}
    assert not document_uses_volume(doc)
