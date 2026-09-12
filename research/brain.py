"""Ask Opus, with no tools and a fixed answer shape.

Every call is one non-interactive `claude -p` with EVERY tool removed, so
Claude cannot read a file, run a command or reach the network. That is what
makes design 2.4 enforceable: it knows only what the prompt builder chose to
put in front of it.

`--safe-mode` (not `--bare`) is deliberate: both skip local configuration, but
bare mode refuses the subscription login and demands an API key, and this
system runs on the Pro plan.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

TIMEOUT_SECONDS = 900

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


def claude_executable() -> str:
    """Where the Claude Code CLI is, honouring CLAUDE_CLI if set."""
    return os.environ.get("CLAUDE_CLI") or shutil.which("claude") or "claude"


def build_command(instruction: str, schema: dict[str, Any], *, model: str) -> list[str]:
    """The exact call. Every flag here is load-bearing; see the module docstring."""
    return [
        claude_executable(),
        "-p", instruction,
        "--safe-mode",
        "--disallowed-tools", "*",
        # dontAsk denies anything that would prompt. There is deliberately no
        # --permission-prompts here: it needs Claude Code v2.1.259+, and with
        # every tool removed there is nothing left that could raise a prompt.
        "--permission-mode", "dontAsk",
        "--model", model,
        "--max-turns", "1",
        "--output-format", "json",
        "--json-schema", json.dumps(schema),
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
        command = build_command(instruction, schema, model=self._model)
        completed = self._runner(
            command, input=prompt, capture_output=True, text=True,
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
            raise BrainError(f"could not read the reply as JSON: {stdout[:400]}") from exc

        structured = envelope.get("structured_output")
        if not isinstance(structured, dict):
            raise BrainError(
                "the reply had no structured output; Claude said: "
                f"{str(envelope.get('result'))[:400]}"
            )
        return structured
