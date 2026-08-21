"""Universe resolution inside the backtest engine.

Before this, backtest.py iterated strategy.instruments — empty for a universe
strategy — so `universe: NIFTY50` produced zero combinations and reported
success. A silent nothing that looks like a clean result is the failure mode
every other layer of this system refuses.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import resolve_strategy_symbols  # noqa: E402
from strategy_schema import parse_strategies  # noqa: E402
from universes import UniverseError  # noqa: E402


def strategy_with(**overrides):
    doc = {
        "name": "u-test", "enabled": True, "position_type": "long",
        "timeframe": "15m",
        "entry": {"all": [{"indicator": "close", "operator": ">", "value": 1}]},
        "exit": {"any": [{"indicator": "close", "operator": "<", "value": 1}]},
        "risk": {"stop_loss": {"type": "percent", "value": 1.0},
                 "target": {"type": "percent", "value": 2.0}},
        "sizing": {"type": "notional", "notional_per_trade": 100000},
    }
    doc.update(overrides)
    return parse_strategies({"version": 2, "strategies": [doc]})[0]


class FakeGroups:
    """Stands in for SupabaseStore.universe_members."""

    def __init__(self, members):
        self._members = members

    def universe_members(self, name):
        if name not in self._members:
            raise UniverseError(
                f"unknown universe {name!r}. Available: "
                + (", ".join(sorted(self._members)) or "(none)")
            )
        return self._members[name]


def test_an_instruments_strategy_is_unchanged():
    s = strategy_with(instruments=["NSE:TCS", "NSE:INFY"])
    resolved = resolve_strategy_symbols(s, FakeGroups({}))
    assert resolved.symbols == ("NSE:INFY", "NSE:TCS")
    assert resolved.universe_name is None
    assert resolved.constituents_as_of is None


def test_a_universe_strategy_resolves_to_its_members():
    s = strategy_with(universe="NIFTY3")
    groups = FakeGroups({"NIFTY3": (("NSE:A", "NSE:B", "NSE:C"), "2026-08-11")})
    resolved = resolve_strategy_symbols(s, groups)
    assert resolved.symbols == ("NSE:A", "NSE:B", "NSE:C")
    assert resolved.universe_name == "NIFTY3"
    assert resolved.constituents_as_of == "2026-08-11"


def test_an_unknown_universe_is_a_hard_error_naming_what_exists():
    s = strategy_with(universe="NIFTY_NOPE")
    groups = FakeGroups({"NIFTY50": ((), "2026-08-11")})
    with pytest.raises(UniverseError) as exc:
        resolve_strategy_symbols(s, groups)
    assert "NIFTY_NOPE" in str(exc.value)
    assert "NIFTY50" in str(exc.value)


def test_an_empty_universe_is_a_hard_error_not_an_empty_result():
    """A zero-symbol run would report success having tested nothing.

    This is the whole reason the function refuses rather than returning ().
    """
    s = strategy_with(universe="EMPTY")
    groups = FakeGroups({"EMPTY": ((), "2026-08-11")})
    with pytest.raises(UniverseError) as exc:
        resolve_strategy_symbols(s, groups)
    msg = str(exc.value)
    assert "EMPTY" in msg
    assert "zero symbols" in msg
    assert "refresh_universes" in msg


def test_resolution_needs_a_store_and_says_so_without_one():
    """`--no-db` cannot resolve a universe; failing clearly beats failing
    with an AttributeError deep inside the run."""
    s = strategy_with(universe="NIFTY50")
    with pytest.raises(UniverseError) as exc:
        resolve_strategy_symbols(s, None)
    assert "--no-db" in str(exc.value)


def test_symbols_are_sorted_so_a_run_is_reproducible():
    s = strategy_with(universe="UNSORTED")
    groups = FakeGroups({"UNSORTED": (("NSE:Z", "NSE:A", "NSE:M"), "2026-08-11")})
    assert resolve_strategy_symbols(s, groups).symbols == (
        "NSE:A", "NSE:M", "NSE:Z",
    )


# ---------------------------------------------------------------------------
# The run row: the strategy-level verdict, and what it was computed over.
# ---------------------------------------------------------------------------

import uuid  # noqa: E402

from backtest import build_run_row  # noqa: E402
from tests.test_metrics import trade  # noqa: E402


def resolved_with(symbols, universe=None, as_of=None):
    return type("R", (), {
        "symbols": tuple(symbols), "universe_name": universe,
        "constituents_as_of": as_of,
    })()


def test_the_run_row_records_what_it_was_computed_over():
    """Without this a result cannot be reproduced and its survivorship caveat
    has nowhere to live."""
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(universe="NIFTY2"),
        resolved=resolved_with(["NSE:A", "NSE:B"], "NIFTY2", "2026-08-11"),
        per_symbol={"NSE:A": [], "NSE:B": []},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["universe_name"] == "NIFTY2"
    assert row["constituents_as_of"] == "2026-08-11"
    assert row["symbols"] == ["NSE:A", "NSE:B"]
    assert row["symbols_requested"] == 2
    assert row["symbols_resolved"] == 2


def test_a_symbol_with_no_candles_is_reported_not_hidden():
    """A 1-of-2 result must not present itself as covering the whole universe."""
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(universe="NIFTY2"),
        resolved=resolved_with(["NSE:A", "NSE:B"], "NIFTY2", "2026-08-11"),
        per_symbol={"NSE:A": [trade(100)]},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing={"NSE:B"},
    )
    assert row["symbols_requested"] == 2
    assert row["symbols_resolved"] == 1
    assert row["symbols"] == ["NSE:A"]
    assert row["symbols_missing"] == ["NSE:B"]


def test_a_run_with_no_trades_is_still_recorded():
    """A strategy that traded nothing is a result, and must be visible rather
    than absent."""
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(instruments=["NSE:A"]),
        resolved=resolved_with(["NSE:A"]),
        per_symbol={"NSE:A": []},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["total_trades"] == 0
    assert row["passed_kill_rules"] is False
    assert row["sharpe_daily"] is None


def test_capital_base_scales_with_the_symbols_actually_traded():
    """notional x symbols traded: the worst case under per-symbol independent
    sizing, biasing Sharpe low rather than flattering."""
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(universe="NIFTY2"),      # notional 100_000
        resolved=resolved_with(["NSE:A", "NSE:B"], "NIFTY2", "2026-08-11"),
        per_symbol={"NSE:A": [trade(100)], "NSE:B": [trade(50)]},
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["capital_base"] == pytest.approx(200000.0)


def test_the_verdict_carries_dispersion_so_one_lucky_symbol_is_visible():
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(universe="NIFTY5"),
        resolved=resolved_with(["A", "B", "C", "D", "LUCKY"], "NIFTY5", "2026-08-11"),
        per_symbol={
            "LUCKY": [trade(1000)], "A": [trade(-50)], "B": [trade(-60)],
            "C": [trade(-40)], "D": [trade(-30)],
        },
        start_date="2024-08-09", end_date="2026-08-07",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["net_pnl"] > 0                      # the flattering headline
    assert row["symbols_profitable"] == 1          # the correction
    assert row["best_symbol"] == "LUCKY"
    assert row["passed_kill_rules"] is False       # 20% is under the 40% bar


# ---------------------------------------------------------------------------
# Multi-timeframe: the run owns the timeframe list, not the strategy.
#
# A strategy declares one timeframe, but "does this edge hold on 5m as well as
# 60m?" is a question about the RUN. Duplicating a strategy per timeframe would
# give each copy its own version history and make them impossible to compare.
# ---------------------------------------------------------------------------


def test_the_run_row_records_the_timeframe_it_was_tested_on():
    """Not the strategy's declared one - otherwise three timeframe results
    would all claim to be the strategy's own and be indistinguishable."""
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(instruments=["NSE:A"]),   # declares 15m
        resolved=resolved_with(["NSE:A"]),
        per_symbol={"NSE:A": []},
        start_date="2024-08-12", end_date="2026-08-12",
        trading_days=250, entries_skipped=0, missing=set(),
        timeframe="60m",
    )
    assert row["timeframe"] == "60m"


def test_omitting_the_timeframe_falls_back_to_the_strategy_s_own():
    row = build_run_row(
        batch_id=uuid.uuid4(),
        strategy=strategy_with(instruments=["NSE:A"]),
        resolved=resolved_with(["NSE:A"]),
        per_symbol={"NSE:A": []},
        start_date="2024-08-12", end_date="2026-08-12",
        trading_days=250, entries_skipped=0, missing=set(),
    )
    assert row["timeframe"] == "15m"


def test_pairs_expand_across_strategies_and_timeframes():
    """The expansion the engine performs: every strategy against every
    requested timeframe, each a separate result."""
    strategies = [strategy_with(instruments=["NSE:A"])]
    timeframes = ["15m", "30m", "60m"]
    pairs = [(st, tf) for st in strategies for tf in (timeframes or [st.timeframe])]
    assert [tf for _, tf in pairs] == ["15m", "30m", "60m"]

    # Without an override, exactly one pair per strategy - unchanged behaviour.
    pairs = [(st, tf) for st in strategies for tf in (None or [st.timeframe])]
    assert [tf for _, tf in pairs] == ["15m"]
