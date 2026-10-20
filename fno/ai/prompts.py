"""What the options research AI is asked (design §2), from training-side facts only.

Every input is a training summary, a ledger line, an atlas report (training
years only) or the format reference. Nothing here can carry an exam number:
the exam file (fno.ai.ledger.EXAMS) is never read by this package.

The answer shapes are the stock research's own (research.prompts), so the
loop, the brain and the final choice are shared unchanged.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from fno import rules
from research.portfolio import LUCK_STRONG_T
from research.prompts import PROPOSE_SCHEMA, REVIEW_SCHEMA  # noqa: F401 - re-exported for the day
from research.segment import TARGET_MONTHLY_MAX, TARGET_MONTHLY_MIN

ATLAS_FILES = (("results.json", "positional atlas"), ("intraday.json", "intraday atlas"),
               ("ivrank_test.json", "expensive-options test (entry only when iv_rank >= 67)"))
FORMAT_DOC = Path("docs/OPTIONS_STRATEGY_FORMAT.md")

AIM = f"""\
What this strategy is being designed to achieve:
- an average compounded return of {TARGET_MONTHLY_MIN:.0f}-{TARGET_MONTHLY_MAX:.0f}% A MONTH on a Rs 5,00,000
  account trading NIFTY or BANKNIFTY options. That is deliberately ambitious:
  say so in your hypothesis if you think the idea cannot reach it, rather
  than quietly aiming lower.
- it must PASS this rule on at least one of the two indexes, on the training
  years, or it is discarded however good one number looks:
  - history: at least {rules.MIN_MONTHS['positional']} months (positional) or {rules.MIN_MONTHS['intraday']} months (intraday)
  - worst single day no worse than {rules.WORST_DAY_PCT:.0f}% of the account
  - worst fall from a high no more than {rules.MAX_FALL_PCT:.0f}%
  - a positive average month
  - at least {rules.MIN_TRADES_PER_MONTH:.0f} trades a month, or an average month of {TARGET_MONTHLY_MIN:.0f}% or more. A
    rule that trades a few times a year FAILS: the owner does not want it,
    and the account sits idle while simply holding NIFTY earns more.
  - beat simply holding the index on the same capital in at least
    {rules.MIN_YEARS_BEATING_HOLDING_PCT:.0f}% of its years
  - a lead over holding larger than luck explains (luck test t of {LUCK_STRONG_T:.0f} or more)
- nothing from the textbook has passed (the atlas below). Selling options
  wins often and loses big; buying them bleeds time value; costs - half the
  spread on every leg, NSE charges - take a real share of every trade. Aim
  for an idea with a REASON to work after those costs, not a parameter tweak
  of a failed classic.

Every strategy is tested on BOTH NIFTY and BANKNIFTY (`underlying` is
replaced), so design for both. Choose `mode`:
- positional: decided at each daily close, held across days; tested on
  daily data from Jan 2016 (about ten years, including the 2020 crash).
- intraday: decided minute by minute and closed by exit.time the same day;
  tested on real option minutes from Oct 2024 - about one year of training,
  with no crash in it. BANKNIFTY has only a MONTHLY expiry since Nov 2024,
  and a monthly option barely decays inside one day.
"""

RULES = """\
Rules the tool applies to whatever you write, so do not spend words on them:
- name and underlying are replaced; the strategy runs on NIFTY and BANKNIFTY
- nothing you write can trade real or paper money: this is research only
- intraday strikes must stay within 5% of the index, and expiry.which within 1
- sizing.max_margin_pct is yours to set; missing means 25 (positional) or 100
  (intraday). It is the main lever on the worst day: one bad gap at full
  margin can exceed the -10% limit on its own. But it must buy at least one
  set: one NIFTY short strangle or straddle set has needed about Rs 1.4-2.5
  lakh of margin since 2025, so at 25% of Rs 5 lakh (Rs 1.25 lakh) the
  textbook sellers above could not open a single position in 2025-26 - the
  day is skipped as "not enough capital". Defined-risk positions (spreads,
  condors, flies) need far less. Check each year's trade count.
- exit.max_loss_pct caps a position's loss as a share of the ACCOUNT (e.g. 2
  = Rs 10,000 on Rs 5 lakh), whichever of it and stop_pct comes first.
  Intraday it holds a day's loss to about the cap plus slippage - the direct
  answer to a -10% worst day. Positional it is checked at the close, so an
  overnight gap still goes through it.
