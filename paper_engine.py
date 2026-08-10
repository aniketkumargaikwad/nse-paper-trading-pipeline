"""The stateless paper-trading engine — one run per 15-minute cron tick.

Every run starts with empty memory and rebuilds its world from Supabase:
the day's Kite token, the validated strategies, and any open positions.
It then evaluates the JUST-CLOSED candle for each strategy x instrument,
simulates fills, and writes results back. Simulation only — there is no
code path to any order endpoint anywhere in this system.

RUN SHAPE
---------
1. Gate: weekend / NSE holiday / outside 09:30-15:50 IST -> log, write a
   run_audit row with status=skipped, exit 0. This is the NORMAL result
   outside market hours, not an error.
2. Load token + strategies + open positions; resolve instrument tokens
   (cache-first).
3. Per strategy x instrument, on the last CLOSED candle:
     a. open position?  check stop-loss/target against that candle's range
        (stop before target — worst case; gaps fill at the open), else
        check the exit rules (fill at the next candle's open);
     b. flat?           check the entry rules (fill at the next candle's
        open), respecting max_cycles_per_day.
   "Next candle's open" is the open of the candle that just STARTED — a
   price fixed at candle start, so using it cannot repaint. On the day's
   final candle there is no next open; entry/rule-exit signals there are
   skipped (logged), while stop/target checks still apply.
4. Write a run_audit row with counts.

IDEMPOTENCY (the reason re-runs are safe)
-----------------------------------------
* positions has a UNIQUE(strategy_name, instrument): a re-run's duplicate
  entry insert is rejected and treated as "already done".
* trades has a UNIQUE(strategy_name, instrument, entry_signal_candle_ts):
  a re-run's duplicate close is rejected and treated as "already recorded".
Running the engine twice for the same candle therefore changes nothing,
which also makes it safe that 30m/60m strategies are re-evaluated on every
15m tick between their candle closes.

Note one deliberate divergence from the backtester: after an exit, re-entry
for that instrument can happen no earlier than the NEXT run (the backtester
can re-enter on the next candle). Slightly conservative, much simpler.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Sequence

import numpy as np
import pandas as pd

import indicators
import signals
# Reused rather than reimplemented: the batch backtester (backtest.py) and this
# engine must compute a stop/target level identically, or the same strategy
# could show a profitable backtest and a different live behaviour. Both the
# hard-error-on-insufficient-history rule and the ATR-at-the-signal-candle
# rule live in ONE place (backtest._level_from_spec) for exactly that reason.
from risk_levels import RiskLevelError, build_atr_series, stop_and_target
from config import IST, TIMEFRAME_MINUTES, UTC, Settings, get_settings
from data_provider import create_data_client, describe_provider
from db import ClosedTrade, OpenPosition, SupabaseStore
from kite_client import (
    KiteClientError,
    MarketDataClient,
    TokenExpiredError,
    drop_forming_candle,
    lookback_start_utc,
)
from market_calendar import load_holidays, session_gate
from strategy_schema import Strategy, load_strategy_documents, resolve_quantity


@dataclass
class RunSummary:
    """What one engine run did — becomes the run_audit row's details."""

    candle_ts: datetime | None = None   # newest closed candle seen (any tf)
    checked: int = 0                    # strategy x instrument combos evaluated
    entries: int = 0
    exits: int = 0
    skipped: list[str] = field(default_factory=list)  # human-readable notes


# ---------------------------------------------------------------------------
# Fill helpers (same conventions as backtest.py)
# ---------------------------------------------------------------------------


def _entry_fill_price(price: float, position_type: str, slippage_pct: float) -> float:
    """Long entry is a buy (pays more); short entry is a sell (receives less)."""
    slip = slippage_pct / 100.0
    return price * (1 + slip) if position_type == "long" else price * (1 - slip)


def _exit_fill_price(price: float, position_type: str, slippage_pct: float) -> float:
    """Long exit is a sell; short exit is a buy-back."""
    slip = slippage_pct / 100.0
    return price * (1 - slip) if position_type == "long" else price * (1 + slip)


def _check_stop_target(position: OpenPosition, candle: pd.Series) -> tuple[float, str] | None:
    """Did the closed candle hit the stop or target? -> (intended_price, reason).

    Ordering matches the backtester: stop before target (worst case when one
    candle spans both), and a gap beyond a level fills at the open.
    """
    o, h, low = float(candle["open"]), float(candle["high"]), float(candle["low"])
    sl, tgt = position.stop_loss_price, position.target_price
    if position.position_type == "long":
        if o <= sl:
            return o, "stop_loss"
        if low <= sl:
            return sl, "stop_loss"
        if o >= tgt:
            return o, "target"
        if h >= tgt:
            return tgt, "target"
    else:
        if o >= sl:
            return o, "stop_loss"
        if h >= sl:
            return sl, "stop_loss"
        if o <= tgt:
            return o, "target"
        if low <= tgt:
            return tgt, "target"
    return None


