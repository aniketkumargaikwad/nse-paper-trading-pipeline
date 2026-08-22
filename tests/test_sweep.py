"""Sweep expansion. Pure — no network, no database, no clock.

The dangerous failure for a sweep is not a crash; it is producing variants
that differ from what was asked for, because every number downstream would
then be attributed to the wrong parameters. So these tests check the CONTENT
of each variant, not just how many came back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_schema import parse_strategy_dict, strategy_to_raw  # noqa: E402
from sweep import (  # noqa: E402
    SweepError,
    apply_override,
    count_variants,
    expand,
    false_positive_warning,
    parse_spec,
    variant_name,
)


def base_document() -> dict:
    return {
        "name": "EMA-RSI",
        "enabled": True,
        "position_type": "long",
        "timeframe": "60m",
        "universe": "NIFTY50",
        "entry": {
            "all": [
                {
                    "indicator": "ema",
                    "params": {"period": 9},
                    "operator": "crosses_above",
                    "compare_to": {"indicator": "ema", "params": {"period": 21}},
                },
                {
                    "indicator": "rsi",
                    "params": {"period": 14},
                    "operator": ">",
                    "value": 50,
                },
            ]
        },
        "exit": {
            "any": [
                {
                    "indicator": "rsi",
                    "params": {"period": 14},
                    "operator": "<",
                    "value": 45,
                }
            ]
        },
        "sizing": {"type": "notional", "notional_per_trade": 100000},
        "risk": {
            "stop_loss": {"type": "percent", "value": 0.7},
            "target": {"type": "percent", "value": 1.5},
        },
    }


def base_strategy():
    return parse_strategy_dict(base_document(), where="test")


# ---------------------------------------------------------------------------
# Reading a specification
# ---------------------------------------------------------------------------


def test_values_keep_their_yaml_types():
    """`14` must be an int, not the string "14".

    The parser requires an int period. A string would be rejected with a
    message about the indicator, sending the reader to look at their strategy
    rather than at their sweep.
    """
    path, values = parse_spec("entry.all.1.params.period=10,14,21")
    assert path == ["entry", "all", 1, "params", "period"]
    assert values == [10, 14, 21]
    assert all(isinstance(v, int) for v in values)

    _, floats = parse_spec("risk.stop_loss.value=0.5,0.7")
    assert floats == [0.5, 0.7]


def test_a_specification_without_an_equals_sign_is_refused():
    with pytest.raises(SweepError, match="path=value1,value2"):
        parse_spec("risk.stop_loss.value")


def test_a_repeated_value_is_refused():
    """Running the same variant twice costs minutes and proves nothing."""
    with pytest.raises(SweepError, match="twice"):
        parse_spec("risk.stop_loss.value=0.5,0.7,0.5")


def test_an_empty_value_is_refused():
    with pytest.raises(SweepError, match="empty value"):
        parse_spec("risk.stop_loss.value=0.5,,0.7")


# ---------------------------------------------------------------------------
# Applying one override
# ---------------------------------------------------------------------------


def test_an_override_does_not_touch_the_original():
    """Every variant is built from the same base document.

    If an override mutated it, the second variant would inherit the first
    one's values — producing plausible results attributed to the wrong
    parameters, which is far worse than an error.
    """
    document = base_document()
    changed = apply_override(document, ["risk", "stop_loss", "value"], 1.2)

    assert changed["risk"]["stop_loss"]["value"] == 1.2
    assert document["risk"]["stop_loss"]["value"] == 0.7


def test_an_override_reaches_into_a_list():
    changed = apply_override(
        base_document(), ["entry", "all", 1, "params", "period"], 21
    )
    assert changed["entry"]["all"][1]["params"]["period"] == 21
    # The sibling condition is untouched.
    assert changed["entry"]["all"][0]["params"]["period"] == 9


def test_sweeping_a_key_that_does_not_exist_is_refused():
    """A typo must not silently add a key.

    `stop_los` would either be rejected by the parser with a message about the
    strategy, or accepted somewhere unvalidated and change nothing at all —
    and a sweep that changes nothing produces N identical results that look
    like a stable edge.
    """
    with pytest.raises(SweepError, match="no key 'stop_los'"):
        apply_override(base_document(), ["risk", "stop_los", "value"], 1.0)


def test_an_index_past_the_end_is_refused():
    with pytest.raises(SweepError, match="past the end"):
        apply_override(base_document(), ["entry", "all", 7, "params", "period"], 5)


# ---------------------------------------------------------------------------
# The grid
# ---------------------------------------------------------------------------


def test_expansion_is_the_full_cartesian_product():
    """3 stops x 2 targets = 6 variants, each a distinct pair."""
    variants = expand(
        base_strategy(),
        ["risk.stop_loss.value=0.5,0.7,1.0", "risk.target.value=1.0,2.0"],
    )
    assert len(variants) == 6

    pairs = {
        (v.risk.stop_loss.value, v.risk.target.value) for v in variants
    }
    assert pairs == {
        (0.5, 1.0), (0.5, 2.0),
        (0.7, 1.0), (0.7, 2.0),
        (1.0, 1.0), (1.0, 2.0),
    }


def test_every_variant_is_named_for_what_makes_it_different():
    """Names are the only handle on a variant in the results table."""
    variants = expand(base_strategy(), ["risk.stop_loss.value=0.5,1.0"])
    names = sorted(v.name for v in variants)
    assert names == [
        "EMA-RSI [stop_loss.value=0.5]",
        "EMA-RSI [stop_loss.value=1.0]",
    ]
    # Distinct, or two variants would collide into one row.
    assert len(set(names)) == len(names)


def test_variants_differ_only_in_the_swept_parameter():
    """Everything not swept must survive expansion unchanged."""
    original = base_strategy()
    variants = expand(original, ["risk.stop_loss.value=0.5,1.0"])

    for variant in variants:
        assert variant.timeframe == original.timeframe
        assert variant.universe == original.universe
        assert variant.position_type == original.position_type
        assert variant.risk.target.value == original.risk.target.value
        assert (
            variant.sizing.notional_per_trade
            == original.sizing.notional_per_trade
        )
        # The entry rules survive the round trip through raw form.
        assert strategy_to_raw(variant)["entry"] == strategy_to_raw(original)["entry"]


def test_no_specs_means_the_strategy_itself():
    original = base_strategy()
    assert expand(original, []) == [original]


def test_sweeping_one_parameter_twice_is_refused():
    with pytest.raises(SweepError, match="swept twice"):
        expand(
            base_strategy(),
            ["risk.stop_loss.value=0.5,0.7", "risk.stop_loss.value=1.0,1.2"],
        )


def test_an_invalid_combination_fails_before_the_run_not_during_it():
    """A stop of 70% is the classic 0.7 typo, and the parser knows it.

    Catching it here costs nothing. Catching it after the candles are fetched
    wastes the whole run.
    """
    with pytest.raises(Exception) as caught:
        expand(base_strategy(), ["risk.stop_loss.value=0.7,70"])
    assert "stop" in str(caught.value).lower()


def test_counting_variants_needs_no_strategy():
    assert count_variants(["a.b=1,2,3", "c.d=4,5"]) == 6
    assert count_variants([]) == 1


# ---------------------------------------------------------------------------
# Saying what the sweep cost in certainty
# ---------------------------------------------------------------------------


def test_the_false_positive_warning_states_a_probability():
    """Worked by hand: 1 - 0.95^16 = 0.5599, which rounds to 56%."""
    message = false_positive_warning(16, kill_rule_threshold=0.05)
    assert "Across 16 combinations" in message
    assert "56%" in message


def test_a_single_variant_carries_no_warning():
    """One run is not a search, and a warning that always fires is ignored."""
    assert false_positive_warning(1) == ""


def test_more_combinations_means_a_higher_stated_chance():
    def pct(text: str) -> int:
        return int(text.rsplit("about ", 1)[1].split("%")[0])

    assert pct(false_positive_warning(4)) < pct(false_positive_warning(40))


def test_variant_name_shortens_a_deep_path_without_losing_it():
    name = variant_name("S", [("entry.all.0.params.period", 9)])
    assert name == "S [params.period=9]"
