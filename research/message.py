"""One stored run, as the words that arrive on a phone (design 7.3).

Pure: a mapping in, a string out. No client, no clock, no network - so every
shape a day can take is tested without a bot token.

THE SHAPE
---------
One block per version, each with the same detail, then the day's verdict,
then the locked year. A reader should be able to see what changed between
v3 and v4 and what it did, without opening anything.

WHICH NUMBERS ARE WHICH
-----------------------
Version blocks are the TRAINING window - every year of history except the
last one, which is eight to fifteen years depending on the timeframe. That is
the full backtest.

The locked year is the exam: twelve months held back, opened ONCE, for the
one version the day ended on. It is deliberately not reported per version.
Showing every version's locked year and letting a human pick the best is
fitting to the exam, which is the whole thing the locked year exists to
prevent.

Written for someone who does not read backtests for a living: every number
says over what period, trade counts come as a rate, and every version carries
a verdict on one scale.

This is the one place a locked-year number is ALLOWED to be formatted for a
human. The prompt builder never reads it; see design 2.4.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from research.segment import describe, meets_target, min_trades_per_month

TELEGRAM_LIMIT = 4096
START_VALUE = 100000
REVIEW_CHARS = 220
LABEL_WIDTH = 15
DAYS_PER_MONTH = 30.44

# What share of a version's combinations must beat simply holding the same
# stock before the idea is worth the words "an edge". Beating holding on half
# of 1,177 combinations is a signal; on a tenth it is scatter.
EDGE_PCT = 50.0
MIXED_PCT = 25.0

# Below this, the locked year has not failed - it has not been answered. One
# trade is a coin toss whichever way it lands, and calling that FAILED reads
# as evidence against the strategy when there is none either way. Matches
# research.lakh.MIN_LOCKED_TRADES, which is what `passed` already requires.
MIN_LOCKED_TRADES = 10

# Written at the point the run stopped, after a blank line, so the message
# ends where the day did rather than trailing off mid-thought.
CUT_SHORT_MARKER = "limit expired........................."

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
    """How long the locked year actually was, in months."""
    start, end = _as_date(run.get("locked_from")), _as_date(run.get("locked_to"))
    if not start or not end:
        return 12.0
    return max((end - start).days / DAYS_PER_MONTH, 0.1)


def edge_verdict(beat: int | None, tested: int | None) -> str:
    """One scale for every version, so seven blocks can be compared."""
    if not tested or beat is None:
        return "not measured"
    share = 100 * beat / tested
    if share >= EDGE_PCT:
        return f"REAL EDGE ({share:.0f}% beat holding)"
    if share >= MIXED_PCT:
        return f"mixed ({share:.0f}% beat holding)"
    return f"no edge (only {share:.0f}% beat holding)"


def idea_shape(versions: Sequence[Mapping[str, Any]]) -> str:
    """Whether the day refined one idea or tried several."""
    if not versions:
        return "No version was tested."
    ideas = {row.get("idea_no") for row in versions}
    if len(ideas) == 1:
        if len(versions) == 1:
            return "One idea, tested once."
        return (f"{len(versions)} versions of this ONE idea - each a tweak of the "
                "one before, not separate strategies.")
    return (f"{len(ideas)} separate ideas, {len(versions)} versions in total. "
            "A new idea starts from scratch; a new version tweaks the last one.")


def _monthly_from_annual(cagr_pct: float | None) -> float | None:
    """The monthly rate that compounds to this annual one.

    Dividing by twelve would overstate it, and this number sits next to real
    money.
    """
    if cagr_pct is None or cagr_pct <= -100:
        return None
    return 100 * ((1 + cagr_pct / 100) ** (1 / 12) - 1)


def version_rows(version: Mapping[str, Any], *, compact: bool = False) -> list[tuple[str, str]]:
    """One version's TRAINING result, in the same shape as every other."""
    facts = version.get("training_summary") or {}
    if not version.get("valid"):
        error = (version.get("error") or "no reason recorded").splitlines()[0]
        return [("Rejected", error[:60]), ("VERDICT", "never tested")]
    if not facts:
        return [("VERDICT", "tested, but no summary was stored")]

    tested = facts.get("combos_tested")
    profitable = facts.get("combos_profitable")
    beat = facts.get("combos_beating_hold")
    best = (facts.get("top") or [{}])[0]

    rows: list[tuple[str, str]] = []
    if best.get("symbol"):
        rows.append(("Luckiest", f"{best['symbol']}, {best.get('timeframe')} bars"))

        if best.get("segment") and not compact:
            rows.append(("Built for", describe(best["segment"], best.get("held_days"))))

        years = best.get("window_years")
        if years and not compact:
            rows.append(("Tested over", f"{years:.1f} years of history"))

        trades = best.get("trades") or 0
        rate = best.get("trades_per_month")
        pace = ""
        if rate:
            pace = f"  ({rate:.1f} a month"
            floor = min_trades_per_month(best.get("segment") or "")
            # Why a fine-looking combination is not the pick: it trades too
            # rarely for the segment it turned out to be.
            pace += f", below the {floor:.0f} floor)" if rate < floor else ")"
        rows.append(("Total trades", f"{trades}{pace}"))

        if not compact and best.get("win_rate_pct") is not None:
            rows.append(("Win rate", f"{best['win_rate_pct']:.0f}%"))

        # Older runs were stored before these were recorded. A missing row is
        # honest; "n/a" beside real money is not.
        end = best.get("end_value")
        if end is not None:
            if compact:
                rows.append((f"{rupees(START_VALUE)} →", rupees(end)))
            else:
                rows.append(("Started with", rupees(START_VALUE)))
                rows.append(("Ended with", rupees(end)))
        if best.get("holding_value") is not None:
            rows.append(("Just holding", rupees(best["holding_value"])))

        annual = best.get("cagr_pct")
        if annual is not None:
            rows.append(("Return a year", f"{annual:+.1f}%"))
            monthly = _monthly_from_annual(annual)
            if monthly is not None:
                # The target band is the point of the line, so it survives
                # compaction even when the wording has to shrink.
                against = meets_target(monthly)
                if compact:
                    against = against.replace("the 4-7% target", "target")
                rows.append(("Return a month", f"{monthly:+.2f}%  ({against})"))
        if not compact and best.get("worst_dip_pct") is not None:
            rows.append(("Worst drop", f"{best['worst_dip_pct']:.1f}%"))

    if tested:
        rows.append(("Across all", f"{tested} combos · {profitable} made money "
                                   f"· {beat if beat is not None else '?'} beat holding"))
    rows.append(("VERDICT", edge_verdict(beat, tested)))
    return rows


