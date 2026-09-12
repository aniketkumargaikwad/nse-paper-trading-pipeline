"""Calling Claude Code headlessly, and reading what comes back."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.brain import BrainError, BrainStopped, Claude, build_command  # noqa: E402

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}


class FakeRun:
    """Stands in for subprocess.run."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self


def envelope(structured=None, result="ok"):
    body = {"result": result, "session_id": "s1", "total_cost_usd": 0.12}
    if structured is not None:
        body["structured_output"] = structured
    return json.dumps(body)


def test_the_command_removes_every_tool():
    command = build_command("do it", SCHEMA, model="opus")
    assert "--disallowed-tools" in command
    assert command[command.index("--disallowed-tools") + 1] == "*"
    assert "--safe-mode" in command
    assert "--bare" not in command          # bare mode refuses the Pro login


def test_the_command_asks_for_the_schema_and_json():
    command = build_command("do it", SCHEMA, model="opus")
    assert command[command.index("--output-format") + 1] == "json"
    assert json.loads(command[command.index("--json-schema") + 1]) == SCHEMA


def test_the_command_names_the_model_and_allows_one_turn():
    command = build_command("do it", SCHEMA, model="opus")
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--max-turns") + 1] == "1"


def test_a_structured_reply_comes_back_as_a_dict():
    run = FakeRun(stdout=envelope({"a": "hello"}))
    assert Claude(runner=run).ask("prompt", SCHEMA) == {"a": "hello"}


def test_the_prompt_is_piped_on_stdin():
    run = FakeRun(stdout=envelope({"a": "hello"}))
    Claude(runner=run).ask("a very long context", SCHEMA)
    assert run.calls[0][1]["input"] == "a very long context"


def test_a_reply_without_structured_output_is_an_error():
    run = FakeRun(stdout=envelope(None, result="I cannot do that"))
    with pytest.raises(BrainError, match="no structured output"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_unreadable_output_is_an_error_that_quotes_it():
    run = FakeRun(stdout="not json at all")
    with pytest.raises(BrainError, match="not json at all"):
        Claude(runner=run).ask("prompt", SCHEMA)


@pytest.mark.parametrize("text", [
    "Claude usage limit reached. Your limit will reset at 12:40pm",
    "OAuth token has expired. Please run /login",
    "Invalid API key · Please run /login",
    "Failed to authenticate: OAuth session expired and could not be refreshed",
])
def test_a_usage_or_auth_failure_stops_the_day(text):
    run = FakeRun(stdout=envelope(None, result=text), returncode=1)
    with pytest.raises(BrainStopped):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_an_auth_failure_says_how_to_fix_it():
    """A credential problem has one fix, and the message should name it."""
    run = FakeRun(
        stdout=envelope(None, result="Failed to authenticate: OAuth session expired"),
        returncode=1,
    )
    with pytest.raises(BrainStopped, match="claude setup-token"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_a_usage_limit_message_is_not_buried_in_login_advice():
    run = FakeRun(stdout=envelope(None, result="Claude usage limit reached"), returncode=1)
    with pytest.raises(BrainStopped) as caught:
        Claude(runner=run).ask("prompt", SCHEMA)
    assert "setup-token" not in str(caught.value)


def test_any_other_failure_is_a_plain_error():
    run = FakeRun(stdout="", stderr="something broke", returncode=2)
    with pytest.raises(BrainError, match="something broke"):
        Claude(runner=run).ask("prompt", SCHEMA)