def _stop_target_levels(
    strategy: Strategy, closed: pd.DataFrame, entry_price: float,
) -> tuple[float, float]:
    """(stop_loss_price, target_price) for a brand-new entry.

    `closed` is the SAME closed-candle frame `signals.entry_signal` just
    evaluated, so its last row is the entry SIGNAL candle — the one whose
    close fired the entry, one candle before the fill this engine is about
    to record. Delegating to risk_levels keeps this identical to the batch
    backtester's ATR-at-the-signal-candle rule instead of quietly re-deriving
    it here — if the two diverged, a strategy could show one backtest and
    behave differently once deployed.
    """
    return stop_and_target(
        strategy,
        entry_price,
        signal_idx=len(closed) - 1,
        atr_series=build_atr_series(closed, strategy),
    )


def _build_trade(
    position: OpenPosition,
    *,
    exit_signal_ts: datetime,
    exit_fill_ts: datetime,
    intended_exit: float,
    exit_price: float,
    exit_reason: str,
    cost_per_trade_inr: float,
) -> ClosedTrade:
    if position.position_type == "long":
        gross = (exit_price - position.entry_price) * position.quantity
    else:
        gross = (position.entry_price - exit_price) * position.quantity
    return ClosedTrade(
        strategy_name=position.strategy_name,
        instrument=position.instrument,
        position_type=position.position_type,
        quantity=position.quantity,
        entry_signal_candle_ts=position.entry_signal_candle_ts,
        entry_fill_ts=position.entry_fill_ts,
        intended_entry_price=position.intended_entry_price,
        entry_price=position.entry_price,
        exit_signal_candle_ts=exit_signal_ts,
        exit_fill_ts=exit_fill_ts,
        intended_exit_price=intended_exit,
        exit_price=exit_price,
        exit_reason=exit_reason,
        gross_pnl=round(gross, 4),
        costs=cost_per_trade_inr,
        net_pnl=round(gross - cost_per_trade_inr, 4),
    )


# ---------------------------------------------------------------------------
# Core run logic (I/O only through the injected store/client — testable)
# ---------------------------------------------------------------------------