"""


def atlas_lines(root: Path) -> list[str]:
    """One line per baseline the tool already measured, training years only."""
    lines = []
    for name, label in ATLAS_FILES:
        path = Path(root) / "atlas" / name
        if not path.exists():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            h = row.get("headline") or row
            strategy = row.get("name") or row.get("strategy", "?")
            underlying = row.get("underlying") or row.get("index", "?")
            avg = h.get("avg_month_pct", h.get("avg_month"))
            fall = h.get("worst_fall_pct", h.get("worst_fall"))
            day = h.get("worst_day_pct", h.get("worst_day"))
            tpm = h.get("trades_per_month")
            won = h.get("win_rate_pct", h.get("won_pct"))
            why = row.get("why_not") or ("passes" if row.get("passes") else "")
            lines.append(f"{label}: {strategy} on {underlying}: {avg:+.2f}%/month, won {won:.0f}%, "
                         f"{tpm:.1f} trades/month, worst day {day:+.1f}%, worst fall {fall:.1f}% - "
                         f"{'fails: ' + why if why != 'passes' else 'PASSES'}")
    return lines


def format_reference(root: Path | None = None) -> str:
    return ((root or Path(".")) / FORMAT_DOC).read_text(encoding="utf-8")


def propose_prompt(
    *,
    format_doc: str,
    atlas: Sequence[str],
    notes: Sequence[Mapping[str, Any]],
    ideas_tried: Sequence[str],
    previous: Any = None,
    change_hint: str | None = None,
    error: str | None = None,
) -> str:
    parts = ["You are designing ONE options trading strategy for NSE index options.", "", AIM, "", RULES]
    if atlas:
        parts.append("\nBaselines already measured on this exact data (training years, the same "
                     "engine and rule your strategy faces). None passed. Do not re-test them:")
        parts.extend(f"- {line}" for line in atlas)
    if notes:
        parts.append("\nWhat earlier options research days learned:")
        parts.extend(f"- {n.get('day')}: {n.get('idea_title')} - {n.get('lessons')}" for n in notes)
    if ideas_tried:
        parts.append("\nOptions ideas already tried (do not repeat one that failed the same way):")
        parts.extend(f"- {line}" for line in ideas_tried)
    if previous is not None:
        parts.append("\nHow your previous version did on the training years:")
        parts.append(json.dumps(previous.as_dict(), indent=1, default=str))
    if change_hint:
        parts.append(f"\nThe change to make this time: {change_hint}")
    if error:
        parts.append("\nYour last attempt was rejected by the checker. Fix exactly this and return "
                     f"the whole strategy again:\n{error}")
    parts += ["\nStrategy format reference:", format_doc,
              "\nWrite ONE strategy as a single YAML mapping in the options/1 format and return it "
              "in strategy_yaml. Say in description whether it is positional or intraday and why. "
              "Write change_note as an empty string for a first version, otherwise say in one line "
              "what you changed and why."]
    return "\n".join(parts)


def review_prompt(*, summary: Any, version: int, versions_left: int) -> str:
    parts = [
        f"Your version {version} was tested on the training years, on NIFTY and on BANKNIFTY. "
        "Here is how it did.", "",
        json.dumps(summary.as_dict(), indent=1, default=str), "",
        "Judge it honestly against the pass rule: `passes` and `why_not` per index say what failed. "
        "Read months_positive_pct, worst_day_pct, worst_fall_pct, trades_per_month, the year rows "
        "(strategy_pct against holding_pct), edge_t (the luck test, needs 2 or more) and the splits "
        "by market condition before believing an average. A `warning` means most days could not "
        f"be entered as written. The aim is an average month of {TARGET_MONTHLY_MIN:.0f}-"
        f"{TARGET_MONTHLY_MAX:.0f}% that passes; a version trading a few times a year has failed "
        "however well its trades did.",
        "",
        "Then set decision to one of:",
        "- next_version: keep this idea and change one thing (say what in change_hint)",
        "- new_idea: this idea is not worth more versions; start a different one",
        "- stop: nothing further is worth trying today. If an EARLIER version of this idea was "
        'better, name it in final_version as "v<idea>.<version>" (for example "v1.2"); otherwise '
        "the version just reviewed is the final one.",
    ]
    if versions_left <= 0:
        parts.append('\nThis was the LAST VERSION allowed today, so decision MUST be "stop".')
    else:
        parts.append(f"\nVersions left today: {versions_left}.")
    return "\n".join(parts)
