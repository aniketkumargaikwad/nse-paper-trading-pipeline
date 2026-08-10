"""Tests for format v2 validation. Pure — no network, no database.

These assert on MESSAGE CONTENT, not merely that an error was raised. The
messages are the interface for correcting AI-generated strategies, so a message
that stops naming the offending key is a real regression.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.parse import (  # noqa: E402
    StrategyConfigError,
    parse_strategy_dict,
    resolve_quantity,
    strategy_to_raw,
)


def valid_v2() -> dict:
    """Smallest valid v2 strategy; each test breaks exactly one thing."""
    return {
        "name": "t",
        "enabled": True,
        "position_type": "long",
        "timeframe": "15m",
        "instruments": ["NSE:RELIANCE"],
        "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                           "operator": ">", "value": 50}]},
        "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                          "operator": "<", "value": 40}]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 0.7},
            "target": {"type": "percent", "value": 1.5},
        },
        # v2 sizing: rupee notional, the recommended mode (Task 4). Tests in
        # this file are about the risk block, not sizing, so this value is
        # arbitrary — it just needs to be valid.
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }


def test_percent_stop_parses():
    s = parse_strategy_dict(valid_v2())
    assert s.risk.stop_loss.type == "percent"
    assert s.risk.stop_loss.value == 0.7
    assert s.risk.trailing_stop is None


def test_percent_properties_stay_available_for_the_engine():
    s = parse_strategy_dict(valid_v2())
    assert s.risk.stop_loss_pct == 0.7
    assert s.risk.target_pct == 1.5


def test_atr_stop_parses():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": 14, "multiplier": 1.5}
    s = parse_strategy_dict(doc)
    assert s.risk.stop_loss.type == "atr"
    assert s.risk.stop_loss.period == 14
    assert s.risk.stop_loss.multiplier == 1.5


def test_trailing_stop_is_optional_and_parses():
    doc = valid_v2()
    doc["risk"]["trailing_stop"] = {"type": "percent", "value": 0.5}
    s = parse_strategy_dict(doc)
    assert s.risk.trailing_stop.value == 0.5


def test_atr_stop_rejects_percent_keys():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "value": 1.5}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "risk.stop_loss" in msg
    assert "period" in msg and "multiplier" in msg


def test_percent_stop_rejects_atr_keys():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "percent", "period": 14, "multiplier": 2}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "value" in str(exc.value)


def test_unknown_stop_type_lists_the_allowed_ones():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "trailing", "value": 1}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "trailing" in msg
    assert "percent" in msg and "atr" in msg


def test_percent_out_of_range_is_rejected():
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "percent", "value": 70}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "between 0 and 50" in str(exc.value)


# ---------------------------------------------------------------------------
# The compatibility properties. These are the one mechanism keeping the engine
# working until the backtest engine reads StopSpec directly, and an earlier
# review caught that nothing exercised their raising branch.
# ---------------------------------------------------------------------------


def atr_stop_doc() -> dict:
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": 14, "multiplier": 1.5}
    return doc


def test_stop_loss_pct_raises_on_an_atr_stop_rather_than_guessing():
    """Returning 0.0 here would misdescribe the strategy in every summary."""
    s = parse_strategy_dict(atr_stop_doc())
    with pytest.raises(ValueError) as exc:
        _ = s.risk.stop_loss_pct
    assert "atr" in str(exc.value)


def test_target_pct_raises_on_an_atr_target():
    doc = valid_v2()
    doc["risk"]["target"] = {"type": "atr", "period": 14, "multiplier": 3}
    s = parse_strategy_dict(doc)
    with pytest.raises(ValueError) as exc:
        _ = s.risk.target_pct
    assert "atr" in str(exc.value)


def test_the_two_properties_are_independent():
    """An ATR stop with a percent target: only the stop side may raise."""
    s = parse_strategy_dict(atr_stop_doc())
    with pytest.raises(ValueError):
        _ = s.risk.stop_loss_pct
    assert s.risk.target_pct == 1.5


def test_describe_renders_both_forms():
    s = parse_strategy_dict(atr_stop_doc())
    assert s.risk.stop_loss.describe() == "1.5x ATR(14)"
    assert s.risk.target.describe() == "1.5%"


# ---------------------------------------------------------------------------
# Round-tripping. Only the percent form was covered before.
# ---------------------------------------------------------------------------


def test_atr_stop_round_trips_through_strategy_to_raw():
    original = parse_strategy_dict(atr_stop_doc())
    assert parse_strategy_dict(strategy_to_raw(original)) == original


def test_trailing_stop_round_trips_through_strategy_to_raw():
    doc = valid_v2()
    doc["risk"]["trailing_stop"] = {"type": "percent", "value": 0.5}
    original = parse_strategy_dict(doc)
    assert parse_strategy_dict(strategy_to_raw(original)) == original


def test_absent_trailing_stop_is_omitted_not_emitted_as_null():
    raw = strategy_to_raw(parse_strategy_dict(valid_v2()))
    assert "trailing_stop" not in raw["risk"]


# ---------------------------------------------------------------------------
# ATR field validation and the typo ceilings.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_period", [0, -1, 2.5, "14", True])
def test_bad_atr_period_is_rejected(bad_period):
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": bad_period, "multiplier": 1.5}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "period" in str(exc.value)


@pytest.mark.parametrize("bad_multiplier", [0, -1, "1.5", True])
def test_bad_atr_multiplier_is_rejected(bad_multiplier):
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": 14, "multiplier": bad_multiplier}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "multiplier" in str(exc.value)


def test_an_absurd_atr_multiplier_is_rejected_as_a_likely_typo():
    """`multiplier: 150` almost certainly meant 1.5 — same class as 70 for 0.7."""
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"type": "atr", "period": 14, "multiplier": 150}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "20" in str(exc.value)


def test_a_missing_type_still_surfaces_a_sibling_typo():
    """Both problems in ONE message; fixing them one round-trip at a time is
    exactly what this parser's _require_keys rationale exists to avoid."""
    doc = valid_v2()
    doc["risk"]["stop_loss"] = {"perod": 14, "multiplier": 1.5}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "type" in msg
    assert "perod" in msg