def _ist_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    """[start, end) of the current IST calendar day, expressed in UTC."""
    ist_day = now_utc.astimezone(IST).date()
    start = datetime.combine(ist_day, time(0, 0), tzinfo=IST)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def _process_combo(
    *,
    now_utc: datetime,
    settings: Settings,
    store: SupabaseStore,
    client: MarketDataClient,
    strategy: Strategy,
    instrument: str,
    instrument_token: int,
    position: OpenPosition | None,
    summary: RunSummary,
) -> None:
    """Evaluate one strategy x instrument on its just-closed candle."""
    tf = strategy.timeframe
    combo = f"{strategy.name}/{instrument}"

    # Fetch enough history for the indicators PLUS the forming candle: its
    # open is the fill price for any signal on the just-closed candle.
    from_utc = lookback_start_utc(tf, signals.min_candles_required(strategy), now_utc)
    df = client.fetch_historical_candles(
        instrument_token, tf, from_utc, now_utc, closed_only=False, now_utc=now_utc
    )
    closed = drop_forming_candle(df, tf, now_utc)
    if closed.empty:
        summary.skipped.append(f"{combo}: no closed candles returned")
        return

    last_ts = closed.index[-1]
    forming = df[df.index > last_ts]
    next_open: float | None = float(forming["open"].iloc[0]) if len(forming) else None
    next_open_ts: datetime | None = forming.index[0].to_pydatetime() if len(forming) else None

    summary.checked += 1
    if summary.candle_ts is None or last_ts.to_pydatetime() > summary.candle_ts:
        summary.candle_ts = last_ts.to_pydatetime()

    # ---- Open position: stop/target first, then rule exits. -------------
    if position is not None:
        # A position entered at candle T's open can only be exited using
        # candles from T onward. If the newest CLOSED candle still predates
        # the entry fill (a re-run inside the same window), there is nothing
        # legitimate to evaluate — judging the position by pre-entry price
        # action would be a look-behind bug.
        if last_ts.to_pydatetime() < position.entry_fill_ts:
            summary.skipped.append(
                f"{combo}: position entered on the still-forming candle; "
                "awaiting its first close"
            )
            return
        hit = _check_stop_target(position, closed.iloc[-1])
        if hit is not None:
            intended, reason = hit
            trade = _build_trade(
                position,
                exit_signal_ts=last_ts.to_pydatetime(),
                exit_fill_ts=last_ts.to_pydatetime(),  # hit inside that candle
                intended_exit=intended,
                exit_price=_exit_fill_price(intended, position.position_type, settings.slippage_pct),
                exit_reason=reason,
                cost_per_trade_inr=settings.cost_per_trade_inr,
            )
            if store.close_position(position, trade):
                summary.exits += 1
            else:
                summary.skipped.append(f"{combo}: exit already recorded (re-run)")
            return

        if signals.exit_signal(closed, strategy):
            if next_open is None:
                summary.skipped.append(
                    f"{combo}: exit signal on the day's final candle — no next "
                    "open to fill at; will re-evaluate next trading day"
                )
                return
            trade = _build_trade(
                position,
                exit_signal_ts=last_ts.to_pydatetime(),
                exit_fill_ts=next_open_ts,
                intended_exit=next_open,
                exit_price=_exit_fill_price(next_open, position.position_type, settings.slippage_pct),
                exit_reason="signal",
                cost_per_trade_inr=settings.cost_per_trade_inr,
            )
            if store.close_position(position, trade):
                summary.exits += 1
            else:
                summary.skipped.append(f"{combo}: exit already recorded (re-run)")
        return  # had a position this run -> no same-run re-entry (see module docstring)

    # ---- Flat: entry rules. ----------------------------------------------
    if not signals.entry_signal(closed, strategy):
        return

    # A trailing stop needs a running "best price seen since entry" that
    # survives to the NEXT run — but this engine has none of its own memory;
    # every tick rebuilds its whole world from Supabase (see module
    # docstring). `OpenPosition` (db.py) mirrors the `positions` table, and
    # that table carries only `stop_loss_price` / `target_price` — there is
    # no column to persist a running best price or an active trail level
    # between runs. Backtest.py CAN track it because it holds the entire
    # candle history in memory for one uninterrupted simulate() call; this
    # engine cannot reproduce that across independent, stateless ticks
    # without a schema change (e.g. `positions.best_price_since_entry` plus
    # wiring this engine to update it after each candle's checks, the same
    # "update after checks, apply next candle" rule backtest.py uses).
    #
    # Silently opening the position anyway and just ignoring trailing_stop
    # would be exactly the backtest/live divergence risk_levels.py exists to
    # prevent: the same strategy could show a trailing exit in its backtest
    # and never trail at all once deployed here. So this refuses loudly
    # instead — every tick, until the schema gap above is closed — rather
    # than either faking the behaviour or pretending the config doesn't
    # exist.
    if strategy.risk.trailing_stop is not None:
        summary.skipped.append(
            f"{combo}: entry signal ignored — trailing_stop is configured but "
            "the paper engine has no schema support for it yet (positions has "
            "no column to persist a running best price across stateless runs; "
            "see the comment above this check in paper_engine.py). Remove "
            "trailing_stop from this strategy before paper trading it, or add "
            "the schema support first."
        )
        return

    if next_open is None:
        summary.skipped.append(
            f"{combo}: entry signal on the day's final candle — no next open "
            "to fill at; skipped (no overnight pending orders in v1)"
        )
        return

    day_start, day_end = _ist_day_bounds_utc(now_utc)
    done_today = store.completed_cycles_between(strategy.name, instrument, day_start, day_end)
    if done_today >= strategy.max_cycles_per_day:
        summary.skipped.append(
            f"{combo}: entry signal ignored — max_cycles_per_day "
            f"({strategy.max_cycles_per_day}) already used"
        )
        return

    entry_price = _entry_fill_price(next_open, strategy.position_type, settings.slippage_pct)

    # Quantity is derived from THIS trade's own entry fill price — see
    # resolve_quantity's docstring. Zero is a real, expected outcome (a
    # dear share against a modest notional) and must never open a
    # zero-quantity position: that would sit in `positions` looking like a
    # live trade the strategy actually took, when nothing was ever bought.
    qty = resolve_quantity(strategy.sizing, entry_price)
    if qty < 1:
        summary.skipped.append(
            f"{combo}: entry signal ignored — notional_per_trade "
            f"₹{strategy.sizing.notional_per_trade:,.0f} buys 0 shares at the "
            f"entry fill price ₹{entry_price:,.2f}. Raise notional_per_trade "
            f"to at least the share price to trade this symbol."
        )
        return

    sl_price, tgt_price = _stop_target_levels(strategy, closed, entry_price)

    created = store.open_position(
        strategy_name=strategy.name,
        instrument=instrument,
        position_type=strategy.position_type,
        quantity=qty,
        entry_signal_candle_ts=last_ts.to_pydatetime(),
        entry_fill_ts=next_open_ts,
        intended_entry_price=next_open,
        entry_price=round(entry_price, 4),
        stop_loss_price=round(sl_price, 4),
        target_price=round(tgt_price, 4),
    )
    if created is not None:
        summary.entries += 1
    else:
        summary.skipped.append(f"{combo}: entry already recorded (re-run)")


