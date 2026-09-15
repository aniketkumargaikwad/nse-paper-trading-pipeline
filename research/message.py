"""One stored run, as the words that arrive on a phone (design 7.3).

Pure: a mapping in, a string out. No client, no clock, no network - so every
shape a day can take (no pick, cut short, a review longer than Telegram will
carry) is tested without a bot token.

Written for someone who does not read backtests for a living. That means:

* Every number says over what period. "Rs 1,00,000 became Rs 89,685" is
  meaningless without "in 12 months".
* Trade counts are given as a rate as well as a total. Six trades is
  unreadable; 0.5 a month is a sentence.
* The gain or loss is stated, not left as a subtraction between two lines.
* Every version gets a verdict, on the same scale, so seven rows can be read
  at a glance instead of compared by hand.
* "Beat holding" leads, because making money in a rising market is not an
  edge - and the training years rose about 418%.

Version verdicts are TRAINING-ONLY. The locked year is opened once, for the
chosen version, so no other version has a locked-year number to report and
the message must not imply one.

This is the one place a locked-year number is ALLOWED to be formatted for a
human. The prompt builder never reads it; see design 2.4.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

TELEGRAM_LIMIT = 4096
START_VALUE = 100000
REVIEW_CHARS = 260
# Every label must be shorter than this or its value runs into it. A test
# pins that, because the overflow is silent and only shows on a phone.
LABEL_WIDTH = 15
DAYS_PER_MONTH = 30.44

# What share of a version's combinations must beat simply holding the same
# stock before the idea is worth the words "an edge". Beating holding on half
# of 1,177 combinations is a signal; on a tenth it is scatter.
EDGE_PCT = 50.0
MIXED_PCT = 25.0

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


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None


def _day(value: Any) -> str:
    moment = _as_date(value)
    return moment.strftime("%d %b %Y") if moment else str(value or "")


def locked_months(run: Mapping[str, Any]) -> float:
    """How long the locked year actually was, in months.

    Every balance in this message is "over this long", and a reader cannot
    judge -10.3% without it.
    """
    start, end = _as_date(run.get("locked_from")), _as_date(run.get("locked_to"))
    if not start or not end:
        return 12.0
    return max((end - start).days / DAYS_PER_MONTH, 0.1)


def edge_verdict(beat: int | None, tested: int | None) -> str:
    """One scale for every version, so seven rows can be skimmed."""
    if not tested or beat is None:
        return "not measured"
    share = 100 * beat / tested
    if share >= EDGE_PCT:
        return "REAL EDGE"
    if share >= MIXED_PCT:
        return "mixed"
    return "no edge"


def idea_shape(versions: Sequence[Mapping[str, Any]]) -> str:
    """Whether the day refined one idea or tried several.

    The seven rows below look like seven strategies unless this says
    otherwise, and on the first real day they were seven tweaks of one.
    """
    if not versions:
        return "No version was tested."
    ideas = {row.get("idea_no") for row in versions}
    if len(ideas) == 1:
        if len(versions) == 1:
            return "One idea, tested once."
        return (f"Tried as {len(versions)} versions of this ONE idea - each "
                "version a tweak of the one before, not separate strategies.")
    return (f"{len(ideas)} separate ideas, {len(versions)} versions in total. "
            "A new idea starts from scratch; a new version tweaks the last one.")


def summary_rows(run: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The label/value pairs of the result table, in reading order."""
    months = locked_months(run)
    span = f"{months:.0f} months"

    if not run.get("pick_symbol"):
        return [
            ("Best stock", "none qualified"),
            ("Why none", "nothing had 30+ trades and a survivable drop"),
            ("VERDICT", "no strategy worth measuring"),
        ]

    end = run.get("lakh_end_value")
    hold = run.get("hold_end_value")
    trades = run.get("locked_trades") or 0
    wins = round((run.get("win_rate_pct") or 0) * trades / 100)

    rows = [
        ("Best stock", f"{run['pick_symbol']}, {run.get('pick_timeframe')} bars"),
        ("Started with", rupees(START_VALUE)),
        ("Ended with", rupees(end)),
    ]
    if end is not None:
        change = end - START_VALUE
        rows.append(
            ("You " + ("gained" if change >= 0 else "lost"),
             f"{rupees(abs(change))}  ({100 * change / START_VALUE:+.1f}% over {span})")
        )
    if hold is not None:
        rows.append(
            ("Just holding", f"{rupees(hold)}  "
                             f"({100 * (hold - START_VALUE) / START_VALUE:+.1f}%)")
        )
    rows += [
        ("Trades", f"{trades} in {span}  ({trades / months:.1f} per month)"),
        ("Wins", f"{wins} of {trades}  ({run.get('win_rate_pct') or 0:.0f}%)"),
        ("Worst drop", f"{run.get('worst_dip_pct') or 0:.1f}% below its own best"),
    ]

    passed = run.get("verdict_passed")
    beat = run.get("beat_holding")
    if passed and beat:
        verdict = "PASSED - made money and beat holding"
    elif beat:
        verdict = "FAILED - lost money, though holding lost more"
    elif passed:
        verdict = "FAILED - made money, but holding made more"
    else:
        verdict = "FAILED - lost money; holding gained instead"
    rows.append(("VERDICT", verdict))
    return rows


