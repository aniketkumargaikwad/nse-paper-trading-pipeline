"""Ask the model, with no tools and a fixed answer shape.

Two transports live here and the loop cannot tell them apart: `Claude`
shells out to the Claude Code CLI on the Pro plan (the default, described
below), and `ChatApi` posts to any OpenAI-compatible `/chat/completions`
endpoint. `make_brain` picks between them from MODEL_PROVIDER.

Every call is one non-interactive `claude -p` with EVERY tool removed, so
Claude cannot read a file, run a command or reach the network. That is what
makes design 2.4 enforceable: it knows only what the prompt builder chose to
put in front of it.

`--safe-mode` (not `--bare`) is deliberate: both skip local configuration, but
bare mode refuses the subscription login and demands an API key, and this
system runs on the Pro plan.

The answer shape is asked for in words rather than with `--json-schema`, and
that is forced, not a preference. Measured against Claude Code 2.1.251 on
2026-09-12:

* `--json-schema` is implemented as a TOOL named StructuredOutput, so
  `--disallowed-tools "*"` blocks it and every call dies with no output.
* Adding `--allowed-tools StructuredOutput` does not rescue it: the denylist
  wins, and the tool is still refused.
* `--permission-mode dontAsk` does NOT stand in for the denylist. Asked to
  run a shell command with the tools present, Claude ran it and read the
  working directory. "dontAsk" means it will not stop to ask, not that it
  will refuse.

So the denylist stays, the answer shape is asked for in words, and the reply
is parsed here. Verified the same day: with `--disallowed-tools "*"` Claude
reports it has no tools at all and answers in one turn.

Re-verified on 2026-09-25, on a GitHub runner, before the pin moved to
2.1.282 for MODEL: through `Claude().ask`, claude-opus-5-5 answered, parsed,
and listed its tools as `[]`. Told to run `ls` and quote a line from
research/journal/, it returned nothing from the repository - one turn, no
permission denials, no error.

The schema text goes on STDIN, never in the `-p` argument. On Windows the CLI
is a `claude.CMD` shim, so every argument passes through cmd.exe, and an
argument full of braces and quotes comes out re-split: the prompt still
arrived, but `--output-format json` was silently lost and the reply came back
as markdown instead of an envelope. Stdin is not parsed by anything, so
nothing can be mangled there.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

TIMEOUT_SECONDS = 900
# The owner's choice (25 Sep 2026): the newest Opus, named exactly. Not the
# alias `opus`: an alias means whatever the PINNED CLI thinks the latest Opus
# is, so it only moves when .github/workflows/research.yml bumps the CLI -
# and 2.1.251 did not know this model at all (it logged `unrecognized_model`).
MODEL = "claude-opus-5-5"
# With no tools there is nothing to iterate on, so one turn is the answer.
# The second is headroom for a turn spent on a preamble.
MAX_TURNS = 2

JSON_RULE = (
    "Reply with ONLY a JSON object - no prose, no explanation, no code fence - "
    "matching this JSON Schema exactly:"
)

# Failures that end the day rather than the version: no retry can fix them.
_STOP_MARKERS = (
    "usage limit",
    "rate limit",
    "oauth token has expired",
    "oauth session expired",
    "failed to authenticate",
    "invalid api key",
    "please run /login",
    "credit balance",
)

# Shown when the credential is the problem. A nested `claude -p` cannot always
# refresh the interactive login, and an unattended run has nobody to log in, so
# both the laptop and GitHub Actions want a long-lived token instead.
AUTH_HELP = (
    "Claude Code could not authenticate. Generate a long-lived token by running "
    "`claude setup-token` in a terminal, then set CLAUDE_CODE_OAUTH_TOKEN to it "
    "(in .env locally, or as a GitHub secret)."
)


class BrainError(RuntimeError):
    """One call failed in a way the loop may retry or record."""


class BrainStopped(RuntimeError):
    """The day cannot continue: usage limit, or no valid credential."""


def _said(stdout: str) -> str:
    """Claude's own words, whether the reply was an envelope or bare text."""
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return stdout.strip()
    if isinstance(envelope, dict):
        return str(envelope.get("result") or "").strip()
    return stdout.strip()


def answer_request(prompt: str, schema: dict[str, Any]) -> str:
    """What goes on stdin: the context, then the shape the answer must take.

    The schema belongs here rather than beside `-p`; see the module docstring
    for what Windows does to a brace-heavy argument.
    """
    return f"{prompt}\n\n{JSON_RULE}\n{json.dumps(schema)}"


def read_answer(said: str, schema: dict[str, Any]) -> dict[str, Any]:
    """The JSON object Claude replied with, fence or no fence.

    A reply that is not usable raises, and the message quotes what came back,
    because that message is what the caller shows a human at 6am.
    """
    text = said.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rstrip().removesuffix("```")
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise BrainError(f"the reply was not JSON: {said[:400]}")
    try:
        value = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise BrainError(f"the reply was not valid JSON: {said[:400]}") from exc
    missing = [key for key in schema.get("required", ()) if key not in value]
    if missing:
        raise BrainError(f"the reply is missing {', '.join(missing)}: {said[:400]}")
    return value


