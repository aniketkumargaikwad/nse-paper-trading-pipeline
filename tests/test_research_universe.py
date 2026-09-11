"""Which symbols and timeframes a research run tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from research.universe import (  # noqa: E402
    INDEX_TIMEFRAMES,
    INDEXES,
    STOCK_TIMEFRAMES,
    VOLUME_INPUTS,
    Combo,
    document_uses_volume,
    research_combos,
)
from strategy.vocabulary import (  # noqa: E402
    EXPR_MULTI_OUTPUT,
    EXPR_SIMPLE_INDICATORS,
    EXPR_STRUCTURE,
    INDICATOR_PARAMS,
    PRICE_SOURCES,
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


# A new indicator in vocabulary.py must be classified here, or a volume-based
# one would slip past the index skip and be tested on Yahoo's fake index
# volume with no test failing to catch it.
NOT_VOLUME = {
    "open", "high", "low", "close", "ema", "sma", "wma", "hma", "dema", "tema",
    "rsi", "cci", "williams_r", "roc", "momentum", "trix", "stddev", "atr",
    "awesome", "ultimate", "macd", "bbands", "supertrend", "stoch", "stochrsi",
    "adx", "aroon", "donchian", "keltner", "psar", "swing",
}


def test_every_vocabulary_name_is_classified_as_volume_or_not():
    """A new indicator in vocabulary.py must be classified here, or a
    volume-based one would be tested on Yahoo's fake index volume."""
    vocabulary_names = (
        set(PRICE_SOURCES)
        | set(INDICATOR_PARAMS)
        | set(EXPR_SIMPLE_INDICATORS)
        | set(EXPR_MULTI_OUTPUT)
        | set(EXPR_STRUCTURE)
    )
    for name in vocabulary_names:
        assert (name in VOLUME_INPUTS) ^ (name in NOT_VOLUME), name
    for name in VOLUME_INPUTS:
        assert name in vocabulary_names, name


@pytest.mark.parametrize(
    "doc",
    [
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"when": "daily.volume > 0", "goto": "a"}]}]},
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"when": "vwma(20) > close", "goto": "a"}]}]},
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"when": "obv() > 0", "goto": "a"}]}]},
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"when": "MFI(14) < 20", "goto": "a"}]}]},
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"set": {"x": "cmf(20)"}, "goto": "a"}]}]},
        {"version": 3, "states": [{"name": "a", "transitions": [
            {"enter": {"side": "long", "stop": "vwap()"}, "goto": "a"}]}]},
    ],
)
def test_every_volume_word_is_detected_in_v3_expressions(doc):
    assert document_uses_volume(doc)


def test_bare_string_stock_timeframes_is_rejected():
    with pytest.raises(TypeError):
        research_combos(["NSE:A"], include_indexes=False, stock_timeframes="day")


def test_unknown_stock_timeframe_is_rejected():
    with pytest.raises(ValueError):
        research_combos(["NSE:A"], include_indexes=False, stock_timeframes=("1h",))
