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


def test_the_atlas_measures_at_a_quarter_of_the_balance_in_margin():
    """At 100% every short-option strategy ruins itself (BANKNIFTY straddle, Jan 2016:
    12x leverage, -64% in two weeks), which measures leverage, not the strategy."""
    assert all(parse(doc).max_margin_pct == atlas.MARGIN_PCT == 25.0 for doc in atlas.documents())


def test_every_intraday_atlas_strategy_parses_for_both_underlyings():
    parsed = [parse(doc) for doc in atlas.intraday_documents()]
    assert len(parsed) == 2 * len(atlas.INTRADAY)
    assert all(s.mode == "intraday" and s.exit.time == atlas.EXIT_TIME for s in parsed)
    assert {s.window for s in parsed} >= {("09:20", "09:21"), ("09:45", "13:00")}


def test_intraday_sizing_keeps_one_stop_small_not_a_quarter_of_margin():
    """A quarter of Rs 5 lakh (Rs 1.25 lakh) cannot buy one NIFTY straddle set on expiry
    day (about Rs 2.5 lakh), so the quarter rule would measure nothing intraday; and full
    margin on a cheap position (an iron fly, a bought option) would bet the account."""
    sizing = {name: parse(atlas.intraday_document(name, "NIFTY")).max_margin_pct for name in atlas.INTRADAY}
    assert sizing["straddle-0920"] == sizing["strangle-0930"] == sizing["expiry-day-straddle"] == 100.0
    assert sizing["iron-fly"] == sizing["orb-call"] == sizing["orb-put"] == 10.0
