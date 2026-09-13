"""One stored run, as the words that arrive on a phone (design 7.3).

Pure: a mapping in, a string out. No client, no clock, no network - so every
shape a day can take (no pick, cut short, a review longer than Telegram will
carry) is tested without a bot token.

This is the one place a locked-year number is ALLOWED to be formatted for a
human. The prompt builder never reads it; see design 2.4.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

TELEGRAM_LIMIT = 4096
START_VALUE = 100000
# A review runs to 800 characters of numbered lessons. All of it belongs on
# the dashboard; a phone notification wants the headline (design 7.3 asks for
# one sentence).
REVIEW_CHARS = 300

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


def telegram_text(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None
) -> str:
    """The morning message, inside Telegram's 4,096-character limit."""
    lines = [f"\U0001f4ca Research run — {_day(run.get('started_at'))}"]

    cut = _CUT_SHORT.get(str(run.get("status")))
    if cut:
        lines.append(f"⚠️ The day was {cut}. What it finished was still kept.")

    versions = run.get("versions_tried") or 0
    dropped = run.get("ideas_dropped") or 0
    lines.append(
        f"Idea: {title or run.get('final_strategy_name') or 'none'} "
        f"({versions} version(s) tried, {dropped} idea(s) dropped)"
    )

    if not run.get("pick_symbol"):
        lines.append(
            "No qualifying pick: nothing had 30+ training trades and a "
            "survivable worst dip, so the locked year was not opened."
        )
    else:
        lines.append(
            f"Best pick: {run['pick_symbol']} · {run.get('pick_timeframe')} "
            "(chosen on training years)"
        )
        lines.append(f"Locked year: {rupees(START_VALUE)} → {rupees(run.get('lakh_end_value'))}")
        lines.append(f"Just holding: {rupees(START_VALUE)} → {rupees(run.get('hold_end_value'))}")
        trades = run.get("locked_trades") or 0
        lines.append(
            f"Won {run.get('win_rate_pct') or 0:.0f}% of {trades} trades · "
            f"worst dip {run.get('worst_dip_pct') or 0:.1f}%"
        )
        passed = "✅ Passed" if run.get("verdict_passed") else "❌ Failed"
        beat = "Beat holding" if run.get("beat_holding") else "Did not beat holding"
        lines.append(f"Verdict: {passed} · {beat}")

    if run.get("ai_review"):
        lines.append(f"Why: {first_sentence(str(run['ai_review']))}")
    if dashboard_url:
        lines.append(f"Details: {dashboard_url}")
    lines.append(
        f"⚠️ Prices frozen at {_day(run.get('data_end'))} — every day "
        "tests a new idea against the same window, not new data."
    )

    text = "\n".join(lines)
    if len(text) <= TELEGRAM_LIMIT:
        return text
    return text[: TELEGRAM_LIMIT - 1] + "…"