def run_once(
    *,
    now_utc: datetime,
    settings: Settings,
    store: SupabaseStore,
    client: MarketDataClient,
    strategies: Sequence[Strategy],
) -> RunSummary:
    """One full engine pass over every enabled strategy x instrument.

    A failure on one combination (bad symbol, transient fetch error after
    retries) is recorded in the summary and does NOT abort the others.
    """
    summary = RunSummary()
    today_ist = now_utc.astimezone(IST).date()

    all_instruments = sorted({i for s in strategies for i in s.instruments})
    tokens = client.resolve_instrument_tokens(all_instruments, today_ist)

    open_positions = {
        (p.strategy_name, p.instrument): p for p in store.list_open_positions()
    }

    for strategy in strategies:
        for instrument in strategy.instruments:
            try:
                _process_combo(
                    now_utc=now_utc,
                    settings=settings,
                    store=store,
                    client=client,
                    strategy=strategy,
                    instrument=instrument,
                    instrument_token=tokens[instrument],
                    position=open_positions.get((strategy.name, instrument)),
                    summary=summary,
                )
            except KiteClientError as exc:
                # TokenExpiredError is a subclass but must bubble up: nothing
                # else in this run can work without a session either.
                if isinstance(exc, TokenExpiredError):
                    raise
                summary.skipped.append(f"{strategy.name}/{instrument}: {exc}")
            except RiskLevelError as exc:
                # One strategy's ATR period outrunning its available history
                # must not take down every other combination in the run — this
                # loop's whole contract is that a failure on one is recorded,
                # not fatal. Thin early-session data or a strategy edited to a
                # longer period would otherwise abort healthy strategies too.
                summary.skipped.append(f"{strategy.name}/{instrument}: {exc}")
    return summary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    started = datetime.now(tz=UTC)
    now_ist = started.astimezone(IST)

    # --- Gate first: outside market hours this must no-op cleanly (exit 0)
    # even if half the configuration is missing.
    should_run, reason = session_gate(now_ist, load_holidays())
    if not should_run:
        print(f"SKIPPED: {reason}")
        try:  # best effort — a skip must still exit 0 if Supabase is down
            SupabaseStore.from_env().write_run_audit(
                run_type="paper", status="skipped",
                run_started_at=started, run_finished_at=datetime.now(tz=UTC),
                reason=reason,
            )
        except Exception as exc:
            print(f"note: could not write skip audit row: {exc}", file=sys.stderr)
        return 0

    try:
        settings = get_settings()
        store = SupabaseStore.connect(settings)

        # The DATABASE is the source of truth for strategies, so enabling or
        # editing one in the dashboard takes effect on the next run with no
        # code push. strategies.yaml seeds it on the very first run only and
        # is never allowed to clobber later UI edits.
        seeded = store.seed_strategies_if_empty(load_strategy_documents())
        if seeded:
            print(f"seeded {seeded} strategy definition(s) from strategies.yaml")
        all_strategies = store.list_strategies()
        enabled = [s for s in all_strategies if s.enabled]

        if not enabled:
            store.write_run_audit(
                run_type="paper", status="skipped",
                run_started_at=started, run_finished_at=datetime.now(tz=UTC),
                reason="no enabled strategies in strategies.yaml",
            )
            print("SKIPPED: no enabled strategies")
            return 0

        # Provider is chosen by DATA_PROVIDER (free yfinance by default).
        # On the free path this needs no keys and no morning login.
        client = create_data_client(settings, store, now_ist.date())
        print(f"data provider: {describe_provider(settings)}")
        summary = run_once(
            now_utc=started, settings=settings, store=store,
            client=client, strategies=enabled,
        )

        store.write_run_audit(
            run_type="paper", status="ok",
            run_started_at=started, run_finished_at=datetime.now(tz=UTC),
            candle_ts=summary.candle_ts,
            reason=None,
            details={
                "provider": settings.data_provider,
                "checked": summary.checked,
                "entries": summary.entries,
                "exits": summary.exits,
                "skipped": summary.skipped[:50],  # cap: audit rows stay small
            },
        )
        print(
            f"OK: checked {summary.checked} combos — "
            f"{summary.entries} entries, {summary.exits} exits, "
            f"{len(summary.skipped)} notes"
        )
        for note in summary.skipped:
            print(f"  note: {note}")
        return 0

    except Exception as exc:
        # Any failure (expired token, Supabase outage, bug) must be visible:
        # audit if possible, print, exit nonzero so the Actions run shows red.
        print(f"ERROR: {exc}", file=sys.stderr)
        try:
            SupabaseStore.from_env().write_run_audit(
                run_type="paper", status="error",
                run_started_at=started, run_finished_at=datetime.now(tz=UTC),
                reason=str(exc)[:500],
            )
        except Exception:
            pass  # auditing a failure must never mask the failure
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
