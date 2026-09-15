"""Build what Opus is asked, from training-side facts only.

Every input here is a TrainingSummary, a research note, or a format document.
None of them carries a locked-year number, which is the whole of design 2.4:
the builder cannot use what it cannot see. A test asserts the built text
mentions nothing from the locked year.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from research.segment import (
    MIN_TRADES_PER_MONTH,
    TARGET_MONTHLY_MAX,
    TARGET_MONTHLY_MIN,
)
from research.summary import TrainingSummary

PROPOSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["title", "description", "hypothesis", "strategy_yaml", "change_note"],
    "properties": {
        "title": {"type": "string", "maxLength": 80},
        "description": {"type": "string", "maxLength": 400},
        "hypothesis": {"type": "string", "maxLength": 600},
        "strategy_yaml": {"type": "string"},
        "change_note": {"type": "string", "maxLength": 400},
    },
}

REVIEW_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["why_failed", "why_worked", "lessons", "decision"],
    "properties": {
        "why_failed": {"type": "string", "maxLength": 800},
        "why_worked": {"type": "string", "maxLength": 800},
        "lessons": {"type": "string", "maxLength": 800},
        "decision": {"type": "string", "enum": ["next_version", "new_idea", "stop"]},
        "change_hint": {"type": "string", "maxLength": 400},
    },
}

# The format pages describe a whole strategies.yaml FILE, and the first real
# proposal copied that shape - costing a repair round before the day could
# start. The checker wants one strategy, so the prompt says so.
SHAPE = """\
Write ONE strategy as a single top-level YAML mapping, not a file of them:
- v2: one item from the `strategies:` list, with the `version:` and
  `strategies:` wrapper removed, so `position_type`, `entry`, `exit` and
  `risk` are top-level keys.
- v3: the document exactly as the v3 page shows it, keeping `version: 3` as
  a top-level key.
"""

_YEARLY_MIN = (1 + TARGET_MONTHLY_MIN / 100) ** 12 * 100 - 100
_YEARLY_MAX = (1 + TARGET_MONTHLY_MAX / 100) ** 12 * 100 - 100

# What the owner actually wants, stated up front. Without it Opus optimised
# for "profitable somewhere", which is how six versions in a row came back
# holding one stock for two years and trading twice a quarter.
AIM = f"""\
What this strategy is being designed to achieve:
- an average compounded return of {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}% A MONTH,
  which is {_YEARLY_MIN:.0f}-{_YEARLY_MAX:.0f}% a year. That is deliberately ambitious: say so in
  your hypothesis if you think the idea cannot reach it, rather than quietly
  aiming lower.
- at least {MIN_TRADES_PER_MONTH:.0f} trades a month on the combination finally picked. A
  combination trading less often is DISCARDED however good its total looks,
  so an idea that enters once a quarter cannot win here.
- beating simply holding the same stock. One that made money while holding
  made more is discarded too.

Say in `description` which segment you are designing for: intraday (closed
the same day), swing (held days to a few weeks), or long-term (held months).
The tool MEASURES the segment from your trades' holding periods and reports
it, so an intraday claim that holds for three weeks is shown as swing.

FUTURES AND OPTIONS ARE NOT TESTABLE HERE. The price store holds NSE cash
equities only - no expiries, strikes, lot sizes or margin - so do not propose
an F&O strategy.
"""

RULES = """\
Rules the tool applies to whatever you write, so do not spend words on them:
- sizing is forced to notional, 100000 rupees per trade
- the symbols and timeframe you write are replaced: every strategy is tested on
  200 NSE stocks across 5m, 15m, 25m, 30m, 60m and day, and on 9 indexes at 60m
  and day
- enabled is forced to false; nothing you write can trade real or paper money
- a short strategy MUST set session.square_off, e.g. "15:10"
- fees are charged per trade, and a position held overnight pays delivery
  charges (about 0.21% of turnover) rather than intraday ones
"""


def _summary_block(summary: TrainingSummary) -> str:
    return json.dumps(summary.as_dict(), indent=2, default=str)


def propose_prompt(
    *,
    formats: Sequence[str],
    notes: Sequence[dict[str, Any]],
    ideas_tried: Sequence[str],
    previous: TrainingSummary | None = None,
    change_hint: str | None = None,
    error: str | None = None,
) -> str:
    """The ask for a strategy: a first idea, or the next version of one."""
    parts = [
        "You are designing ONE trading strategy to be tested on Indian equities.",
        "",
        AIM,
        "",
        RULES,
    ]
    if notes:
        parts.append("\nWhat earlier days learned:")
        for note in notes:
            parts.append(f"- {note.get('day')}: {note.get('idea_title')} — {note.get('lessons')}")
    if ideas_tried:
        parts.append("\nIdeas already tried (do not repeat one that failed the same way):")
        parts.extend(f"- {line}" for line in ideas_tried)
    if previous is not None:
        parts.append("\nHow your previous version did on the training years:")
        parts.append(_summary_block(previous))
    if change_hint:
        parts.append(f"\nThe change to make this time: {change_hint}")
    if error:
        parts.append(
            "\nYour last attempt was rejected by the checker. Fix exactly this "
            f"and return the whole strategy again:\n{error}"
        )
    parts.append("\nStrategy format reference:")
    parts.extend(formats)
    parts.append(f"\n{SHAPE}")
    parts.append(
        "Return that mapping as YAML in strategy_yaml. Write change_note as an "
        "empty string for a first version, otherwise say in one line what you "
        "changed and why."
    )
    return "\n".join(parts)


def review_prompt(*, summary: TrainingSummary, version: int, versions_left: int) -> str:
    """The ask for a verdict on a tested version, and what to do next."""
    parts = [
        f"Your version {version} was tested on the training years. Here is how it did.",
        "",
        _summary_block(summary),
        "",
        "Judge it honestly. A strategy that made money while simply holding the "
        "same stocks made more has no edge; say so. The top and bottom tables "
        "are ranked by excess_vs_hold_pct - what the strategy returned minus "
        "what holding that symbol returned over the same window - not by "
        "rupees, and combos_beating_hold is the count that matters.",
        "",
        "Then set decision to one of:",
        "- next_version: keep this idea and change one thing (say what in change_hint)",
        "- new_idea: this idea is not worth more versions; start a different one",
        "- stop: nothing further is worth trying today",
    ]
    if versions_left <= 0:
        parts.append(
            "\nThis was the LAST VERSION allowed today, so decision MUST be "
            '"stop". The tool will use this version as the day\'s final one.'
        )
    else:
        parts.append(f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)
