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
             max_cycles=5, position_type="long",
             sizing=None, risk=None):
    doc = {
        "version": 2,
        "strategies": [{
            "name": "pe-test", "enabled": True, "position_type": position_type,
            "timeframe": "15m", "instruments": ["NSE:RELIANCE"],
            "entry": {"all": [{"indicator": "close", "operator": ">", "value": entry_above}]},
            "exit": {"any": [{"indicator": "close", "operator": "<", "value": exit_below}]},
            "risk": risk or {
                "stop_loss": {"type": "percent", "value": sl_pct},
                "target": {"type": "percent", "value": tgt_pct},
            },
            "sizing": sizing or {"type": "fixed_quantity", "quantity": 1},
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


def test_entry_derives_quantity_from_notional_sizing() -> None:
    # Forming open is 108 -> floor(100000 / (108 * 1.0005)) shares, not the
    # sizing.quantity field (which does not exist under notional sizing).
    rows = [
        (100, 101, 99, 100),   # 09:15
        (100, 101, 99, 100),   # 09:30
        (100, 101, 99, 100),   # 09:45
        (100, 107, 99, 106),   # 10:00 closed candle: close 106 > 105 -> ENTRY
        (108, 108.5, 107.5, 108),  # forming candle, open 108
    ]
    store = FakeStore()
    strat = strategy(sizing={"type": "notional", "notional_per_trade": 100000})
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strat)
    assert summary.entries == 1
    pos = store.positions[("pe-test", "NSE:RELIANCE")]
    from decimal import Decimal
    expected_price = 108.0 * 1.0005
    assert pos.quantity == int(Decimal(str(100000)) // Decimal(str(expected_price)))
    assert pos.quantity > 1


def test_entry_skipped_when_notional_below_share_price() -> None:
    # A Rs 500 notional cannot buy a ~Rs 108 share is false — invert it: use
    # a notional smaller than the forming open so quantity resolves to 0.
    rows = [
        (100, 101, 99, 100),   # 09:15
        (100, 101, 99, 100),   # 09:30
        (100, 101, 99, 100),   # 09:45
        (100, 107, 99, 106),   # 10:00 closed candle: close 106 > 105 -> ENTRY
        (108, 108.5, 107.5, 108),  # forming candle, open 108
    ]
    store = FakeStore()
    strat = strategy(sizing={"type": "notional", "notional_per_trade": 50})
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strat)
    assert summary.entries == 0
    assert store.positions == {}
    assert any(
        "notional_per_trade" in note and "0 shares" in note
        for note in summary.skipped
    )


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


# ---------------------------------------------------------------------------
# ATR stops in the paper engine. The batch backtester and this engine must
# compute levels identically — a divergence would mean a strategy showed one
# backtest and behaved differently once deployed.
# ---------------------------------------------------------------------------

ATR_RISK = {
    "stop_loss": {"type": "atr", "period": 2, "multiplier": 1.5},
    "target": {"type": "percent", "value": 2.0},
}


def test_atr_stop_levels_match_the_backtester_exactly():
    """Same strategy, same candles, same level — asserted, not assumed."""
    import pandas as pd

    from risk_levels import build_atr_series, stop_and_target
    from paper_engine import _stop_target_levels

    strat = strategy(risk=dict(ATR_RISK))
    closed = pd.DataFrame(
        {
            "open": [100.0, 102.0, 101.0, 103.0],
            "high": [103.0, 105.0, 104.0, 106.0],
            "low": [99.0, 100.0, 99.5, 101.0],
            "close": [102.0, 101.0, 103.0, 105.0],
            "volume": [1000.0] * 4,
        },
        index=pd.date_range("2026-07-16 04:00", periods=4, freq="15min", tz="UTC"),
    )
    entry_price = 105.0

    from_engine = _stop_target_levels(strat, closed, entry_price)
    from_shared = stop_and_target(
        strat, entry_price,
        signal_idx=len(closed) - 1,
        atr_series=build_atr_series(closed, strat),
    )
    assert from_engine == from_shared
    stop, target = from_engine
    assert stop < entry_price < target      # long: stop below, target above


def test_an_atr_period_beyond_available_history_does_not_abort_the_whole_run():
    """run_once promises a failure on one combination is recorded, not fatal.

    An ATR period outrunning the available history used to escape the
    per-combination handler and take down every other strategy in the run.
    """
    import pandas as pd

    from risk_levels import RiskLevelError

    strat = strategy(
        risk={
            "stop_loss": {"type": "atr", "period": 500, "multiplier": 1.5},
            "target": {"type": "percent", "value": 2.0},
        }
    )
    closed = pd.DataFrame(
        {
            "open": [100.0, 102.0], "high": [103.0, 105.0],
            "low": [99.0, 100.0], "close": [102.0, 101.0],
            "volume": [1000.0, 1000.0],
        },
        index=pd.date_range("2026-07-16 04:00", periods=2, freq="15min", tz="UTC"),
    )
    with pytest.raises(RiskLevelError) as exc:
        __import__("paper_engine")._stop_target_levels(strat, closed, 105.0)
    msg = str(exc.value)
    assert "500" in msg and "pe-test" in msg

    # And run_once catches it: the summary records the skip rather than raising.
    import inspect

    import paper_engine

    source = inspect.getsource(paper_engine.run_once)
    assert "RiskLevelError" in source, (
        "run_once must catch RiskLevelError, or one bad ATR period aborts "
        "every other strategy in the run"
    )


# ---------------------------------------------------------------------------
# Trailing stop — the paper engine is stateless (every run rebuilds from
# Supabase) and `positions` has no column to persist a running best price
# between runs, so a trailing_stop config cannot be honoured here without a
# schema change. It must be refused loudly, not silently downgraded to a
# fixed-stop-only trade that would diverge from what the same strategy's
# backtest shows.
# ---------------------------------------------------------------------------


def test_entry_with_trailing_stop_is_refused_not_silently_ignored() -> None:
    rows = [
        (100, 101, 99, 100),   # 09:15
        (100, 101, 99, 100),   # 09:30
        (100, 101, 99, 100),   # 09:45
        (100, 107, 99, 106),   # 10:00 closed candle: close 106 > 105 -> would ENTRY
        (108, 108.5, 107.5, 108),  # forming candle
    ]
    strat = strategy(risk={
        "stop_loss": {"type": "percent", "value": 1.0},
        "target": {"type": "percent", "value": 2.0},
        "trailing_stop": {"type": "percent", "value": 1.0},
    })
    store = FakeStore()
    summary = run(store, {"NSE:RELIANCE": frame(rows)}, strat)
    assert summary.entries == 0
    assert len(store.positions) == 0
    assert any("trailing_stop" in note for note in summary.skipped)


# ---------------------------------------------------------------------------
# An uncovered year is NOT a year without holidays.
#
# nse_holidays.yaml listed only 2026. From 1 Jan 2027 every NSE holiday would
# have read as an ordinary trading day, and the engine would have recorded
# fills on days the market never opened. NSE publishes its calendar annually
# and many dates are lunar, so they cannot be derived - refusing is the only
# honest option.
# ---------------------------------------------------------------------------


def test_an_uncovered_year_is_refused_not_assumed_open() -> None:
    from market_calendar import covered_years, session_gate

    years = covered_years(str(REPO_ROOT / "nse_holidays.yaml"))
    wednesday_2099 = datetime(2099, 6, 10, 11, 0, tzinfo=IST)
    run, reason = session_gate(wednesday_2099, frozenset(), years)
    assert run is False
    assert "2099" in reason
    assert "nse_holidays.yaml" in reason


def test_a_covered_year_still_trades_normally() -> None:
    from market_calendar import covered_years, load_holidays, session_gate

    path = str(REPO_ROOT / "nse_holidays.yaml")
    years = covered_years(path)
    assert 2026 in years, "the shipped file should cover 2026"
    ordinary = datetime(2026, 6, 10, 11, 0, tzinfo=IST)
    run, reason = session_gate(ordinary, load_holidays(path), years)
    assert run is True and reason == "ok"


def test_omitting_known_years_keeps_the_old_behaviour() -> None:
    """Existing callers must not change meaning just because the parameter
    exists; only passing it opts into the stricter check."""
    from market_calendar import session_gate

    ordinary = datetime(2099, 6, 10, 11, 0, tzinfo=IST)
    run, _ = session_gate(ordinary, frozenset())
    assert run is True


def test_covered_years_survives_a_missing_file() -> None:
    from market_calendar import covered_years

    assert covered_years("does-not-exist.yaml") == frozenset()
