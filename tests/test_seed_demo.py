"""Tests for seed_demo.py's pure row builders (no network).

These guard that the demo rows match the real table schema in sql/001_init.sql
so seeding can never fail on a constraint, and that the demo markers are
present so `--clear` can always remove exactly what was inserted.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from seed_demo import (  # noqa: E402
    DEMO_AUDIT_REASON_PREFIX,
    DEMO_PREFIX,
    build_all,
)

UTC = timezone.utc

# Constraints copied from sql/001_init.sql so a schema change that would break
# seeding trips a test here first.
VALID_EXIT_REASONS = {"signal", "stop_loss", "target", "end_of_day"}
VALID_STATUS = {"ok", "skipped", "error"}
VALID_RUN_TYPES = {"paper", "backtest", "login", "selftest"}

# A weekday mid-session moment so "today" trades are generated.
NOW = datetime(2026, 7, 17, 6, 0, tzinfo=UTC)  # Fri, 11:30 IST


def test_build_all_is_deterministic() -> None:
    a = build_all(NOW, seed=7)
    b = build_all(NOW, seed=7)
    assert a == b


def test_all_rows_carry_demo_markers() -> None:
    data = build_all(NOW)
    for table in ("strategies",):
        assert all(r["name"].startswith(DEMO_PREFIX) for r in data[table])
    for table in ("trades", "positions", "backtest_results"):
        assert all(r["strategy_name"].startswith(DEMO_PREFIX) for r in data[table])
    assert all(r["reason"].startswith(DEMO_AUDIT_REASON_PREFIX) for r in data["run_audit"])


def test_trades_match_schema_and_pnl_math() -> None:
    trades = build_all(NOW)["trades"]
    assert trades, "expected some demo trades"
    for t in trades:
        assert t["exit_reason"] in VALID_EXIT_REASONS
        assert t["position_type"] in {"long", "short"}
        assert t["quantity"] >= 1
        # net = gross - costs (both rounded to 4 dp)
        assert t["net_pnl"] == round(t["gross_pnl"] - t["costs"], 4)
        # timestamps are ISO strings with an explicit UTC offset
        for col in ("entry_fill_ts", "exit_fill_ts", "entry_signal_candle_ts"):
            assert t[col].endswith("+00:00")


def test_some_trades_exit_today_ist() -> None:
    trades = build_all(NOW)["trades"]
    today_ist = NOW.astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).date()
    exit_dates = {
        datetime.fromisoformat(t["exit_fill_ts"])
        .astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata"))
        .date()
        for t in trades
    }
    assert today_ist in exit_dates, "dashboard 'today' panels would be empty"


def test_positions_are_unique_per_strategy_instrument() -> None:
    positions = build_all(NOW)["positions"]
    keys = [(p["strategy_name"], p["instrument"]) for p in positions]
    assert len(keys) == len(set(keys))  # satisfies the positions unique constraint
    for p in positions:
        assert p["stop_loss_price"] < p["entry_price"] < p["target_price"]  # long


def test_trades_unique_per_entry_signal_candle() -> None:
    trades = build_all(NOW)["trades"]
    keys = [
        (t["strategy_name"], t["instrument"], t["entry_signal_candle_ts"]) for t in trades
    ]
    assert len(keys) == len(set(keys))  # satisfies trades unique constraint


def test_audit_rows_valid() -> None:
    audits = build_all(NOW)["run_audit"]
    assert audits[0]["status"] == "ok"  # newest run is green
    for a in audits:
        assert a["status"] in VALID_STATUS
        assert a["run_type"] in VALID_RUN_TYPES


def test_backtest_rows_valid_and_flags_consistent() -> None:
    rows = build_all(NOW)["backtest_results"]
    assert rows
    batch_ids = {r["batch_id"] for r in rows}
    assert len(batch_ids) == 1  # one batch
    for r in rows:
        assert r["winning_trades"] <= r["total_trades"]
        computed = all(f["passed"] for f in r["kill_rule_flags"].values())
        assert r["passed_kill_rules"] == computed