# ---------------------------------------------------------------------------
# Sizing: rupee notional (the recommended mode) and legacy fixed_quantity.
# ---------------------------------------------------------------------------


def test_notional_sizing_parses():
    s = parse_strategy_dict(valid_v2())
    assert s.sizing.type == "notional"
    assert s.sizing.notional_per_trade == 100000.0
    assert s.sizing.quantity is None


def test_notional_is_required_with_no_default():
    doc = valid_v2()
    doc["sizing"] = {"type": "notional"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "notional_per_trade" in str(exc.value)


def test_legacy_fixed_quantity_still_parses():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 5}
    s = parse_strategy_dict(doc)
    assert s.sizing.type == "fixed_quantity"
    assert s.sizing.quantity == 5


def test_sizing_is_required_in_v2():
    doc = valid_v2()
    del doc["sizing"]
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "sizing" in str(exc.value)


def test_resolve_quantity_floors_the_notional():
    s = parse_strategy_dict(valid_v2())          # notional 100000
    assert resolve_quantity(s.sizing, price=1500.0) == 66   # 66.67 -> 66


def test_resolve_quantity_returns_zero_when_a_share_costs_more_than_the_notional():
    doc = valid_v2()
    doc["sizing"] = {"type": "notional", "notional_per_trade": 100}
    s = parse_strategy_dict(doc)
    assert resolve_quantity(s.sizing, price=1500.0) == 0


def test_resolve_quantity_ignores_price_for_fixed_quantity():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 3}
    s = parse_strategy_dict(doc)
    assert resolve_quantity(s.sizing, price=99999.0) == 3


def test_notional_sizing_round_trips_through_strategy_to_raw():
    original = parse_strategy_dict(valid_v2())
    assert parse_strategy_dict(strategy_to_raw(original)) == original


def test_fixed_quantity_sizing_round_trips_through_strategy_to_raw():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 7}
    original = parse_strategy_dict(doc)
    assert parse_strategy_dict(strategy_to_raw(original)) == original
