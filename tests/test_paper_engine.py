"""Tests for market_calendar.py and paper_engine.py's core run logic.

The engine is exercised through fakes that faithfully mimic the two DB
unique constraints (one open position per strategy+instrument; one trade
per entry-signal candle) — the same guarantees sql/001_init.sql provides.
"""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Settings  # noqa: E402
from db import ClosedTrade, OpenPosition  # noqa: E402
from market_calendar import (  # noqa: E402
    CalendarError,
    is_trading_day,
    load_holidays,
    session_gate,
)
from paper_engine import RunSummary, run_once  # noqa: E402
from strategy_schema import parse_strategies  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

REPO_ROOT = Path(__file__).resolve().parent.parent

SETTINGS = Settings(
    kite_api_key="k", kite_api_secret="s",
    supabase_url="https://x.supabase.co", supabase_service_role_key="key",
    slippage_pct=0.05, cost_per_trade_inr=30.0,
)


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------


def ist(y, m, d, hh, mm) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=IST)


def test_shipped_holiday_file_loads() -> None:
    holidays = load_holidays(str(REPO_ROOT / "nse_holidays.yaml"))
    assert date(2026, 1, 26) in holidays  # Republic Day
    assert all(isinstance(d, date) for d in holidays)


def test_trading_day_logic() -> None:
    holidays = frozenset({date(2026, 1, 26)})
    assert is_trading_day(date(2026, 7, 17), holidays)       # Friday
    assert not is_trading_day(date(2026, 7, 18), holidays)   # Saturday
    assert not is_trading_day(date(2026, 7, 19), holidays)   # Sunday
    assert not is_trading_day(date(2026, 1, 26), holidays)   # holiday (Monday)


@pytest.mark.parametrize(
    "now, should_run, reason_fragment",
    [
        (ist(2026, 7, 18, 11, 0), False, "Saturday"),
        (ist(2026, 7, 19, 11, 0), False, "Sunday"),
        (ist(2026, 1, 26, 11, 0), False, "holiday"),
        (ist(2026, 7, 17, 9, 15), False, "before"),      # no closed candle yet
        (ist(2026, 7, 17, 9, 29), False, "before"),
        (ist(2026, 7, 17, 9, 30), True, "ok"),           # first candle closed
        (ist(2026, 7, 17, 12, 0), True, "ok"),
        (ist(2026, 7, 17, 15, 50), True, "ok"),          # grace window edge
        (ist(2026, 7, 17, 15, 51), False, "after"),
        (ist(2026, 7, 17, 20, 0), False, "after"),
    ],
)
def test_session_gate(now, should_run, reason_fragment) -> None:
    holidays = frozenset({date(2026, 1, 26)})
    run, reason = session_gate(now, holidays)
    assert run is should_run
    assert reason_fragment.lower() in reason.lower()


def test_bad_holiday_file_fails_loudly(tmp_path: Path) -> None:
    bad = tmp_path / "h.yaml"
    bad.write_text("holidays:\n  2026:\n    - 'not-a-date'\n")
    with pytest.raises(CalendarError, match="not an ISO date"):
        load_holidays(str(bad))

    wrong_year = tmp_path / "h2.yaml"
    wrong_year.write_text("holidays:\n  2025:\n    - 2026-01-26\n")
    with pytest.raises(CalendarError, match="listed under year 2025"):
        load_holidays(str(wrong_year))


# ---------------------------------------------------------------------------
# Fakes mirroring the DB constraints
# ---------------------------------------------------------------------------


class FakeStore:
    def __init__(self):
        self.positions: dict[tuple[str, str], OpenPosition] = {}
        self.trades: list[ClosedTrade] = []
        self._trade_keys: set[tuple] = set()

    def list_open_positions(self):
        return list(self.positions.values())

    def open_position(self, **kw) -> OpenPosition | None:
        key = (kw["strategy_name"], kw["instrument"])
        if key in self.positions:
            return None  # UNIQUE(strategy_name, instrument)
        pos = OpenPosition(id=str(uuid.uuid4()), **kw)
        self.positions[key] = pos
        return pos

    def close_position(self, position: OpenPosition, trade: ClosedTrade) -> bool:
        tkey = (trade.strategy_name, trade.instrument, trade.entry_signal_candle_ts)
        recorded = tkey not in self._trade_keys  # UNIQUE per entry signal
        if recorded:
            self._trade_keys.add(tkey)
            self.trades.append(trade)
        self.positions.pop((position.strategy_name, position.instrument), None)
        return recorded

    def completed_cycles_between(self, strategy_name, instrument, start, end) -> int:
        return sum(
            1 for t in self.trades
            if t.strategy_name == strategy_name and t.instrument == instrument
            and start <= t.entry_fill_ts < end
        )


