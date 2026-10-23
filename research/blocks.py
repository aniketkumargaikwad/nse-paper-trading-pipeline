"""The layout the newer messages share, in the morning research message's style (research.message).

Each block is a bold heading, a short aligned table in a monospace box, and a
note in plain text. On a phone the box does not wrap well, so table values
stay short; anything long - why a version failed, what the AI learned - goes
in the note, which wraps like any text. The options, rotation and paper-book
messages of 20-23 Oct 2026 put everything into one wide box and were hard to
read; this replaces that.
"""

from __future__ import annotations

import html
import os
from collections.abc import Sequence
from dataclasses import dataclass

from research.message import LABEL_WIDTH, TELEGRAM_LIMIT, first_sentence

LESSON_CHARS = 200


@dataclass(frozen=True)
class Block:
    heading: str
    rows: tuple[tuple[str, str], ...] = ()
    note: str = ""


def lesson(text: str | None) -> str:
    return f"Lesson: {first_sentence(text or '', LESSON_CHARS)}" if text else ""


def _table(rows: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"{label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _render(blocks: Sequence[Block], *, as_html: bool) -> str:
    parts = []
    for b in blocks:
        if as_html:
            part = f"<b>{html.escape(b.heading)}</b>"
            if b.rows:
                part += f"\n<pre>{html.escape(_table(b.rows))}</pre>"
            if b.note:
                part += f"\n{html.escape(b.note)}"
        else:
            part = b.heading + (f"\n{_table(b.rows)}" if b.rows else "") + (f"\n{b.note}" if b.note else "")
        parts.append(part)
    return "\n\n".join(parts)


def render(blocks: Sequence[Block], *, as_html: bool) -> str:
    """The whole message; notes are dropped from the end, then text cut, to stay under Telegram's limit."""
    blocks = list(blocks)
    text = _render(blocks, as_html=as_html)
    i = len(blocks) - 1
    while len(text) > TELEGRAM_LIMIT and i > 0:
        if blocks[i].note:
            blocks[i] = Block(blocks[i].heading, blocks[i].rows, "")
            text = _render(blocks, as_html=as_html)
        i -= 1
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


def as_text(blocks: Sequence[Block]) -> str:
    return render(blocks, as_html=False)


def as_html(blocks: Sequence[Block]) -> str:
    return render(blocks, as_html=True)


def send(subject: str, blocks: Sequence[Block], *, warn_token: bool = True) -> None:
    """Telegram (HTML) and email (plain text), each channel's failure kept to itself."""
    from research.notify import send_all, token_warning

    def pair(*names: str) -> tuple[str, ...] | None:
        values = tuple((os.environ.get(n) or "").strip() for n in names)
        return values if all(values) else None

    blocks = list(blocks)
    warning = token_warning(os.environ.get("CLAUDE_TOKEN_CREATED", "")) if warn_token else ""
    if warning:
        blocks.append(Block("Claude token", note=warning))
    results = send_all(as_html(blocks), subject, as_text(blocks),
                       telegram=pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"),
                       email=pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"))
    for channel, result in results.items():
        print(f"{channel:9s} {'sent' if result.sent else result.detail}")
