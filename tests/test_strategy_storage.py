"""Tests for DB-backed strategy storage (the UI's save/load round-trip).

The database is the source of truth for what the engine trades, so a
strategy must survive save -> load -> parse with identical behaviour.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_schema import (  # noqa: E402
    StrategyConfigError,
    load_strategies,
    load_strategy_documents,
    parse_strategy_dict,
    strategy_to_raw,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_load_strategy_documents_returns_validated_raw_dicts() -> None:
    docs = load_strategy_documents(str(REPO_ROOT / "strategies.yaml"))
    assert len(docs) >= 1
    assert docs[0]["name"] == "TF-EMA-RSI-15m-v1"
    # Raw dicts must re-validate through the same code path the engine uses.
    for doc in docs:
        parse_strategy_dict(doc)


def test_round_trip_preserves_behaviour() -> None:
    """Strategy -> raw dict -> Strategy must be identical."""
    original = load_strategies(str(REPO_ROOT / "strategies.yaml"))[0]
    raw = strategy_to_raw(original)
    restored = parse_strategy_dict(raw)
    assert restored == original


def test_round_trip_of_every_indicator_shape() -> None:
    """Covers the fiddly bits: params, source, output, nesting, crosses."""
    doc = {
        "name": "round-trip",
        "enabled": True,
        "position_type": "short",
        "timeframe": "60m",
        "instruments": ["NSE:RELIANCE", "NSE:TCS"],
        "entry": {
            "all": [
                {   # multi-output indicator with explicit output
                    "indicator": "macd",
                    "params": {"fast": 12, "slow": 26, "signal": 9},
                    "output": "histogram",
                    "operator": ">",
                    "value": 0,
                },
                {   # moving average over a non-close source
                    "indicator": "volume",
                    "operator": ">",
                    "compare_to": {
                        "indicator": "sma", "source": "volume", "params": {"period": 20}
                    },
                },
                {   # nested OR group
                    "any": [
                        {
                            "indicator": "ema", "params": {"period": 9},
                            "operator": "crosses_above",
                            "compare_to": {"indicator": "ema", "params": {"period": 21}},
                        },
                        {"indicator": "rsi", "params": {"period": 14},
                         "operator": "<", "value": 30},
                    ]
                },
            ]
        },
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        },
        "sizing": {"type": "fixed_quantity", "quantity": 25},
        "max_cycles_per_day": 3,
    }
    parsed = parse_strategy_dict(doc)
    restored = parse_strategy_dict(strategy_to_raw(parsed))
    assert restored == parsed
    assert restored.sizing.quantity == 25
    assert restored.position_type == "short"


def test_invalid_document_rejected_before_storage() -> None:
    """A malformed strategy must never reach the database."""
    bad = {
        "name": "bad", "enabled": True, "position_type": "long",
        "timeframe": "1m",  # faster than the 5m base is forbidden
        "instruments": ["NSE:RELIANCE"],
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 1.0},
            "target": {"type": "percent", "value": 2.0},
        },
        "sizing": {"type": "fixed_quantity", "quantity": 1},
    }
    with pytest.raises(StrategyConfigError, match="unsupported timeframe"):
        parse_strategy_dict(bad)


def test_ui_built_document_shape_is_accepted() -> None:
    """Exactly what the Strategies page builder produces.

    NOTE: app_pages/strategies.py itself still emits the v1 `stop_loss_pct`/
    `target_pct` keys as of this task — updating the builder's output shape
    is out of scope here (see Task 3's file list). This fixture is written
    in the v2 shape the parser now requires, matching what the builder will
    need to emit once it is updated.
    """
    doc = {
        "name": "MY-STRATEGY-v1",
        "enabled": False,
        "position_type": "long",
        "timeframe": "15m",
        "instruments": ["NSE:RELIANCE", "NSE:TCS"],
        "entry": {"all": [
            {"indicator": "rsi", "params": {"period": 14}, "operator": ">", "value": 50.0}
        ]},
        "exit": {"any": [
            {"indicator": "rsi", "params": {"period": 14}, "operator": "<", "value": 40.0}
        ]},
        "risk": {
            "stop_loss": {"type": "percent", "value": 0.7},
            "target": {"type": "percent", "value": 1.5},
        },
        "sizing": {"type": "fixed_quantity", "quantity": 10},
        "max_cycles_per_day": 2,
    }
    s = parse_strategy_dict(doc)
    assert s.name == "MY-STRATEGY-v1"
    assert s.enabled is False  # builder always creates paused
    assert s.sizing.quantity == 10
