"""Tests for the v1 -> v2 strategy document migration. Pure."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy.migrate import MigrationError, migrate_document  # noqa: E402
from strategy.parse import parse_strategies  # noqa: E402


def v1_doc() -> dict:
    return {
        "version": 1,
        "strategies": [
            {
                "name": "old",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": {"all": [{"indicator": "rsi", "params": {"period": 14},
                                   "operator": ">", "value": 50}]},
                "exit": {"any": [{"indicator": "rsi", "params": {"period": 14},
                                  "operator": "<", "value": 40}]},
                "risk": {"stop_loss_pct": 0.7, "target_pct": 1.5},
                "sizing": {"type": "fixed_quantity", "quantity": 2},
                "max_cycles_per_day": 2,
            }
        ],
    }


def test_migrated_document_is_version_2_and_valid():
    out = migrate_document(v1_doc())
    assert out["version"] == 2
    parse_strategies(out)          # must not raise


def test_risk_keys_become_stop_specs():
    out = migrate_document(v1_doc())
    risk = out["strategies"][0]["risk"]
    assert risk["stop_loss"] == {"type": "percent", "value": 0.7}
    assert risk["target"] == {"type": "percent", "value": 1.5}
    assert "stop_loss_pct" not in risk


def test_fixed_quantity_is_carried_over_unchanged():
    out = migrate_document(v1_doc())
    assert out["strategies"][0]["sizing"] == {"type": "fixed_quantity", "quantity": 2}


def test_sizing_absent_in_v1_becomes_the_v1_default_of_one_share():
    doc = v1_doc()
    del doc["strategies"][0]["sizing"]
    out = migrate_document(doc)
    assert out["strategies"][0]["sizing"] == {"type": "fixed_quantity", "quantity": 1}


def test_no_session_block_is_added():
    # v1 had no square-off, so adding one would change behaviour silently.
    out = migrate_document(v1_doc())
    assert "session" not in out["strategies"][0]


def test_instruments_are_untouched():
    out = migrate_document(v1_doc())
    assert out["strategies"][0]["instruments"] == ["NSE:RELIANCE"]


def test_a_v2_document_passes_through_unchanged():
    out = migrate_document(v1_doc())
    assert migrate_document(out) == out


def test_an_unknown_version_is_rejected_by_name():
    with pytest.raises(MigrationError) as exc:
        migrate_document({"version": 7, "strategies": []})
    assert "7" in str(exc.value)


def test_the_input_document_is_not_mutated():
    original = v1_doc()
    migrate_document(original)
    assert original["version"] == 1
    assert original["strategies"][0]["risk"] == {"stop_loss_pct": 0.7, "target_pct": 1.5}
