"""Tests for format v2 validation. Pure — no network, no database.

These assert on MESSAGE CONTENT, not merely that an error was raised. The
messages are the interface for correcting AI-generated strategies, so a message
that stops naming the offending key is a real regression.
"""

from __future__ import annotations

import sys
from datetime import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.parse import (  # noqa: E402
    StrategyConfigError,
    parse_strategy_dict,
    resolve_quantity,
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


# ---------------------------------------------------------------------------
# Sizing: cross-form rejection and typo surfacing, mirroring the stop-spec
# tests above. Review caught that only the stop side had this coverage.
# ---------------------------------------------------------------------------


def test_notional_sizing_rejects_a_quantity_key():
    doc = valid_v2()
    doc["sizing"] = {"type": "notional", "notional_per_trade": 100000, "quantity": 5}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "quantity" in str(exc.value)


def test_fixed_quantity_sizing_rejects_a_notional_key():
    doc = valid_v2()
    doc["sizing"] = {"type": "fixed_quantity", "quantity": 5, "notional_per_trade": 100000}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "notional_per_trade" in str(exc.value)


def test_unknown_sizing_type_lists_the_allowed_ones():
    doc = valid_v2()
    doc["sizing"] = {"type": "risk_based", "notional_per_trade": 100000}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "risk_based" in msg
    assert "notional" in msg and "fixed_quantity" in msg


def test_a_missing_sizing_type_still_surfaces_a_sibling_typo():
    doc = valid_v2()
    doc["sizing"] = {"typ": "notional", "notional_per_trade": 100000}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "type" in msg
    assert "typ" in msg


@pytest.mark.parametrize("bad", [0, -1, "100000", True])
def test_bad_notional_is_rejected(bad):
    doc = valid_v2()
    doc["sizing"] = {"type": "notional", "notional_per_trade": bad}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "notional_per_trade" in str(exc.value)


def test_resolve_quantity_is_exact_at_decimal_boundaries():
    """Float // floors one short here: 100.0 // 0.1 is 999.0, not 1000.0.

    Sizing one share light on every trade would quietly bias every backtest,
    so this uses Decimal — matching the deliberate numeric(14,4) choice in the
    candle store rather than trusting binary floating point.
    """
    doc = valid_v2()
    doc["sizing"] = {"type": "notional", "notional_per_trade": 100}
    sizing = parse_strategy_dict(doc).sizing
    assert resolve_quantity(sizing, price=0.1) == 1000


def test_resolve_quantity_handles_a_price_of_zero_without_raising():
    sizing = parse_strategy_dict(valid_v2()).sizing
    assert resolve_quantity(sizing, price=0.0) == 0
    assert resolve_quantity(sizing, price=-5.0) == 0


# ---------------------------------------------------------------------------
# Session: entry window and intraday square-off.
# ---------------------------------------------------------------------------


def test_session_is_optional_and_defaults_to_empty():
    s = parse_strategy_dict(valid_v2())
    assert s.session.no_entry_before is None
    assert s.session.no_entry_after is None
    assert s.session.square_off is None


def test_session_times_parse():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "09:30", "no_entry_after": "14:30",
                      "square_off": "15:15"}
    s = parse_strategy_dict(doc)
    assert s.session.no_entry_before == time(9, 30)
    assert s.session.square_off == time(15, 15)


