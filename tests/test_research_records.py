"""Turning a finished run into the rows the database stores."""

from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.lakh import LakhResult  # noqa: E402
from research.records import combo_rows, equity_rows, locked_trade_rows, run_row  # noqa: E402
from research.sweep import ComboResult  # noqa: E402
from research_helpers import FREE, ist, trade  # noqa: E402

UTC = timezone.utc
LOCKED = LakhResult(start_value=100_000.0, end_value=107_461.0, trades=6,
                    winning_trades=4, worst_dip_pct=7.1, cagr_pct=7.4)


def base_run(**over):
    kwargs = dict(
        started_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 12, 1, 20, tzinfo=UTC),
        status="completed",
        data_end=date(2026, 7, 31),
        locked_from=date(2025, 8, 1),
        strategy_name="N200-PULLBACK-DAY",
        pick_symbol="NSE:360ONE",
        pick_timeframe="day",
        locked=LOCKED,
        hold_end_value=107_622.0,
        combos_profitable=4,
        combos_tested=18,
        warnings=["prices frozen at 2026-07-31"],
    )
    kwargs.update(over)
    return run_row(**kwargs)


def test_run_row_carries_the_verdict_columns():
    row = base_run()
    assert row["lakh_end_value"] == 107_461.0
    assert row["hold_end_value"] == 107_622.0
    assert row["win_rate_pct"] == pytest.approx(66.67, abs=0.01)
    assert row["trades_per_month"] == pytest.approx(0.5, abs=0.01)
    assert row["locked_to"] == date(2026, 7, 31)


def test_verdict_fails_on_too_few_trades_even_when_profitable():
    """Six trades is under the ten the design requires."""
    assert base_run()["verdict_passed"] is False


def test_verdict_passes_when_all_three_conditions_hold():
    good = LakhResult(start_value=100_000.0, end_value=108_400.0, trades=38,
                      winning_trades=17, worst_dip_pct=6.1, cagr_pct=8.4)
    assert base_run(locked=good)["verdict_passed"] is True


def test_beat_holding_is_separate_from_the_verdict():
    row = base_run()
    assert row["beat_holding"] is False
    assert base_run(hold_end_value=100_000.0)["beat_holding"] is True


def test_a_run_with_no_qualifying_pick_still_makes_a_row():
    row = run_row(
        started_at=datetime(2026, 9, 12, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 9, 12, 1, 20, tzinfo=UTC),
        status="completed", data_end=date(2026, 7, 31), locked_from=date(2025, 8, 1),
        strategy_name="X", pick_symbol=None, pick_timeframe=None, locked=None,
        hold_end_value=None, combos_profitable=0, combos_tested=1177, warnings=[],
    )
    assert row["pick_symbol"] is None
    assert row["lakh_end_value"] is None
    assert row["verdict_passed"] is False
    assert row["beat_holding"] is None
    assert row["combos_tested"] == 1177


def test_combo_rows_keep_skips_with_their_reason():
    results = [
        ComboResult("NSE:A", "day", False, (trade(entry=ist(2025, 1, 2), exit_=ist(2025, 1, 2, 14)),)),
        ComboResult("NSE:B", "60m", False, (), "no candles in window"),
    ]
    rows = combo_rows("run-1", results, FREE, window_days_for=lambda r: 730)
    assert rows[0]["symbol"] == "NSE:A" and rows[0]["skipped_reason"] is None
    assert rows[0]["trades"] == 1 and rows[0]["cagr_pct"] is not None
    assert rows[1]["skipped_reason"] == "no candles in window"
    assert rows[1]["trades"] == 0 and rows[1]["cagr_pct"] is None


def test_locked_trade_rows_carry_the_running_balance():
    trades = [
        trade(entry=ist(2025, 8, 4, 10), exit_=ist(2025, 8, 4, 14), entry_price=100.0, exit_price=110.0),
        trade(entry=ist(2025, 8, 5, 10), exit_=ist(2025, 8, 5, 14), entry_price=100.0, exit_price=90.0),
    ]
    rows = locked_trade_rows("run-1", trades, FREE)
    assert [r["side"] for r in rows] == ["long", "long"]
    assert rows[0]["balance_after"] == pytest.approx(110_000.0)
    assert rows[1]["balance_after"] == pytest.approx(99_000.0)
    assert rows[0]["net_return_pct"] == pytest.approx(10.0)
    assert rows[1]["net_return_pct"] == pytest.approx(-10.0)


def test_equity_rows_tag_every_point_with_the_run():
    rows = equity_rows("run-1", [{"day": date(2025, 8, 1), "lakh_balance": 100_000.0}])
    assert rows == [{"run_id": "run-1", "day": date(2025, 8, 1), "lakh_balance": 100_000.0}]