class FakeClient:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames  # instrument -> full frame incl. forming candle

    def resolve_instrument_tokens(self, instruments, today_ist):
        return {inst: i + 1 for i, inst in enumerate(sorted(instruments))}

    def fetch_historical_candles(self, token, timeframe, from_utc, to_utc, *,
                                 closed_only=True, now_utc=None):
        # Engine always calls with closed_only=False; return the raw frame.
        inst = sorted(self.frames)[token - 1]
        return self.frames[inst]


# ---------------------------------------------------------------------------
# Engine scenario helpers
# ---------------------------------------------------------------------------

# Fixed run moment: Friday 2026-07-17, 10:15:30 IST. Last CLOSED 15m candle
# is 10:00-10:15 (starts 10:00); the forming candle started at 10:15.
NOW = ist(2026, 7, 17, 10, 15).replace(second=30).astimezone(UTC)
SESSION_START = ist(2026, 7, 17, 9, 15)


def frame(rows: list[tuple]) -> pd.DataFrame:
    """rows = [(open, high, low, close)] starting 09:15 IST, 15m spacing.
    The LAST row is the forming candle (starts at 09:15 + 15*(n-1) min)."""
    arr = np.asarray(rows, dtype=float)
    index = pd.DatetimeIndex(
        [(SESSION_START + timedelta(minutes=15 * i)).astimezone(UTC) for i in range(len(rows))]
    )
    return pd.DataFrame(
        {"open": arr[:, 0], "high": arr[:, 1], "low": arr[:, 2],
         "close": arr[:, 3], "volume": np.full(len(rows), 1000.0)},
        index=index,
    )


def strategy(*, entry_above=105.0, exit_below=90.0, sl_pct=1.0, tgt_pct=2.0,
             max_cycles=5, position_type="long"):
    doc = {
        "version": 2,
        "strategies": [{
            "name": "pe-test", "enabled": True, "position_type": position_type,
            "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
            "entry": {"all": [{"indicator": "close", "operator": ">", "value": entry_above}]},
            "exit": {"any": [{"indicator": "close", "operator": "<", "value": exit_below}]},
            "risk": {
                "stop_loss": {"type": "percent", "value": sl_pct},
                "target": {"type": "percent", "value": tgt_pct},
            },
            "sizing": {"type": "fixed_quantity", "quantity": 1},
            "max_cycles_per_day": max_cycles,
        }],
    }
    return parse_strategies(doc)[0]


def run(store, frames, strat) -> RunSummary:
    return run_once(
        now_utc=NOW, settings=SETTINGS, store=store,
        client=FakeClient(frames), strategies=[strat],
    )


def seed_position(store: FakeStore, *, entry_price=100.0, sl=99.0, tgt=102.0,
                  position_type="long") -> OpenPosition:
    ts = ist(2026, 7, 17, 9, 30).astimezone(UTC)
    pos = store.open_position(
        strategy_name="pe-test", instrument="NSE:RELIANCE",
        position_type=position_type, quantity=1,
        entry_signal_candle_ts=ts, entry_fill_ts=ts + timedelta(minutes=15),
        intended_entry_price=entry_price, entry_price=entry_price,
        stop_loss_price=sl, target_price=tgt,
    )
    assert pos is not None
    return pos


# Candles 09:15..10:00 (closed) + forming 10:15. Quiet prices below any
# trigger; individual tests override the tail.
QUIET = [(100, 101, 99, 100)] * 5  # 09:15, 09:30, 09:45, 10:00 closed + forming


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def test_entry_opens_position_at_forming_open_with_slippage() -> None:
    rows = [
        (100, 101, 99, 100),   # 09:15
        (100, 101, 99, 100),   # 09:30
        (100, 101, 99, 100),   # 09:45
        (100, 107, 99, 106),   # 10:00 closed candle: close 106 > 105 -> ENTRY
        (108, 108.5, 107.5, 108),  # forming candle, open 108
    ]
    store = FakeStore()
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert summary.entries == 1 and summary.exits == 0
    pos = store.positions[("pe-test", "NSE:RELIANCE")]
    assert pos.entry_signal_candle_ts == ist(2026, 7, 17, 10, 0).astimezone(UTC)
    assert pos.entry_fill_ts == ist(2026, 7, 17, 10, 15).astimezone(UTC)
    assert pos.intended_entry_price == 108.0
    assert pos.entry_price == pytest.approx(108.0 * 1.0005, abs=1e-3)
    assert pos.stop_loss_price == pytest.approx(pos.entry_price * 0.99, abs=1e-3)
    assert pos.target_price == pytest.approx(pos.entry_price * 1.02, abs=1e-3)


def test_rerun_same_candle_is_idempotent() -> None:
    rows = QUIET[:3] + [(100, 107, 99, 106), (108, 109, 107, 108)]
    store = FakeStore()
    frames = {"NSE:RELIANCE": frame(rows)}
    first = run(store, frames, strategy())
    second = run(store, frames, strategy())  # same candle, same everything
    assert first.entries == 1
    assert second.entries == 0
    assert len(store.positions) == 1
    assert store.trades == []


