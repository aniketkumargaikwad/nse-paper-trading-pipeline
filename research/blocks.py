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

# Telegram cannot colour text (its HTML allows bold, italic, code and links - no colours), so
# the owner's "red when it fails, green when it passes" (18 Nov 2026) is a coloured circle
# at the start of the line. Email gets the same characters.
GREEN, RED, WHITE = "\U0001f7e2", "\U0001f534", "⚪"


def mark(ok: bool | None) -> str:
    """🟢 for a pass, 🔴 for a fail, ⚪ when there is no verdict yet."""
    return WHITE if ok is None else (GREEN if ok else RED)


def marked(ok: bool | None, text: str) -> str:
    return f"{mark(ok)} {text}"


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


def promote_each(versions, promote_one) -> str:
    """Run `promote_one(version)` -> (entry, words) for each version; one line for the message.

    A failure is written into that line, never raised: on 19 Nov 2026 a KeyError here
    crashed the index research AFTER its research, so a plan that passed was neither
    saved nor paper-traded, and no message went out."""
    said = []
    for v in versions:
        try:
            _, words = promote_one(v)
        except Exception as exc:        # noqa: BLE001 - the day's message matters more
            words = f"could NOT be added to paper trading ({type(exc).__name__}: {exc}) - see the job log"
            print(f"WARNING: v{v.idea_no}.{v.version_no}: {words}")
        said.append(f"v{v.idea_no}.{v.version_no} {words}")
    return "; ".join(said)


def pack(blocks: Sequence[Block]) -> list[list[Block]]:
    """Whole blocks grouped into as few Telegram messages as fit, in order - nothing cut.

    A single block too long for one message is still sent alone (and cut by render)."""
    groups: list[list[Block]] = []
    for b in blocks:
        if groups and len(_render([*groups[-1], b], as_html=True)) <= TELEGRAM_LIMIT:
            groups[-1].append(b)
        else:
            groups.append([b])
    return groups


def send(subject: str, blocks: Sequence[Block], *, warn_token: bool = True, split: bool = False) -> bool:
    """Telegram (HTML) and email (plain text), each channel's failure kept to itself.

    Which bot receives it is the workflow's choice: each job hands this the
    TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID of its own bot (docs/TELEGRAM_BOTS.md).
    With `split`, a long message goes to Telegram as several, each block whole,
    instead of losing its last notes; email always gets it in one piece.

    Returns whether any channel confirmed delivery."""
    from research.notify import send_all, send_telegram, token_warning

    def pair(*names: str) -> tuple[str, ...] | None:
        values = tuple((os.environ.get(n) or "").strip() for n in names)
        return values if all(values) else None

    blocks = list(blocks)
    warning = token_warning(os.environ.get("CLAUDE_TOKEN_CREATED", "")) if warn_token else ""
    if warning:
        blocks.append(Block("Claude token", note=warning))
    telegram = pair("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID")
    groups = pack(blocks) if split else [blocks]
    body = _render(blocks, as_html=False) if split else as_text(blocks)    # email has no length limit
    results = send_all(as_html(groups[0]), subject, body,
                       telegram=telegram,
                       email=pair("GMAIL_USER", "GMAIL_APP_PASSWORD", "NOTIFY_EMAIL"))
    if telegram and results["telegram"].sent:
        for group in groups[1:]:
            more = send_telegram(telegram[0], telegram[1], as_html(group))
            if not more.sent:
                results["telegram"] = more
                break
    for channel, result in results.items():
        print(f"{channel:9s} {'sent' if result.sent else result.detail}")
    return any(r.sent for r in results.values())
