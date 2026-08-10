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

from strategy.parse import StrategyConfigError, parse_strategy_dict  # noqa: E402


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
        # v2 sizing (rupee notional) is Task 4's concern, not this one — use
        # the still-valid v1 shape so these tests isolate risk-block behaviour.
        "sizing": {"type": "fixed_quantity", "quantity": 1},
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