def version_table(versions: Sequence[Mapping[str, Any]], chosen: str | None = None) -> list[str]:
    """One row per version, on the same scale as each other."""
    if not versions:
        return []
    lines = ["Ver  Made money    Beat holding   Verdict"]
    for row in sorted(versions, key=lambda r: (r.get("idea_no") or 0, r.get("version_no") or 0)):
        label = f"v{row.get('idea_no')}.{row.get('version_no')}"
        facts = row.get("training_summary") or {}
        if not row.get("valid"):
            lines.append(f"{label:<5}{'-':<14}{'-':<15}rejected, not tested")
            continue
        tested = facts.get("combos_tested")
        profitable = facts.get("combos_profitable")
        beat = facts.get("combos_beating_hold")
        made = (f"{profitable} ({100 * profitable / tested:.0f}%)"
                if tested and profitable is not None else "-")
        beaten = (f"{beat} ({100 * beat / tested:.0f}%)"
                  if tested and beat is not None else "not measured")
        verdict = edge_verdict(beat, tested)
        if chosen and row.get("strategy_name") == chosen:
            verdict += " - CHOSEN"
        lines.append(f"{label:<5}{made:<14}{beaten:<15}{verdict}")
    return lines


def _table(rows: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"{label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _blocks(
    run: Mapping[str, Any],
    title: str | None,
    dashboard_url: str | None,
    versions: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str], str, list[str], str, list[str]]:
    """Heading, idea lines, result table, version table, legend, tail."""
    heading = f"\U0001f4ca Research run — {_day(run.get('started_at'))}"

    idea = [title or run.get("final_strategy_name") or "no idea reached a tested version"]
    if versions:
        idea.append(idea_shape(versions))

    months = locked_months(run)
    result_header = (
        f"THE RESULT — measured on {months:.0f} months of prices it never saw "
        f"while being designed: {_day(run.get('locked_from'))} to "
        f"{_day(run.get('locked_to') or run.get('data_end'))}."
    )

    chosen = run.get("final_strategy_name")
    rows = version_table(versions, chosen=chosen) if len(versions) > 1 else []
    legend = ""
    if rows:
        tested = max(
            (v.get("training_summary") or {}).get("combos_tested") or 0 for v in versions
        )
        legend = (
            f"Each version was tested on {tested} stock-and-timeframe combinations, "
            "using training years only. \"Beat holding\" is the one that matters: "
            "making money in a rising market is not an edge. "
            f"{EDGE_PCT:.0f}%+ = real edge, {MIXED_PCT:.0f}-{EDGE_PCT - 1:.0f}% = mixed, "
            "below that = no edge."
        )

    tail: list[str] = []
    cut = _CUT_SHORT.get(str(run.get("status")))
    if cut:
        tail.append(f"⚠️ The day was {cut}. What it finished was still kept.")
    if run.get("ai_review"):
        tail.append(f"What the AI concluded: {first_sentence(str(run['ai_review']))}")
    if dashboard_url:
        tail.append(f"Full detail: {dashboard_url}")
    tail.append(
        f"⚠️ Prices frozen at {_day(run.get('data_end'))} — every day "
        "tests a new idea against the same window, not new data."
    )
    return heading, idea, result_header, rows, legend, tail


def telegram_html(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None,
    versions: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The morning message for Telegram's HTML parse mode.

    Every interpolated value is escaped. A strategy title like
    "EMA20>EMA50 uptrend" contains a bare '>', which would otherwise make
    Telegram reject the whole message with a 400 and send nothing.
    """
    heading, idea, result_header, rows, legend, tail = _blocks(
        run, title, dashboard_url, versions)
    esc = html.escape

    body = f"<b>{esc(heading)}</b>\n" + "\n".join(esc(line) for line in idea)
    body += f"\n\n{esc(result_header)}\n<pre>{esc(_table(summary_rows(run)))}</pre>"
    if rows:
        body += (f"\n\n<b>THE {len(rows) - 1} VERSIONS</b>\n"
                 f"<pre>{esc(chr(10).join(rows))}</pre>\n{esc(legend)}")
    for line in tail:
        body += f"\n\n{esc(line)}"
    if len(body) <= TELEGRAM_LIMIT:
        return body
    # Trimming inside a tag would break the markup, so the version table and
    # the tail are dropped whole before the result is touched.
    short = f"<b>{esc(heading)}</b>\n{esc(idea[0][:200])}"
    short += f"\n\n{esc(result_header)}\n<pre>{esc(_table(summary_rows(run)))}</pre>"
    return short[:TELEGRAM_LIMIT]


def plain_text(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None,
    versions: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The same message with no markup, for email and for reading in a log."""
    heading, idea, result_header, rows, legend, tail = _blocks(
        run, title, dashboard_url, versions)
    parts = [heading, *idea, "", result_header, "", _table(summary_rows(run))]
    if rows:
        parts += ["", f"THE {len(rows) - 1} VERSIONS", "", *rows, "", legend]
    for line in tail:
        parts += ["", line]
    text = "\n".join(parts)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"
