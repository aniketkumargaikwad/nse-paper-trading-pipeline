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