def claude_executable() -> str:
    """Where the Claude Code CLI is, honouring CLAUDE_CLI if set."""
    return os.environ.get("CLAUDE_CLI") or shutil.which("claude") or "claude"


def build_command(instruction: str, *, model: str) -> list[str]:
    """The exact call. Every flag here is load-bearing; see the module docstring."""
    return [
        claude_executable(),
        # Plain words only. Nothing brace-heavy or quote-heavy goes in an
        # argument on Windows - that is what stdin is for.
        "-p", instruction,
        "--safe-mode",
        # The whole of design 2.4 is this one flag. See the module docstring
        # for what was measured when it was loosened.
        "--disallowed-tools", "*",
        # Belt and braces: with every tool removed nothing can raise a prompt
        # anyway, and an unattended run has nobody to answer one.
        "--permission-mode", "dontAsk",
        "--model", model,
        "--max-turns", str(MAX_TURNS),
        "--output-format", "json",
    ]


class Claude:
    """One `claude -p` call per ask. No session is carried between calls."""

    def __init__(
        self,
        *,
        model: str = MODEL,
        runner: Callable[..., Any] = subprocess.run,
        timeout: int = TIMEOUT_SECONDS,
    ) -> None:
        self._model, self._runner, self._timeout = model, runner, timeout

    def ask(
        self, prompt: str, schema: dict[str, Any], *, instruction: str = "Follow the input."
    ) -> dict[str, Any]:
        """Send `prompt` on stdin, return the structured reply as a dict."""
        command = build_command(instruction, model=self._model)
        completed = self._runner(
            command, input=answer_request(prompt, schema), capture_output=True, text=True,
            timeout=self._timeout, encoding="utf-8",
        )
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()

        lowered = f"{stdout}\n{stderr}".lower()
        if any(marker in lowered for marker in _STOP_MARKERS):
            said = _said(stdout) or stderr or "Claude refused the call"
            needs_login = any(
                marker in lowered
                for marker in ("authenticate", "oauth", "login", "invalid api key")
            )
            raise BrainStopped(f"{said}\n{AUTH_HELP}" if needs_login else said)

        if completed.returncode != 0 and not stdout:
            raise BrainError(f"claude exited {completed.returncode}: {stderr or 'no output'}")

        try:
            envelope = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise BrainError(f"could not read the envelope as JSON: {stdout[:400]}") from exc

        said = str(envelope.get("result") or "").strip()
        if not said:
            raise BrainError(
                "Claude returned nothing"
                f" ({envelope.get('subtype') or envelope.get('terminal_reason') or 'no reason given'})"
            )
        return read_answer(said, schema)


# ---------------------------------------------------------------------------
# The other way to ask: a plain HTTP chat-completions endpoint
# ---------------------------------------------------------------------------
#
# Nothing above this line is Anthropic-specific except the transport. The two
# asks the loop makes (propose, review) are single-turn, send no tools, need
# no prompt caching, and fit in about 25 KB of prompt - so any OpenAI-shaped
# `/chat/completions` endpoint can serve them: DeepSeek, OpenAI, Together,
# OpenRouter, a local server. The answer shape is already asked for in words
# and parsed by `read_answer`, which is what makes the swap this small.

DEFAULT_MAX_TOKENS = 8000

API_AUTH_HELP = (
    "The model endpoint refused the credential. Check MODEL_API_KEY and "
    "MODEL_BASE_URL (in .env locally, or as GitHub secrets/variables)."
)


def completions_url(base_url: str) -> str:
    """The chat endpoint for a base like `https://api.deepseek.com/v1`.

    A base that already names the path is left alone, so either spelling of
    the setting works.
    """
    base = base_url.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def chat_payload(
    instruction: str, request: str, *, model: str, max_tokens: int, json_mode: bool
) -> dict[str, Any]:
    """The request body. `instruction` is the system turn, `request` the user one.

    That split mirrors the CLI call above, where the instruction rides on `-p`
    and the context and schema ride on stdin.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": instruction},
            {"role": "user", "content": request},
        ],
        "max_tokens": max_tokens,
        "stream": False,
    }
    if json_mode:
        # Supported by OpenAI and DeepSeek; NOT universal, which is why it is
        # off unless MODEL_JSON_MODE says otherwise. `read_answer` copes
        # without it, so the default path is the one already proven.
        body["response_format"] = {"type": "json_object"}
    return body


def _api_said(body: dict[str, Any]) -> str:
    """The assistant's words, from an OpenAI-shaped response body."""
    choices = body.get("choices") or []
    if not choices:
        return ""
    message = (choices[0] or {}).get("message") or {}
    return str(message.get("content") or "").strip()