def test_entry_skipped_when_no_forming_candle() -> None:
    # Signal candle is the last row: nothing has started after it.
    rows = QUIET[:3] + [(100, 107, 99, 106)]
    store = FakeStore()
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert summary.entries == 0
    assert store.positions == {}
    assert any("final candle" in note for note in summary.skipped)


def test_max_cycles_per_day_blocks_entry() -> None:
    rows = QUIET[:3] + [(100, 107, 99, 106), (108, 109, 107, 108)]
    store = FakeStore()
    strat = strategy(max_cycles=1)
    # One completed cycle already today.
    pos = seed_position(store)
    trade_ts = ist(2026, 7, 17, 9, 45).astimezone(UTC)
    store.close_position(
        pos,
        ClosedTrade(
            strategy_name="pe-test", instrument="NSE:RELIANCE", position_type="long",
            quantity=1, entry_signal_candle_ts=pos.entry_signal_candle_ts,
            entry_fill_ts=pos.entry_fill_ts, intended_entry_price=100.0,
            entry_price=100.0, exit_signal_candle_ts=trade_ts, exit_fill_ts=trade_ts,
            intended_exit_price=101.0, exit_price=101.0, exit_reason="target",
            gross_pnl=1.0, costs=30.0, net_pnl=-29.0,
        ),
    )
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strat)
    assert summary.entries == 0
    assert any("max_cycles_per_day" in note for note in summary.skipped)


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------


def test_stop_loss_hit_closes_at_level() -> None:
    rows = QUIET[:3] + [(100, 100.5, 98.5, 100), (100, 101, 99, 100)]
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=99.0, tgt=102.0)  # low 98.5 <= 99
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert summary.exits == 1
    assert store.positions == {}
    t = store.trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.intended_exit_price == 99.0                      # the level
    assert t.exit_price == pytest.approx(99.0 * 0.9995)      # sell slippage
    assert t.net_pnl == pytest.approx(t.gross_pnl - 30.0)


def test_gap_open_through_stop_fills_at_open() -> None:
    rows = QUIET[:3] + [(95, 96, 94, 95), (95, 96, 94, 95)]  # opens at 95 << SL 99
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=99.0, tgt=102.0)
    run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    t = store.trades[0]
    assert t.exit_reason == "stop_loss"
    assert t.intended_exit_price == 95.0  # the open, not the level


def test_stop_checked_before_target_in_wide_candle() -> None:
    rows = QUIET[:3] + [(100, 103, 98, 100), (100, 101, 99, 100)]  # spans both
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=99.0, tgt=102.0)
    run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert store.trades[0].exit_reason == "stop_loss"


def test_rule_exit_fills_at_forming_open() -> None:
    # Candle closes at 85 (< 90 exit rule) without touching SL/target band.
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=1.0, tgt=1000.0)
    rows = QUIET[:3] + [(100, 101, 85, 85), (84, 85, 83, 84)]
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert summary.exits == 1
    t = store.trades[0]
    assert t.exit_reason == "signal"
    assert t.exit_signal_candle_ts == ist(2026, 7, 17, 10, 0).astimezone(UTC)
    assert t.exit_fill_ts == ist(2026, 7, 17, 10, 15).astimezone(UTC)
    assert t.intended_exit_price == 84.0
    assert t.exit_price == pytest.approx(84.0 * 0.9995)


def test_rule_exit_on_final_candle_is_deferred() -> None:
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=1.0, tgt=1000.0)
    rows = QUIET[:3] + [(100, 101, 85, 85)]  # exit signal, no forming candle
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strategy())
    assert summary.exits == 0
    assert len(store.positions) == 1  # still open, re-evaluated next day
    assert any("final candle" in note for note in summary.skipped)


def test_no_same_run_reentry_after_exit() -> None:
    # Exit fires AND the entry condition is true on the same closed candle:
    # the engine must exit only; re-entry is left to the next run.
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=1.0, tgt=1000.0)
    strat = strategy(entry_above=80.0, exit_below=90.0)  # both true at close 85
    rows = QUIET[:3] + [(100, 101, 85, 85), (84, 85, 83, 84)]
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strat)
    assert summary.exits == 1
    assert summary.entries == 0
    assert store.positions == {}


def test_short_position_stop_and_target_sides() -> None:
    # Short entered at 100: SL above (101), target below (98).
    store = FakeStore()
    seed_position(store, entry_price=100.0, sl=101.0, tgt=98.0, position_type="short")
    rows = QUIET[:3] + [(100, 100.5, 97.5, 100), (100, 101, 99, 100)]  # low 97.5 <= 98
    run(store, {"NSE:RELIANCE": frame(rows)}, strategy(position_type="short"))
    t = store.trades[0]
    assert t.exit_reason == "target"
    assert t.intended_exit_price == 98.0
    assert t.exit_price == pytest.approx(98.0 * 1.0005)  # buy-back pays slippage
    assert t.gross_pnl == pytest.approx(100.0 - t.exit_price, abs=1e-3)
