"""Where a day's FIRST version is sent, so the loop stops circling.

WHY
---
`research.facts` ends with one sentence listing where edge has not been
measured yet. It is the last line of a fifty-line block whose other forty-nine
lines are failures, and it is phrased as an observation rather than an ask. The
result is visible in the journal: 48 ideas tried by 21 Sep, and the proposals
of 17-21 Sep were "leader breakout to a 200-day high", "daily-only 200-day
leader breakout", "daily 200-DMA trend hold", "daily leader breakout", "daily
60-day leader breakout". The negative evidence dominates the prompt without
redirecting it - Opus reads what failed and proposes a neighbour of it.

So the untried directions become a first-class input: one is ASSIGNED to the
first version of each idea, and the rotation moves with the date so two days
running do not get the same one.

WHAT IS IN HERE AND WHAT IS NOT
-------------------------------
Only directions a strategy can actually EXPRESS today. `strategy.parse.Operand`
carries an indicator, a source, an offset and an optional HIGHER timeframe -
and no symbol. A rule can therefore read its own stock's series and nothing
else, and `research.sweep.run_combo` hands the simulator one symbol's frame at
a time in a worker process. Three of the six directions facts.py advertises are
out of reach for that reason, not because nobody thought of them:

* cross-sectional relative strength (rank one stock against the other 199)
  needs a symbol-referencing operand AND a sweep that walks every symbol on a
  shared clock instead of one at a time;
* regime filters from index breadth (how many of 200 stocks are above their
  200-day average) need the same cross-symbol read - the indexes in
  `universe.INDEXES` are tested as their own combinations, never as a filter
  over a stock;
* time-of-day effects need a clock operand; `strategy.vocabulary` has none.

Assigning one of those would spend a version on a strategy the checker rejects
or, worse, on a rule that quietly expresses something else. They are listed in
OUT_OF_REACH so the gap stays written down rather than being rediscovered, and
facts.py now says which is which.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Direction:
    """One place to look, and what it would take to express it."""

    name: str
    ask: str


# Ordered. The rotation walks this list, so a reordering changes which day gets
# what and nothing else.
DIRECTIONS: tuple[Direction, ...] = (
    Direction(
        "swing holds of 2-10 days",
        "Hold for two to ten days with volatility-scaled exits, and enter on "
        "something other than a breakout. Every idea since 16 Sep has been a "
        "breakout or a trend hold; the atlas already prices those at +0.6% to "
        "+1.25% a month. A 2-10 day hold pays delivery charges (~0.21% a round "
        "trip), so the per-trade edge has to clear that, but it also keeps the "
        "money at work far more of the month than a rule that is flat 85% of "
        "the time - which is the deficit every review since 17 Sep has named.",
    ),
    Direction(
        "the short side, squared off the same day",
        "A short rule that closes before the bell, so it never pays the "
        "overnight leg. Nothing tested so far has been short at all, and the "
        "training years were a 418% bull market, so a short rule is the one "
        "family whose result cannot be beta wearing a strategy's clothes. Set "
        "session.square_off (e.g. \"15:10\"); the checker rejects a short "
        "without it.",
    ),
    Direction(
        "gap behaviour at the open",
        "What the opening bar does after an overnight gap - continuation, fade "
        "or neither - read on 60m bars, where the first bar of the session is "
        "the open. Gap CONTINUATION was tried on 16 Sep and lost; the fade "
        "direction, and conditioning on the size of the gap against the "
        "stock's own ATR, were not.",
    ),
)

# Named in facts.py as unmeasured, and not assignable until the engine can
# express them. Kept here so the reason survives the next rewrite of facts.py.
# name -> what it would take.
OUT_OF_REACH: dict[str, str] = {
    "cross-sectional relative strength": (
        "an Operand that can name another symbol, and a sweep that simulates "
        "the universe on one clock rather than one symbol per worker"
    ),
    "regime filters from index breadth": (
        "the same cross-symbol read, or a precomputed breadth series stored as "
        "its own instrument that an operand may reference"
    ),
    "time-of-day effects": (
        "a clock operand in strategy.vocabulary (bar time, or bars since the "
        "open); nothing in the grammar reads the wall clock today"
    ),
}


def direction_for(day: date, idea_no: int, *, pool: Sequence[Direction] = DIRECTIONS) -> Direction:
    """The direction assigned to idea `idea_no` of `day`.

    Rotated by the date so consecutive days start somewhere different, and
    stepped by the idea so a day that drops an idea and starts another does not
    get sent back to the same place. `idea_no` is 1-based, as the loop counts.
    """
    if not pool:
        raise ValueError("no directions to choose from")
    return pool[(day.toordinal() + idea_no - 1) % len(pool)]


def direction_block(direction: Direction) -> str:
    """How an assigned direction is put to Opus."""
    return (
        "TODAY'S DIRECTION - design for this, not for a neighbour of what "
        f"already failed:\n{direction.name}. {direction.ask}\n\n"
        "This is where to look, not a specification: the rule is yours to "
        "design, and say in your hypothesis if you think the direction is "
        "wrong. What it rules out is another version of an idea the list "
        "below has already measured."
    )
