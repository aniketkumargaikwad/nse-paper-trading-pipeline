"""The version loop: limits, repairs, decisions and the time budget."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.checker import CheckError  # noqa: E402
from research.loop import MAX_VERSIONS, REPAIR_ATTEMPTS, run_versions  # noqa: E402

GOOD_YAML = "a strategy"


class FakeBrain:
    def __init__(self, proposals, reviews):
        self.proposals, self.reviews = list(proposals), list(reviews)
        self.asked = []

    def propose(self, **kwargs):
        self.asked.append(("propose", kwargs))
        return self.proposals.pop(0)

    def review(self, **kwargs):
        self.asked.append(("review", kwargs))
        return self.reviews.pop(0)


def proposal(title="dip", yaml_text=GOOD_YAML, change=""):
    return {"title": title, "description": "d", "hypothesis": "h",
            "strategy_yaml": yaml_text, "change_note": change}


def review(decision="stop", hint=""):
    return {"why_failed": "f", "why_worked": "w", "lessons": "l",
            "decision": decision, "change_hint": hint}


def fake_check(strategy_yaml, **kwargs):
    if strategy_yaml == "broken":
        raise CheckError("entry: unknown indicator 'foo'")
    return f"checked:{strategy_yaml}"


def fake_test(checked, version_no):
    return f"summary-for-{checked}-v{version_no}"


def run(brain, **kwargs):
    return run_versions(
        brain=brain, check=fake_check, test=fake_test, day=date(2026, 9, 12), **kwargs
    )


def test_one_version_then_stop():
    brain = FakeBrain([proposal()], [review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 1
    assert outcome.versions[0].valid and outcome.stopped_because == "stop"


def test_the_tested_version_keeps_its_summary_and_review():
    brain = FakeBrain([proposal()], [review("stop")])
    version = run(brain).versions[0]
    assert version.summary == "summary-for-checked:a strategy-v1"
    assert version.review["lessons"] == "l"


def test_next_version_keeps_the_idea_and_passes_the_hint():
    brain = FakeBrain([proposal(), proposal(change="traded less")],
                      [review("next_version", hint="trade less"), review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 2
    assert outcome.versions[1].idea_no == 1 and outcome.versions[1].version_no == 2
    assert brain.asked[2][1]["change_hint"] == "trade less"


def test_a_new_idea_starts_a_fresh_idea_number():
    brain = FakeBrain([proposal("a"), proposal("b")],
                      [review("new_idea"), review("stop")])
    outcome = run(brain)
    assert [v.idea_no for v in outcome.versions] == [1, 2]
    assert outcome.versions[1].version_no == 1
    assert outcome.ideas_dropped == 1


def test_an_invalid_proposal_is_repaired_without_costing_a_version():
    brain = FakeBrain([proposal(yaml_text="broken"), proposal()], [review("stop")])
    outcome = run(brain)
    assert len(outcome.versions) == 1 and outcome.versions[0].valid
    assert outcome.repairs == 1


def test_the_repair_attempt_is_told_what_was_wrong():
    brain = FakeBrain([proposal(yaml_text="broken"), proposal()], [review("stop")])
    run(brain)
    assert "unknown indicator" in brain.asked[1][1]["error"]


def test_a_proposal_that_stays_broken_becomes_a_failed_version():
    """It still counts toward the seven: an idea Opus cannot express is a result."""
    brain = FakeBrain(
        [proposal(yaml_text="broken")] * (REPAIR_ATTEMPTS + 1) + [proposal()],
        [review("stop")],
    )
    outcome = run(brain)
    assert len(outcome.versions) == 2
    assert outcome.versions[0].valid is False and "foo" in outcome.versions[0].error
    assert outcome.versions[1].valid is True


def test_seven_versions_is_the_ceiling():
    brain = FakeBrain([proposal()] * MAX_VERSIONS, [review("next_version")] * MAX_VERSIONS)
    outcome = run(brain)
    assert len(outcome.versions) == MAX_VERSIONS
    assert outcome.stopped_because == "version_limit"


def test_the_last_review_is_told_no_versions_remain():
    brain = FakeBrain([proposal()] * MAX_VERSIONS, [review("next_version")] * MAX_VERSIONS)
    run(brain)
    last_review = [c for c in brain.asked if c[0] == "review"][-1]
    assert last_review[1]["versions_left"] == 0


def test_the_time_budget_stops_before_starting_another_version():
    clock = iter([0.0, 100.0, 4000.0, 4000.0, 4000.0])
    brain = FakeBrain([proposal()] * 3, [review("next_version")] * 3)
    outcome = run(brain, budget_seconds=3600, now=lambda: next(clock))
    assert outcome.stopped_because == "time_budget"
    assert len(outcome.versions) == 1


def test_a_max_versions_override_is_respected_for_cheap_live_tests():
    brain = FakeBrain([proposal()] * 2, [review("next_version")] * 2)
    outcome = run(brain, max_versions=2)
    assert len(outcome.versions) == 2 and outcome.stopped_because == "version_limit"
