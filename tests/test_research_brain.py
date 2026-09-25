"""Calling Claude Code headlessly, and reading what comes back."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.brain import (  # noqa: E402
    BrainError,
    BrainStopped,
    Claude,
    build_command,
)

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}


class FakeRun:
    """Stands in for subprocess.run."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return self


def envelope(result="ok"):
    return json.dumps({"result": result, "session_id": "s1", "total_cost_usd": 0.12})


def answered(**fields):
    """What Claude replies with: the JSON object as plain text."""
    return envelope(json.dumps(fields))


def test_the_command_removes_every_tool():
    command = build_command("do it", model="opus")
    assert "--disallowed-tools" in command
    assert command[command.index("--disallowed-tools") + 1] == "*"
    assert "--safe-mode" in command
    assert "--bare" not in command          # bare mode refuses the Pro login


def test_the_command_asks_for_json_in_words_not_with_the_schema_flag():
    """--json-schema is a TOOL, and the denylist blocks it. See brain.py."""
    command = build_command("do it", model="opus")
    assert command[command.index("--output-format") + 1] == "json"
    assert "--json-schema" not in command
    assert command[command.index("-p") + 1] == "do it"


def test_the_schema_travels_on_stdin_not_in_an_argument():
    """A brace-heavy argument loses later flags on Windows. See brain.py."""
    run = FakeRun(stdout=answered(a="hello"))
    Claude(runner=run).ask("the context", SCHEMA)
    command, kwargs = run.calls[0]
    assert json.dumps(SCHEMA) in kwargs["input"]
    assert not any(json.dumps(SCHEMA) in part for part in command)


def test_the_command_names_the_model_and_caps_the_turns():
    command = build_command("do it", model="opus")
    assert command[command.index("--model") + 1] == "opus"
    assert command[command.index("--max-turns") + 1] == "2"


def test_a_json_reply_comes_back_as_a_dict():
    run = FakeRun(stdout=answered(a="hello"))
    assert Claude(runner=run).ask("prompt", SCHEMA) == {"a": "hello"}


def test_a_fenced_reply_is_still_read():
    run = FakeRun(stdout=envelope('```json\n{"a": "hello"}\n```'))
    assert Claude(runner=run).ask("prompt", SCHEMA) == {"a": "hello"}


def test_a_reply_wrapped_in_chat_is_still_read():
    run = FakeRun(stdout=envelope('Sure! {"a": "hello"} — hope that helps.'))
    assert Claude(runner=run).ask("prompt", SCHEMA) == {"a": "hello"}


def test_the_prompt_is_piped_on_stdin():
    run = FakeRun(stdout=answered(a="hello"))
    Claude(runner=run).ask("a very long context", SCHEMA)
    assert run.calls[0][1]["input"].startswith("a very long context")


def test_a_reply_missing_a_required_field_is_an_error_that_names_it():
    run = FakeRun(stdout=answered(b="hello"))
    with pytest.raises(BrainError, match="missing a"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_a_reply_that_is_not_json_is_an_error_that_quotes_it():
    run = FakeRun(stdout=envelope("I cannot do that"))
    with pytest.raises(BrainError, match="I cannot do that"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_an_empty_reply_says_why_it_was_empty():
    run = FakeRun(stdout=json.dumps({"result": "", "subtype": "error_max_turns"}))
    with pytest.raises(BrainError, match="error_max_turns"):
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
    run = FakeRun(stdout=envelope(text), returncode=1)
    with pytest.raises(BrainStopped):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_an_auth_failure_says_how_to_fix_it():
    """A credential problem has one fix, and the message should name it."""
    run = FakeRun(
        stdout=envelope("Failed to authenticate: OAuth session expired"),
        returncode=1,
    )
    with pytest.raises(BrainStopped, match="claude setup-token"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_a_usage_limit_message_is_not_buried_in_login_advice():
    run = FakeRun(stdout=envelope("Claude usage limit reached"), returncode=1)
    with pytest.raises(BrainStopped) as caught:
        Claude(runner=run).ask("prompt", SCHEMA)
    assert "setup-token" not in str(caught.value)


def test_any_other_failure_is_a_plain_error():
    run = FakeRun(stdout="", stderr="something broke", returncode=2)
    with pytest.raises(BrainError, match="something broke"):
        Claude(runner=run).ask("prompt", SCHEMA)


def test_the_day_asks_opus_5_5_by_its_exact_name():
    """An alias means whatever the pinned CLI thinks the latest Opus is; the
    owner asked for 5.5 (25 Sep 2026), so it is named outright."""
    run = FakeRun(stdout=answered(a="x"))
    Claude(runner=run).ask("p", SCHEMA)
    command = run.calls[0][0]
    assert command[command.index("--model") + 1] == "claude-opus-5-5"