def test_session_rejects_a_non_time_string():
    doc = valid_v2()
    doc["session"] = {"square_off": "quarter past three"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "session.square_off" in msg
    assert "HH:MM" in msg


def test_session_rejects_a_time_outside_the_nse_session():
    doc = valid_v2()
    doc["session"] = {"square_off": "17:00"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "09:15" in str(exc.value) and "15:30" in str(exc.value)


def test_entry_window_must_not_be_inverted():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "14:30", "no_entry_after": "09:30"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "no_entry_before" in str(exc.value)


def test_square_off_must_not_precede_the_entry_window():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "14:00", "square_off": "13:00"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "square_off" in str(exc.value)


def test_session_rejects_an_unknown_key_alongside_the_allowed_ones():
    """Mirrors _parse_stop_spec / _parse_sizing: a typo like `squareoff` is

    reported by name, alongside the allowed keys, in the same message that
    _require_keys already produces for `optional=set(SESSION_KEYS)`.
    """
    doc = valid_v2()
    doc["session"] = {"squareoff": "15:15"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    msg = str(exc.value)
    assert "squareoff" in msg
    assert "square_off" in msg
    assert "no_entry_before" in msg and "no_entry_after" in msg


def test_session_round_trips_through_strategy_to_raw_when_present():
    doc = valid_v2()
    doc["session"] = {"no_entry_before": "09:30", "no_entry_after": "14:30",
                      "square_off": "15:15"}
    original = parse_strategy_dict(doc)
    assert parse_strategy_dict(strategy_to_raw(original)) == original


def test_session_is_omitted_not_emitted_empty_when_absent():
    raw = strategy_to_raw(parse_strategy_dict(valid_v2()))
    assert "session" not in raw


# ---------------------------------------------------------------------------
# Public surface. strategy_schema.py is a backwards-compatible re-export shim
# over the strategy/ package (Tasks 1-2); anything added to one __all__ and
# not the other is a broken import for whichever caller uses the other name.
# ---------------------------------------------------------------------------


def test_strategy_package_and_shim_export_the_same_names():
    import strategy
    import strategy_schema

    assert set(strategy.__all__) == set(strategy_schema.__all__)


def test_session_rejects_a_time_carrying_seconds():
    doc = valid_v2()
    doc["session"] = {"square_off": "15:15:30"}
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "no seconds" in str(exc.value)


def test_session_bounds_agree_with_the_market_calendar():
    """vocabulary.py cannot import config — its stdlib-only contract is what
    lets the migration and the format-doc generator read it without circular
    imports. So the NSE session bounds are duplicated there deliberately, and
    this test is what stops the two copies drifting apart.
    """
    from datetime import time as _time

    from config import MARKET_CLOSE_IST, MARKET_OPEN_IST
    from strategy.vocabulary import SESSION_CLOSE_HHMM, SESSION_OPEN_HHMM

    assert _time.fromisoformat(SESSION_OPEN_HHMM) == MARKET_OPEN_IST
    assert _time.fromisoformat(SESSION_CLOSE_HHMM) == MARKET_CLOSE_IST


# ---------------------------------------------------------------------------
# universe: as an alternative to instruments: (Task 6). Parsing only checks
# the SHAPE of the universe name here — never whether it exists, which needs
# the database and is checked at save time instead (Task 14).
# ---------------------------------------------------------------------------


def test_universe_replaces_instruments():
    doc = valid_v2()
    del doc["instruments"]
    doc["universe"] = "NIFTY100"
    s = parse_strategy_dict(doc)
    assert s.universe == "NIFTY100"
    assert s.instruments == ()


def test_instruments_still_supported():
    s = parse_strategy_dict(valid_v2())
    assert s.universe is None
    assert s.instruments == ("NSE:RELIANCE",)


def test_both_universe_and_instruments_is_rejected():
    doc = valid_v2()
    doc["universe"] = "NIFTY100"
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "exactly ONE" in str(exc.value)


def test_neither_universe_nor_instruments_is_rejected():
    doc = valid_v2()
    del doc["instruments"]
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "exactly ONE" in str(exc.value)


def test_universe_name_shape_is_validated():
    doc = valid_v2()
    del doc["instruments"]
    doc["universe"] = "nifty 100!"
    with pytest.raises(StrategyConfigError) as exc:
        parse_strategy_dict(doc)
    assert "universe" in str(exc.value)


def test_universe_round_trips_through_strategy_to_raw():
    doc = valid_v2()
    del doc["instruments"]
    doc["universe"] = "NIFTY100"
    original = parse_strategy_dict(doc)
    raw = strategy_to_raw(original)
    assert raw["universe"] == "NIFTY100"
    assert "instruments" not in raw
    assert parse_strategy_dict(raw) == original


def test_instruments_round_trips_through_strategy_to_raw():
    original = parse_strategy_dict(valid_v2())
    raw = strategy_to_raw(original)
    assert raw["instruments"] == ["NSE:RELIANCE"]
    assert "universe" not in raw
    assert parse_strategy_dict(raw) == original