class ChatApi:
    """One HTTP call per ask, against an OpenAI-compatible endpoint.

    Same surface as `Claude` - `ask(prompt, schema, instruction=...)` - so the
    loop cannot tell which one it is holding.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
        poster: Callable[..., Any] | None = None,
        timeout: int = TIMEOUT_SECONDS,
    ) -> None:
        self._url = completions_url(base_url)
        self._key, self._model = api_key, model
        self._max_tokens, self._json_mode = max_tokens, json_mode
        self._timeout = timeout
        self._poster = poster

    def _post(self, *args: Any, **kwargs: Any) -> Any:
        if self._poster is not None:
            return self._poster(*args, **kwargs)
        import requests      # imported late: the CLI path does not need it

        return requests.post(*args, **kwargs)

    def ask(
        self, prompt: str, schema: dict[str, Any], *, instruction: str = "Follow the input."
    ) -> dict[str, Any]:
        """Send the prompt and schema, return the structured reply as a dict."""
        payload = chat_payload(
            instruction, answer_request(prompt, schema),
            model=self._model, max_tokens=self._max_tokens, json_mode=self._json_mode,
        )
        try:
            response = self._post(
                self._url,
                headers={"Authorization": f"Bearer {self._key}",
                         "Content-Type": "application/json"},
                json=payload,
                timeout=self._timeout,
            )
        except Exception as exc:        # noqa: BLE001 - any transport failure is retryable
            raise BrainError(f"the model endpoint could not be reached: {exc}") from exc

        status = int(getattr(response, "status_code", 0) or 0)
        text = (getattr(response, "text", "") or "").strip()

        # A dead key or an empty balance cannot be fixed by trying again, so
        # it ends the day rather than burning the two retries above it.
        if status in (401, 402, 403):
            raise BrainStopped(f"{text[:400] or f'HTTP {status}'}\n{API_AUTH_HELP}")
        if any(marker in text.lower() for marker in _STOP_MARKERS):
            raise BrainStopped(text[:400])
        if status >= 400:
            raise BrainError(f"the model endpoint returned {status}: {text[:400]}")

        try:
            body = response.json()
        except Exception as exc:        # noqa: BLE001 - a proxy error page, usually
            raise BrainError(f"the reply was not JSON: {text[:400]}") from exc

        said = _api_said(body if isinstance(body, dict) else {})
        if not said:
            raise BrainError(f"the model returned nothing: {text[:400]}")
        return read_answer(said, schema)


def make_brain(env: Mapping[str, str] | None = None) -> Any:
    """The client the day should use, chosen by MODEL_PROVIDER.

    Unset or "claude" keeps the Claude Code CLI on the Pro plan, which is what
    has always run. "openai" sends the same two asks to any OpenAI-compatible
    endpoint instead.
    """
    values = os.environ if env is None else env
    provider = (values.get("MODEL_PROVIDER") or "claude").strip().lower()
    if provider in ("", "claude", "anthropic", "claude-code"):
        return Claude(model=(values.get("MODEL_NAME") or MODEL).strip())
    if provider not in ("openai", "openai-compatible", "deepseek"):
        raise BrainStopped(
            f"MODEL_PROVIDER is {provider!r}; it must be 'claude' or 'openai'."
        )

    base_url = (values.get("MODEL_BASE_URL") or "").strip()
    api_key = (values.get("MODEL_API_KEY") or "").strip()
    model = (values.get("MODEL_NAME") or "").strip()
    missing = [name for name, value in
               (("MODEL_BASE_URL", base_url), ("MODEL_API_KEY", api_key),
                ("MODEL_NAME", model)) if not value]
    if missing:
        raise BrainStopped(
            f"MODEL_PROVIDER is {provider!r} but {', '.join(missing)} "
            "is not set. See docs/DEPLOYING.md."
        )
    return ChatApi(
        base_url=base_url, api_key=api_key, model=model,
        max_tokens=int(values.get("MODEL_MAX_TOKENS") or DEFAULT_MAX_TOKENS),
        json_mode=(values.get("MODEL_JSON_MODE") or "").strip().lower()
        in ("1", "true", "yes", "on"),
    )


def brain_description(env: Mapping[str, str] | None = None) -> str:
    """One line for the run header saying which model is being asked."""
    values = os.environ if env is None else env
    provider = (values.get("MODEL_PROVIDER") or "claude").strip().lower()
    if provider in ("", "claude", "anthropic", "claude-code"):
        return f"claude -p, model {(values.get('MODEL_NAME') or MODEL).strip()}, no tools"
    host = (values.get("MODEL_BASE_URL") or "?").strip().rstrip("/")
    return f"chat api at {host}, model {(values.get('MODEL_NAME') or '?').strip()}"