def locked_rows(run: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The exam: the chosen version on twelve months it never saw."""
    months = locked_months(run)
    span = f"{months:.0f} months"

    if not run.get("pick_symbol"):
        return [
            ("Best stock", "none qualified"),
            ("Why none", "nothing beat simply holding in training"),
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

    passed, beat = run.get("verdict_passed"), run.get("beat_holding")
    if trades < MIN_LOCKED_TRADES:
        rows.append(("VERDICT", f"TOO FEW TRADES to judge - {trades} in {span}, "
                                f"needs {MIN_LOCKED_TRADES}+"))
        return rows
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


def best_version(versions: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The version that beat holding most often. The day's own answer."""
    scored = [
        (((v.get("training_summary") or {}).get("combos_beating_hold") or 0)
         / max((v.get("training_summary") or {}).get("combos_tested") or 1, 1), v)
        for v in versions if v.get("valid") and v.get("training_summary")
    ]
    return max(scored, key=lambda pair: pair[0])[1] if scored else None


# How much of a change note survives. A version heading is a line on a phone,
# and Opus writes these at up to 400 characters.
NOTE_CHARS = 150
NOTE_CHARS_COMPACT = 70


def _label(version: Mapping[str, Any], *, first: bool, compact: bool = False) -> str:
    tag = f"v{version.get('idea_no')}.{version.get('version_no')}"
    note = " ".join((version.get("change_note") or "").split())
    if first:
        return f"{tag} - the first version"
    if not note:
        return f"{tag} - no change recorded"
    limit = NOTE_CHARS_COMPACT if compact else NOTE_CHARS
    if len(note) > limit:
        note = note[: limit - 1].rstrip() + "…"
    return f"{tag} - changed: {note}"


def _table(rows: Sequence[tuple[str, str]]) -> str:
    return "\n".join(f"{label:<{LABEL_WIDTH}}{value}" for label, value in rows)


def _ordered(versions: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(versions, key=lambda r: (r.get("idea_no") or 0, r.get("version_no") or 0))


def _assemble(
    run: Mapping[str, Any],
    title: str | None,
    dashboard_url: str | None,
    versions: Sequence[Mapping[str, Any]],
    *,
    compact: bool,
) -> list[tuple[str, str | None]]:
    """(heading text, table text or None) pairs, in order."""
    blocks: list[tuple[str, str | None]] = []
    heading = f"\U0001f4ca Research run — {_day(run.get('started_at'))}"
    name = title or run.get("final_strategy_name") or "no idea reached a tested version"
    intro = f"{heading}\n{name}"
    if versions:
        intro += f"\n{idea_shape(versions)}"
    intro += ("\n\nEvery version below is the FULL backtest: every training year, "
              "all stocks and timeframes. The locked year at the end is the exam.")
    # The 83x that prompted this warning was one stock in one lucky stretch,
    # selected as the best of 1,177 tries. Without saying so, the figure reads
    # as what the strategy earns.
    intro += ("\n\n⚠️ \"Luckiest\" is the single best of ~1,177 combinations "
              "tried. With that many tries the top one always looks spectacular, and "
              "it is usually one stock in one lucky stretch. Read the VERDICT line "
              "instead: it counts how many of the 1,177 beat simply holding. The "
              "locked-year pick is chosen separately and must also survive a 30% "
              "drawdown limit and 30+ trades, so it is often a different stock.")
    blocks.append((intro, None))

    ordered = _ordered(versions)
    for index, version in enumerate(ordered):
        blocks.append((_label(version, first=index == 0, compact=compact),
                       _table(version_rows(version, compact=compact))))

    # A day the allowance cut short stops here. Everything after this point -
    # the verdict, the locked year - describes a day that finished, and the
    # marker is the honest end of one that did not. The locked year is still
    # measured and stored; it is on the dashboard, not in this message.
    if _CUT_SHORT.get(str(run.get("status"))):
        blocks.append((f"\n{CUT_SHORT_MARKER}", None))
        return blocks

    winner = best_version(ordered)
    chosen = run.get("final_strategy_name")
    verdict = "VERDICT FOR THE DAY"
    if winner is not None:
        tag = f"v{winner.get('idea_no')}.{winner.get('version_no')}"
        facts = winner.get("training_summary") or {}
        verdict += (f"\nBest of the {len(ordered)}: {tag} - "
                    f"{edge_verdict(facts.get('combos_beating_hold'), facts.get('combos_tested'))}")
        if chosen and winner.get("strategy_name") != chosen:
            verdict += "\n(The day ended on a different version; the locked year below is that one.)"
    if run.get("ai_review"):
        verdict += f"\nThe AI's own conclusion: {first_sentence(str(run['ai_review']))}"
    blocks.append((verdict, None))

    months = locked_months(run)
    exam = (f"THE LOCKED YEAR — {months:.0f} months never seen while designing "
            f"({_day(run.get('locked_from'))} to "
            f"{_day(run.get('locked_to') or run.get('data_end'))}), opened once, "
            "for the chosen version only.")
    blocks.append((exam, _table(locked_rows(run))))

    tail = ""
    if dashboard_url:
        tail += f"Full detail: {dashboard_url}\n"
    tail += (f"⚠️ Prices frozen at {_day(run.get('data_end'))} — every day "
             "tests a new idea against the same window, not new data.")
    blocks.append((tail, None))
    return blocks


def _render(blocks: Sequence[tuple[str, str | None]], *, as_html: bool) -> str:
    parts = []
    for heading, table in blocks:
        if as_html:
            head = f"<b>{html.escape(heading)}</b>" if table else html.escape(heading)
            parts.append(head + (f"\n<pre>{html.escape(table)}</pre>" if table else ""))
        else:
            parts.append(heading + (f"\n{table}" if table else ""))
    return "\n\n".join(parts)


def _build(
    run: Mapping[str, Any], title: str | None, dashboard_url: str | None,
    versions: Sequence[Mapping[str, Any]], *, as_html: bool,
) -> str:
    """Full detail if it fits, compact if it does not, truncated only as a last resort.

    Seven versions at full detail is about 4,500 characters and Telegram stops
    at 4,096, so the fallback drops rows rather than letting the API refuse
    the whole message.
    """
    for compact in (False, True):
        text = _render(_assemble(run, title, dashboard_url, versions, compact=compact),
                       as_html=as_html)
        if len(text) <= TELEGRAM_LIMIT:
            return text
    # Still too long: keep the first version, the verdict and the exam.
    blocks = _assemble(run, title, dashboard_url, versions, compact=True)
    trimmed = [blocks[0], *blocks[1:2], *blocks[-3:]]
    text = _render(trimmed, as_html=as_html)
    return text if len(text) <= TELEGRAM_LIMIT else text[: TELEGRAM_LIMIT - 1] + "…"


def telegram_html(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None,
    versions: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The morning message for Telegram's HTML parse mode.

    Every interpolated value is escaped. A strategy title like
    "EMA20>EMA50 uptrend" contains a bare '>', which would otherwise make
    Telegram reject the whole message with a 400 and send nothing.
    """
    return _build(run, title, dashboard_url, versions, as_html=True)


def plain_text(
    run: Mapping[str, Any], *, title: str | None = None, dashboard_url: str | None = None,
    versions: Sequence[Mapping[str, Any]] = (),
) -> str:
    """The same message with no markup, for email and for reading in a log."""
    return _build(run, title, dashboard_url, versions, as_html=False)
