"""Asking an OpenAI-compatible endpoint instead of the Claude Code CLI."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.brain import (  # noqa: E402
    BrainError,
    BrainStopped,
    ChatApi,
    Claude,
    brain_description,
    chat_payload,
    completions_url,
    make_brain,
)

SCHEMA = {"type": "object", "required": ["a"], "properties": {"a": {"type": "string"}}}

OPENAI_ENV = {
    "MODEL_PROVIDER": "openai",
    "MODEL_BASE_URL": "https://api.deepseek.com/v1",
    "MODEL_API_KEY": "sk-test",
    "MODEL_NAME": "deepseek-chat",
}


class FakeResponse:
    def __init__(self, *, status_code=200, body=None, text=None):
        self.status_code = status_code
        self._body = body
        self.text = text if text is not None else json.dumps(body or {})

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class FakePost:
    """Stands in for requests.post."""

    def __init__(self, response):
        self._response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._response


def reply(text):
    return FakeResponse(body={"choices": [{"message": {"content": text}}]})


def answered(**fields):
    return reply(json.dumps(fields))


def api(response, **kwargs):
    poster = FakePost(response)
    client = ChatApi(base_url="https://api.deepseek.com/v1", api_key="sk-test",
                     model="deepseek-chat", poster=poster, **kwargs)
    return client, poster


# --- the request -----------------------------------------------------------

@pytest.mark.parametrize("base, expected", [
    ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
    ("https://api.deepseek.com/v1/", "https://api.deepseek.com/v1/chat/completions"),
    ("https://x.test/v1/chat/completions", "https://x.test/v1/chat/completions"),
])
def test_the_completions_path_is_added_once(base, expected):
    assert completions_url(base) == expected


def test_the_schema_travels_in_the_user_turn_not_the_system_one():
    """Same split as the CLI call: instruction on -p, schema on stdin."""
    body = chat_payload("do it", "context\n\nschema here",
                        model="m", max_tokens=100, json_mode=False)
    system, user = body["messages"]
    assert system == {"role": "system", "content": "do it"}
    assert "schema here" in user["content"]
    assert user["role"] == "user"


def test_json_mode_is_off_unless_asked_for():
    """Not every compatible endpoint supports response_format."""
    plain = chat_payload("i", "r", model="m", max_tokens=10, json_mode=False)
    asked = chat_payload("i", "r", model="m", max_tokens=10, json_mode=True)
    assert "response_format" not in plain
    assert asked["response_format"] == {"type": "json_object"}


def test_the_key_is_sent_as_a_bearer_token():
    client, poster = api(answered(a="yes"))
    client.ask("prompt", SCHEMA)
    url, kwargs = poster.calls[0]
    assert url == "https://api.deepseek.com/v1/chat/completions"
    assert kwargs["headers"]["Authorization"] == "Bearer sk-test"
    assert kwargs["json"]["model"] == "deepseek-chat"
    assert kwargs["json"]["stream"] is False


# --- the reply -------------------------------------------------------------

def test_a_plain_json_reply_is_read():
    client, _ = api(answered(a="yes"))
    assert client.ask("prompt", SCHEMA) == {"a": "yes"}


def test_a_fenced_reply_is_read():
    client, _ = api(reply('```json\n{"a": "yes"}\n```'))
    assert client.ask("prompt", SCHEMA) == {"a": "yes"}


def test_a_missing_required_field_is_an_error():
    client, _ = api(answered(b="no"))
    with pytest.raises(BrainError):
        client.ask("prompt", SCHEMA)


def test_an_empty_reply_is_an_error():
    client, _ = api(reply(""))
    with pytest.raises(BrainError):
        client.ask("prompt", SCHEMA)


# --- failures --------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 402, 403])
def test_a_dead_key_ends_the_day_rather_than_retrying(status):
    client, _ = api(FakeResponse(status_code=status, body={"error": "nope"}))
    with pytest.raises(BrainStopped) as seen:
        client.ask("prompt", SCHEMA)
    assert "MODEL_API_KEY" in str(seen.value)


def test_a_spent_balance_ends_the_day():
    client, _ = api(FakeResponse(
        status_code=429, text='{"error": "Insufficient credit balance"}'))
    with pytest.raises(BrainStopped):
        client.ask("prompt", SCHEMA)


def test_a_server_error_is_retryable():
    """BrainError, so OpusBrain's three attempts still apply."""
    client, _ = api(FakeResponse(status_code=500, text="upstream exploded"))
    with pytest.raises(BrainError):
        client.ask("prompt", SCHEMA)


def test_an_unreachable_endpoint_is_retryable():
    def boom(*_, **__):
        raise OSError("connection refused")

    client = ChatApi(base_url="https://x.test", api_key="k", model="m", poster=boom)
    with pytest.raises(BrainError):
        client.ask("prompt", SCHEMA)


def test_an_html_error_page_is_retryable():
    client, _ = api(FakeResponse(status_code=200, body=None, text="<html>502</html>"))
    with pytest.raises(BrainError):
        client.ask("prompt", SCHEMA)


# --- choosing the client ---------------------------------------------------

def test_no_setting_keeps_the_claude_cli():
    assert isinstance(make_brain({}), Claude)


def test_the_openai_setting_gives_the_http_client():
    assert isinstance(make_brain(OPENAI_ENV), ChatApi)


@pytest.mark.parametrize("missing", ["MODEL_BASE_URL", "MODEL_API_KEY", "MODEL_NAME"])
def test_a_missing_setting_stops_the_day_by_name(missing):
    env = dict(OPENAI_ENV)
    env.pop(missing)
    with pytest.raises(BrainStopped) as seen:
        make_brain(env)
    assert missing in str(seen.value)


def test_an_unknown_provider_stops_the_day():
    with pytest.raises(BrainStopped):
        make_brain({"MODEL_PROVIDER": "telepathy"})


def test_the_header_line_names_the_endpoint_actually_used():
    assert "opus" in brain_description({})
    line = brain_description(OPENAI_ENV)
    assert "api.deepseek.com" in line and "deepseek-chat" in line
