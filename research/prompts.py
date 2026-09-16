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

from research.facts import FACTS
from research.segment import TARGET_MONTHLY_MAX, TARGET_MONTHLY_MIN
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
        # When stopping, which version the day should end on - "v1.3" - if an
        # earlier one was better than the last. Absent means the one just
        # reviewed.
        "final_version": {"type": "string", "maxLength": 12},
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
- judged as an ACCOUNT: ten slots of Rs 1,00,000, taking entry signals as
  they arrive across all 200 stocks on one timeframe and skipping a signal
  when every slot is busy, measured month by month on the whole Rs 10,00,000.
  The timeframe whose account earns the most per month is picked - but only
  if it never fell more than 30% from a high, beat holding the same stocks
  on average, and either traded 10+ times a month or averaged the target.
  An account failing any of those is DISCARDED however good its best stock
  looks. A selective rule is fine: ten slots mean a signal on 5% of stocks
  still keeps the account busy.
- one stock's spectacular result counts for nothing on its own; the best of
  ~1,177 combinations is nearly always luck. Design for the average stock.

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
        "",
        FACTS,
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
        "Judge it honestly, and judge it on `accounts`: a ten-slot account "
        "taking signals as they arrive across every stock on one timeframe, "
        "measured month by month on the whole capital. That is how the final "
        "version is picked and how it will be examined - the timeframe whose "
        "account has the best avg_month_pct among those with `qualifies` true "
        "(why_not says what failed). Read months_positive_pct, worst_month_pct, "
        "worst_dip_pct, years_positive_pct, the year-by-year rows, "
        "signals_skipped_pct and luck_check before believing an average. "
        "avg_excess_pct is the average month minus what holding the same stocks "
        "made; below zero there is no edge, however large the return. `baskets` "
        "is the same rule with every stock funded all the time - the per-stock "
        "average, useful for seeing how broadly the rule works. The aim is an "
        f"average account month of {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}%.",
        "",
        "The top and bottom tables are single combinations ranked by "
        "excess_vs_hold_pct - the luckiest and unluckiest of ~1,177 tries. They "
        "show what kind of stock the idea suits; they are not evidence of an "
        "edge. combos_beating_hold counts how broadly the idea worked.",
        "",
        "Then set decision to one of:",
        "- next_version: keep this idea and change one thing (say what in change_hint)",
        "- new_idea: this idea is not worth more versions; start a different one",
        "- stop: nothing further is worth trying today. If an EARLIER version "
        'of this idea was better, name it in final_version as "v<idea>.<version>" '
        '(for example "v1.2"); otherwise the version just reviewed is the final one.',
    ]
    if versions_left <= 0:
        parts.append(
            "\nThis was the LAST VERSION allowed today, so decision MUST be "
            '"stop". The tool will use this version as the day\'s final one.'
        )
    else:
        parts.append(f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)
