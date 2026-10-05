"""The options atlas: classic strategies, every one a valid document (design §7)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fno import atlas  # noqa: E402
from fno.strategy import parse  # noqa: E402


def test_every_atlas_strategy_parses_for_both_underlyings():
    parsed = [parse(doc) for doc in atlas.documents()]
    assert {s.underlying for s in parsed} == {"NIFTY", "BANKNIFTY"}
    assert len({s.name for s in parsed}) == len(parsed) == 2 * len(atlas.ATLAS)
    assert all(s.mode == "positional" for s in parsed)


def test_the_design_s_list_is_all_there():
    assert set(atlas.ATLAS) == {
        "short-straddle", "short-strangle-15d", "short-strangle-20d", "short-strangle-30d",
        "iron-condor", "bull-put-spread", "bear-call-spread", "long-straddle-before-events"}


def test_the_event_straddle_buys_ahead_and_sells_after():
    s = parse(atlas.document("long-straddle-before-events", "NIFTY"))
    assert all(leg.side == "buy" for leg in s.legs)
    assert s.which == 1 and s.entry_when is not None and s.exit.when is not None
