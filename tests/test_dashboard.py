"""Tests for dashboard.py's pure helpers (no Streamlit runtime needed).

Importing dashboard.py must NOT start the app — the module guards its
Streamlit body behind runtime detection, and these tests prove the pure
parts work standalone.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dashboard import (  # noqa: E402  (import also proves no app side-effects)
    equity_and_drawdown,
    is_forbidden_key,
    leaderboard,
    to_ist_display,
    todays_trades,
)

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc


def make_jwt(role: str) -> str:
    """Structurally valid unsigned JWT with a role claim (test double)."""
    def b64(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'HS256'})}.{b64({'role': role, 'iss': 'supabase'})}.sig"


# ---------------------------------------------------------------------------
# Key tripwire
# ---------------------------------------------------------------------------


def test_service_role_jwt_is_forbidden() -> None:
    assert is_forbidden_key(make_jwt("service_role"))


def test_anon_jwt_is_allowed() -> None:
    assert not is_forbidden_key(make_jwt("anon"))


def test_new_style_keys() -> None:
    assert is_forbidden_key("sb_secret_abc123")
    assert not is_forbidden_key("sb_publishable_abc123")


def test_unparseable_key_is_allowed_through() -> None:
    # RLS is the real enforcement; the tripwire must not false-positive.
    assert not is_forbidden_key("not.a-real.jwt")
    assert not is_forbidden_key("plainstring")


# ---------------------------------------------------------------------------
# IST display conversion
# ---------------------------------------------------------------------------


def test_to_ist_display_converts_and_drops_tz() -> None:
    out = to_ist_display(pd.Series(["2026-07-17T04:00:00+00:00"]))
    assert out.iloc[0] == pd.Timestamp("2026-07-17 09:30:00")  # naive IST
    assert out.dt.tz is None


# ---------------------------------------------------------------------------
# Aggregations
# ---------------------------------------------------------------------------

NOW = datetime(2026, 7, 17, 6, 0, tzinfo=UTC)  # 11:30 IST on 17 Jul


def trades_frame(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    """rows = [(strategy_name, exit_iso_utc, net_pnl)]"""
    return pd.DataFrame(
        {
            "strategy_name": [r[0] for r in rows],
            "exit_fill_ts": [r[1] for r in rows],
            "net_pnl": [r[2] for r in rows],
        }
    )


def test_leaderboard_aggregates_and_sorts() -> None:
    trades = trades_frame(
        [
            ("A", "2026-07-16T05:00:00+00:00", 100.0),
            ("A", "2026-07-17T05:00:00+00:00", -40.0),   # today IST
            ("B", "2026-07-17T05:30:00+00:00", 500.0),   # today IST
        ]
    )
    lb = leaderboard(trades, NOW)
    assert list(lb["strategy"]) == ["B", "A"]  # sorted by net_pnl desc
    a = lb[lb["strategy"] == "A"].iloc[0]
    assert a["trades"] == 2
    assert a["win_rate_pct"] == 50.0
    assert a["net_pnl"] == 60.0
    assert a["today_pnl"] == -40.0
    assert lb[lb["strategy"] == "B"].iloc[0]["today_pnl"] == 500.0


def test_leaderboard_empty() -> None:
    lb = leaderboard(pd.DataFrame(), NOW)
    assert lb.empty and "net_pnl" in lb.columns


def test_equity_and_drawdown_hand_computation() -> None:
    trades = trades_frame(
        [
            ("A", "2026-07-15T05:00:00+00:00", 100.0),
            ("A", "2026-07-16T05:00:00+00:00", -60.0),
            ("A", "2026-07-17T05:00:00+00:00", 30.0),
        ]
    )
    curve = equity_and_drawdown(trades)
    assert list(curve["equity"]) == [100.0, 40.0, 70.0]
    assert list(curve["drawdown"]) == [0.0, -60.0, -30.0]  # vs peak of 100
    # Index shows IST wall time: 05:00 UTC == 10:30 IST.
    assert curve.index[0] == pd.Timestamp("2026-07-15 10:30:00")


def test_equity_sorts_out_of_order_input() -> None:
    trades = trades_frame(
        [
            ("A", "2026-07-17T05:00:00+00:00", 50.0),   # later trade listed first
            ("A", "2026-07-15T05:00:00+00:00", 10.0),
        ]
    )
    curve = equity_and_drawdown(trades)
    assert list(curve["equity"]) == [10.0, 60.0]


def test_todays_trades_uses_ist_calendar_day() -> None:
    # 2026-07-16 20:00 UTC is 01:30 IST on the 17th -> counts as "today".
    trades = trades_frame(
        [
            ("A", "2026-07-16T20:00:00+00:00", 10.0),   # today in IST terms
            ("A", "2026-07-16T05:00:00+00:00", 20.0),   # yesterday IST
            ("A", "2026-07-17T05:00:00+00:00", 30.0),   # today
        ]
    )
    today = todays_trades(trades, NOW)
    assert sorted(today["net_pnl"]) == [10.0, 30.0]
