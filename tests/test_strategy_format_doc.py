"""The format reference must match the validator.

A reference that drifts from what the parser accepts sends you in circles
debugging strategies that were never going to validate — and this document's
whole job is to be pasted into an external AI tool as ground truth.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from gen_strategy_format_doc import render_document  # noqa: E402
from strategy.parse import parse_strategies  # noqa: E402

from zoneinfo import ZoneInfo as _ZI
UTC_TZ = _ZI("UTC")

DOC_PATH = REPO_ROOT / "docs" / "STRATEGY_FORMAT.md"


def test_the_committed_document_is_up_to_date():
    assert DOC_PATH.read_text(encoding="utf-8") == render_document(), (
        "docs/STRATEGY_FORMAT.md is stale. Regenerate it:\n"
        "    .venv\\Scripts\\python.exe scripts/gen_strategy_format_doc.py"
    )


def test_every_indicator_in_the_vocabulary_is_documented():
    from strategy.vocabulary import INDICATOR_PARAMS

    text = render_document()
    for indicator in INDICATOR_PARAMS:
        assert f"`{indicator}`" in text, f"{indicator} missing from the format doc"


def test_every_operator_is_documented():
    from strategy.vocabulary import ALL_OPERATORS

    text = render_document()
    for operator in ALL_OPERATORS:
        assert operator in text


def test_every_timeframe_is_documented():
    from config import SUPPORTED_TIMEFRAMES

    text = render_document()
    for timeframe in SUPPORTED_TIMEFRAMES:
        assert f"`{timeframe}`" in text


def test_every_sizing_and_stop_type_is_documented():
    from strategy.vocabulary import SIZING_TYPES, STOP_TYPES

    text = render_document()
    for name in set(SIZING_TYPES) | set(STOP_TYPES):
        assert f"`{name}`" in text


def test_the_worked_example_actually_validates():
    """The example is what an AI tool will imitate, so it must be correct.

    It uses `universe: NIFTY100`, which parses on shape alone — universe
    existence is a save-time check against the database, not a parse-time one.
    """
    blocks = re.findall(r"```yaml\n(.*?)```", render_document(), flags=re.DOTALL)
    assert blocks, "the format doc has no YAML example"
    strategies = parse_strategies(yaml.safe_load(blocks[0]))
    assert strategies[0].universe == "NIFTY100"
    assert strategies[0].sizing.type == "notional"
    assert strategies[0].risk.trailing_stop is not None
    assert strategies[0].session.square_off is not None


# ---------------------------------------------------------------------------
# Version 3
# ---------------------------------------------------------------------------
#
# This page is pasted into an external AI tool as ground truth. A stale one
# does not fail loudly — it sends you round in circles debugging strategies
# that were never going to validate, which is exactly the cost these assert
# away.

from gen_strategy_format_doc import render_v3_document  # noqa: E402
from strategy.v3 import parse_machine  # noqa: E402
from strategy.vocabulary import (  # noqa: E402
    CANDLE_FIELDS,
    EXPR_MULTI_OUTPUT,
    EXPR_SIMPLE_INDICATORS,
    HIGHER_TIMEFRAME_PREFIXES,
    POSITION_FIELDS,
)

V3_DOC_PATH = REPO_ROOT / "docs" / "STRATEGY_FORMAT_V3.md"


def v3_doc_text() -> str:
    return V3_DOC_PATH.read_text(encoding="utf-8")


def test_the_committed_v3_document_is_up_to_date():
    assert v3_doc_text() == render_v3_document(), (
        "docs/STRATEGY_FORMAT_V3.md is stale — regenerate it with "
        ".venv\Scripts\python.exe scripts/gen_strategy_format_doc.py"
    )


def test_every_v3_function_is_documented():
    text = v3_doc_text()
    for name in EXPR_SIMPLE_INDICATORS:
        assert f"`{name}(" in text, f"{name} missing from the v3 reference"
    for family, outputs in EXPR_MULTI_OUTPUT.items():
        for output in outputs:
            assert f"{family}.{output}" in text, f"{family}.{output} undocumented"


def test_every_namespace_is_documented():
    text = v3_doc_text()
    for field in CANDLE_FIELDS:
        assert field in text, f"candle.{field} undocumented"
    for field in POSITION_FIELDS:
        assert field in text, f"position.{field} undocumented"
    for prefix in HIGHER_TIMEFRAME_PREFIXES:
        assert f"`{prefix}." in text, f"{prefix}. undocumented"


def test_the_v3_worked_example_actually_validates():
    """The one that would embarrass us: a reference whose own example is
    rejected by the validator it describes."""
    block = re.search(r"```yaml\n(.*?)```", v3_doc_text(), re.DOTALL)
    assert block, "no yaml example found in the v3 reference"
    machine = parse_machine(yaml.safe_load(block.group(1)))
    assert machine.name == "LIQUIDITY-SWEEP-15m"
    assert machine.on_position_closed == "waiting_for_sweep"


def test_the_v3_worked_example_runs_and_can_trade():
    """Validating is not enough — a machine can validate and still be unable
    to reach its own entry."""
    import numpy as np
    import pandas as pd
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from machine_backtest import simulate_machine

    ist = ZoneInfo("Asia/Kolkata")
    block = re.search(r"```yaml\n(.*?)```", v3_doc_text(), re.DOTALL)
    machine = parse_machine(yaml.safe_load(block.group(1)))

    rng = np.random.default_rng(5)
    n = 25 * 30
    closes = 100.0 + np.cumsum(rng.normal(0.0, 1.5, size=n))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    stamps = []
    start = datetime(2026, 3, 2, 9, 15, tzinfo=ist)
    for i in range(n):
        day, slot = divmod(i, 25)
        stamps.append((start + timedelta(days=day, minutes=15 * slot)).astimezone(UTC_TZ))
    df = pd.DataFrame(
        {"open": opens, "high": np.maximum(opens, closes) + 0.8,
         "low": np.minimum(opens, closes) - 0.8, "close": closes,
         "volume": np.full(n, 1000.0)},
        index=pd.DatetimeIndex(stamps, name="ts"),
    )
    result = simulate_machine(
        df, machine, slippage_pct=0.05, cost_per_trade_inr=30.0
    )
    assert result.trades, "the documented example never reaches an entry"
