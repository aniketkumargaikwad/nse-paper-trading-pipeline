"""Ask Opus, with no tools and a fixed answer shape.

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
from collections.abc import Callable
from typing import Any

TIMEOUT_SECONDS = 900
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
        model: str = "opus",
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
