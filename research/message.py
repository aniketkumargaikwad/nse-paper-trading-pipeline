"""One stored run, as the words that arrive on a phone (design 7.3).

Pure: a mapping in, a string out. No client, no clock, no network - so every
shape a day can take (no pick, cut short, a review longer than Telegram will
carry) is tested without a bot token.

The numbers go in an aligned monospace block rather than a run of sentences.
Read on a phone, a paragraph of "Locked year: X. Just holding: Y." makes you
do the subtraction yourself; a column of labels does not. `summary_rows` is
the one place that decides what those labels are, and both renderers read it.

This is the one place a locked-year number is ALLOWED to be formatted for a
human. The prompt builder never reads it; see design 2.4.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

TELEGRAM_LIMIT = 4096
START_VALUE = 100000
# A review runs to 800 characters of numbered lessons. All of it belongs on
# the dashboard; a phone notification wants the headline (design 7.3 asks for
# one sentence).
REVIEW_CHARS = 300
# The label column. Wide enough for "Worst dip", narrow enough that a value
# still fits beside it on a phone.
LABEL_WIDTH = 11

# Why a day ended, in the words the owner would use.
_CUT_SHORT = {
    "stopped_limit": "cut short - the Claude allowance ran out",
    "stopped_time": "cut short - the time budget ran out",
    "failed": "cut short - the run failed",
}


def rupees(value: float | None) -> str:
    """Indian grouping: 1,00,000 rather than 100,000."""
    if value is None:
        return "n/a"
    whole = f"{int(round(value)):d}"
    sign, digits = ("-", whole[1:]) if whole.startswith("-") else ("", whole)
    if len(digits) <= 3:
        body = digits
    else:
        head, tail, parts = digits[:-3], digits[-3:], []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        body = ",".join([*parts, tail])
    return f"{sign}₹{body}"


def first_sentence(text: str, limit: int = REVIEW_CHARS) -> str:
    """The opening sentence, short enough to read without unlocking a phone."""
    said = " ".join((text or "").split())
    stop = said.find(". ")
    if 0 < stop <= limit:
        return said[: stop + 1]
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def _day(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    text = str(value or "")
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).strftime("%d %b %Y")
    except ValueError:
        return text


def summary_rows(run: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The label/value pairs the table is built from, in reading order."""
    versions = run.get("versions_tried") or 0
    dropped = run.get("ideas_dropped") or 0

    if not run.get("pick_symbol"):
        return [
            ("Pick", "none qualified"),
            ("Why none", "needs 30+ trades and a survivable dip"),
            ("Versions", f"{versions} tried, {dropped} dropped"),
            ("Verdict", "locked year not opened"),
        ]

    end = run.get("lakh_end_value")
    hold = run.get("hold_end_value")
    rows = [
        ("Pick", f"{run['pick_symbol']} · {run.get('pick_timeframe')}"),
        ("₹1 lakh", f"became {rupees(end)}"),
        ("Holding", f"became {rupees(hold)}"),
    ]
    if end is not None:
        change = end - START_VALUE
        # The one number a person actually wants, rather than the subtraction
        # of the two lines above it.
        rows.append(
            ("Result", f"{'+' if change >= 0 else '-'}{rupees(abs(change))} "
                       f"({100 * change / START_VALUE:+.1f}%)")
        )
    rows += [
        ("Trades", f"{run.get('locked_trades') or 0} · won "
                   f"{run.get('win_rate_pct') or 0:.0f}%"),
        ("Worst dip", f"{run.get('worst_dip_pct') or 0:.1f}%"),
        ("Versions", f"{versions} tried, {dropped} dropped"),
        ("Verdict", f"{'PASSED' if run.get('verdict_passed') else 'FAILED'} · "
                    f"{'beat holding' if run.get('beat_holding') else 'did not beat holding'}"),
    ]
    return rows


def _table(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _parts(
    run: Mapping[str, Any], title: str | None, dashboard_url: str | None
) -> tuple[str, str, list[str]]:
    """The heading, the table, and the lines that follow it."""
    heading = f"\U0001f4ca Research run — {_day(run.get('started_at'))}"
    name = title or run.get("final_strategy_name") or "no idea reached a tested version"

    tail: list[str] = []
    cut = _CUT_SHORT.get(str(run.get("status")))
    if cut:
        tail.append(f"⚠️ The day was {cut}. What it finished was still kept.")
    if run.get("ai_review"):
        tail.append(f"Why: {first_sentence(str(run['ai_review']))}")
    if dashboard_url:
        tail.append(f"Details: {dashboard_url}")
    tail.append(
        f"⚠️ Prices frozen at {_day(run.get('data_end'))} — every day "
        "tests a new idea against the same window, not new data."
    )
    return heading, name, tail


def telegram_html(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None
) -> str:
    """The morning message for Telegram's HTML parse mode.

    Every interpolated value is escaped. A strategy title like
    "EMA20>EMA50 uptrend" contains a bare '>', which would otherwise make
    Telegram reject the whole message with a 400 and send nothing.
    """
    heading, name, tail = _parts(run, title, dashboard_url)
    body = (
        f"<b>{html.escape(heading)}</b>\n"
        f"{html.escape(name)}\n\n"
        f"<pre>{html.escape(_table(summary_rows(run)))}</pre>"
    )
    for line in tail:
        body += f"\n{html.escape(line)}"
    if len(body) <= TELEGRAM_LIMIT:
        return body
    # Trimming inside a tag would break the markup, so the tail goes first.
    trimmed = (
        f"<b>{html.escape(heading)}</b>\n"
        f"{html.escape(name[:200])}\n\n"
        f"<pre>{html.escape(_table(summary_rows(run)))}</pre>"
    )
    return trimmed[:TELEGRAM_LIMIT]


def plain_text(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None
) -> str:
    """The same message with no markup, for email and for reading in a log."""
    heading, name, tail = _parts(run, title, dashboard_url)
    text = "\n".join([heading, name, "", _table(summary_rows(run)), "", *tail])
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"
