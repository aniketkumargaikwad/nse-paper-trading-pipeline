"""Unit tests for the strategies.yaml validator.

Pure tests — no network, no database, no env vars needed. Run with:

    python -m pytest tests/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Make the repo root importable when pytest is run from anywhere.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_schema import (  # noqa: E402
    StrategyConfigError,
    load_strategies,
    parse_strategies,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Helpers: a minimal valid document we can mutate per-test.
# ---------------------------------------------------------------------------


def valid_doc() -> dict:
    """Smallest document that passes validation; tests break one thing each."""
    return {
        "version": 2,
        "strategies": [
            {
                "name": "test-strat",
                "enabled": True,
                "position_type": "long",
                "timeframe": "15m",
                "instruments": ["NSE:RELIANCE"],
                "entry": {
                    "all": [
                        {
                            "indicator": "rsi",
                            "params": {"period": 14},
                            "operator": ">",
                            "value": 50,
                        }
                    ]
                },
                "exit": {
                    "any": [
                        {
                            "indicator": "rsi",
                            "params": {"period": 14},
                            "operator": "<",
                            "value": 40,
                        }
                    ]
                },
                "risk": {
                    "stop_loss": {"type": "percent", "value": 0.7},
                    "target": {"type": "percent", "value": 1.5},
                },
                "sizing": {"type": "fixed_quantity", "quantity": 1},
            }
        ],
    }


def expect_error(doc: dict, fragment: str) -> None:
    """Assert parsing fails and the error message contains `fragment`."""
    with pytest.raises(StrategyConfigError) as excinfo:
        parse_strategies(doc)
    assert fragment in str(excinfo.value), (
        f"expected error containing {fragment!r}, got: {excinfo.value}"
    )


# ---------------------------------------------------------------------------
# The shipped strategies.yaml must always be valid.
# ---------------------------------------------------------------------------


def test_shipped_strategies_file_is_valid() -> None:
    strategies = load_strategies(str(REPO_ROOT / "strategies.yaml"))
    assert len(strategies) == 1

    s = strategies[0]
    assert s.name == "TF-EMA-RSI-15m-v1"
    assert s.enabled is True
    assert s.position_type == "long"
    assert s.timeframe == "15m"
    assert len(s.instruments) == 5
    assert s.risk.stop_loss.type == "percent"
    assert s.risk.stop_loss.value == 0.7
    assert s.risk.target.type == "percent"
    assert s.risk.target.value == 1.5
    assert s.max_cycles_per_day == 2

    # Entry: EMA cross AND RSI AND volume filter.
    assert s.entry.logic == "all"
    assert len(s.entry.items) == 3
    cross = s.entry.items[0]
    assert cross.operator == "crosses_above"
    assert cross.right is not None and cross.right.params == {"period": 21}
    vol = s.entry.items[2]
    assert vol.right is not None and vol.right.source == "volume"

    # Exit: OR of cross-down and RSI floor.
    assert s.exit.logic == "any"
    assert len(s.exit.items) == 2


def test_valid_minimal_doc_parses() -> None:
    strategies = parse_strategies(valid_doc())
    assert strategies[0].name == "test-strat"
    assert strategies[0].sizing.quantity == 1
    # max_cycles_per_day is still optional and still defaults.
    assert strategies[0].max_cycles_per_day == 1


def test_missing_sizing_is_rejected() -> None:
    """`sizing` used to default to {fixed_quantity: 1}; it is now required —
    a default here would be a silent opinion about acceptable cost drag
    (see strategy/parse.py:_parse_sizing)."""
    doc = valid_doc()
    del doc["strategies"][0]["sizing"]
    expect_error(doc, "sizing")


# ---------------------------------------------------------------------------
# Loud failures, each pointing at the right location.
# ---------------------------------------------------------------------------


def test_unknown_indicator_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["indicator"] = "emaa"
    expect_error(doc, "unknown indicator 'emaa'")


def test_unknown_operator_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["operator"] = "above"
    expect_error(doc, "unknown operator 'above'")


def test_value_and_compare_to_together_rejected() -> None:
    doc = valid_doc()
    cond = doc["strategies"][0]["entry"]["all"][0]
    cond["compare_to"] = {"indicator": "close"}
    expect_error(doc, "exactly ONE of 'value'")


def test_neither_value_nor_compare_to_rejected() -> None:
    doc = valid_doc()
    del doc["strategies"][0]["entry"]["all"][0]["value"]
    expect_error(doc, "exactly ONE of 'value'")


def test_missing_required_param_rejected() -> None:
    doc = valid_doc()
    del doc["strategies"][0]["entry"]["all"][0]["params"]
    expect_error(doc, "missing required key(s): period")


def test_param_typo_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["params"] = {"perod": 14}
    expect_error(doc, "perod")


def test_bad_timeframe_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["timeframe"] = "1m"  # faster than the 5m base is forbidden
    expect_error(doc, "unsupported timeframe '1m'")


def test_bad_instrument_format_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["instruments"] = ["reliance"]
    expect_error(doc, "EXCHANGE:TRADINGSYMBOL")


def test_empty_instruments_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["instruments"] = []
    expect_error(doc, "non-empty list")


def test_duplicate_strategy_names_rejected() -> None:
    doc = valid_doc()
    doc["strategies"].append(dict(doc["strategies"][0]))
    expect_error(doc, "duplicate strategy name")


def test_negative_stop_loss_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["risk"]["stop_loss"] = {"type": "percent", "value": -1}
    expect_error(doc, "risk.stop_loss")


def test_absurd_stop_loss_rejected() -> None:
    # 70 (percent) is almost certainly a typo for 0.7 — sanity ceiling is 50.
    doc = valid_doc()
    doc["strategies"][0]["risk"]["stop_loss"] = {"type": "percent", "value": 70}
    expect_error(doc, "between 0 and 50")


def test_source_only_allowed_on_moving_averages() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["source"] = "volume"  # on rsi
    expect_error(doc, "'source' is only allowed")


def test_output_rejected_on_single_output_indicator() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["output"] = "line"  # on rsi
    expect_error(doc, "'output' is not applicable")


def test_macd_fast_must_be_less_than_slow() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0] = {
        "indicator": "macd",
        "params": {"fast": 26, "slow": 12, "signal": 9},
        "operator": ">",
        "value": 0,
    }
    expect_error(doc, "'fast' (26) must be < 'slow' (12)")


def test_macd_gets_default_output() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0] = {
        "indicator": "macd",
        "params": {"fast": 12, "slow": 26, "signal": 9},
        "operator": "crosses_above",
        "compare_to": {
            "indicator": "macd",
            "params": {"fast": 12, "slow": 26, "signal": 9},
            "output": "signal",
        },
    }
    s = parse_strategies(doc)[0]
    cond = s.entry.items[0]
    assert cond.left.output == "line"      # default filled in
    assert cond.right.output == "signal"   # explicit respected


def test_nested_condition_groups_parse() -> None:
    doc = valid_doc()
    doc["strategies"][0]["entry"] = {
        "all": [
            {"indicator": "rsi", "params": {"period": 14}, "operator": ">", "value": 50},
            {
                "any": [
                    {"indicator": "close", "operator": ">", "value": 100},
                    {"indicator": "volume", "operator": ">", "value": 1000},
                ]
            },
        ]
    }
    s = parse_strategies(doc)[0]
    assert s.entry.logic == "all"
    nested = s.entry.items[1]
    assert nested.logic == "any"
    assert len(nested.items) == 2


def test_unknown_top_level_strategy_key_rejected() -> None:
    doc = valid_doc()
    doc["strategies"][0]["stop_loss"] = 1  # wrong place: belongs under risk
    expect_error(doc, "unknown key(s): stop_loss")


def test_boolean_period_rejected() -> None:
    # YAML `period: true` must not silently become period=1.
    doc = valid_doc()
    doc["strategies"][0]["entry"]["all"][0]["params"] = {"period": True}
    expect_error(doc, "expected int")


def test_invalid_yaml_syntax_reports_clearly(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nstrategies:\n  - name: x\n   enabled: true\n")
    with pytest.raises(StrategyConfigError) as excinfo:
        load_strategies(str(bad))
    assert "not valid YAML" in str(excinfo.value)


def test_yaml_roundtrip_of_shipped_file(tmp_path: Path) -> None:
    """Editing + re-saving the shipped file with PyYAML must stay valid.

    This guards against the shipped file relying on YAML features that a
    user's editor or a future script would not round-trip.
    """
    original = yaml.safe_load((REPO_ROOT / "strategies.yaml").read_text(encoding="utf-8"))
    resaved = tmp_path / "roundtrip.yaml"
    resaved.write_text(yaml.safe_dump(original), encoding="utf-8")
    strategies = load_strategies(str(resaved))
    assert strategies[0].name == "TF-EMA-RSI-15m-v1"
