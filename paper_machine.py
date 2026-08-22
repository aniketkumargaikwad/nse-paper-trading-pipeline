"""Paper trading a v3 state machine.

WHY THIS IS SEPARATE FROM paper_engine._process_combo
-----------------------------------------------------
A v2 strategy is a pure function of the candles: ask "entry?" and "exit?" and
the answers depend on nothing but price. A machine carries state, and this
engine is stateless by design — it wakes every 15 minutes, rebuilds its whole
world from Supabase, acts, and exits.

So a machine has to be RESUMED from `machine_state`, stepped exactly once on
the newest closed candle, and written back. That is a different shape of work
from the v2 path, and interleaving the two in one function would make both
harder to read than either deserves.

THE IDEMPOTENCY RULE
--------------------
Stepping is the one irreversible thing here. A retried tick that stepped the
machine again on the same candle would advance it through a state it never
really saw, and a `timeout:` would count that candle twice. So the candle a
machine last saw is stored, and a repeat run is a no-op.

WHAT THIS DELIBERATELY REFUSES
------------------------------
Partial exits. `positions` has no way to hold a reduced quantity, so a partial
would either book the whole position or leave a row claiming a size that is no
longer held. Refusing loudly beats a live/backtest divergence that only shows
up in the P&L months later — the same call paper_engine already makes for
trailing stops.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from config import IST, Settings
from db import OpenPosition, SupabaseStore
from kite_client import MarketDataClient, drop_forming_candle
from risk_levels import build_atr_series, stop_and_target
from strategy.expr import Call, Literal, Offset, walk
from strategy.parse import resolve_quantity
from strategy.v3 import StateMachine

# A little more history than the strictly-needed minimum, so an indicator is
# warm rather than barely defined on the first bar it could fire.
WARMUP_MARGIN = 5


def min_candles_required(machine: StateMachine) -> int:
    """How much history a machine needs before it can be evaluated.

    Every expression it contains is walked for indicator periods and offsets;
    the largest wins. A swing lookback of n reads n bars either side of a
    pivot, so it needs roughly twice its argument — counting every call that
    way is deliberately generous, because fetching a few extra candles is
    cheap and evaluating on too little history is silently wrong.
    """
    needed = 1
    for state in machine.states:
        for transition in state.transitions:
            trees = [transition.when, *(expr for _, expr in transition.sets)]
            if transition.enter is not None:
                trees += [
                    e for e in (transition.enter.stop, transition.enter.target)
                    if e is not None
                ]
            for tree in trees:
                for node in walk(tree):
                    if isinstance(node, Offset):
                        needed = max(needed, node.bars + 1)
                    elif isinstance(node, Call):
                        periods = [
                            int(a.value) for a in node.args
                            if isinstance(a, Literal) and a.value == int(a.value)
                        ]
                        if periods:
                            needed = max(needed, max(periods) * 2 + 2)
    return needed + WARMUP_MARGIN


def process_machine_combo(
    *,
    now_utc: datetime,
    settings: Settings,
    store: SupabaseStore,
    client: MarketDataClient,
    machine: StateMachine,
    instrument: str,
    instrument_token: int,
    position: OpenPosition | None,
    summary: Any,
) -> None:
    """Evaluate one machine x instrument on its just-closed candle."""
    # Imported here rather than at module scope: paper_engine imports this
    # module lazily, and importing it back at import time would close the loop.
    from paper_engine import (
        _build_trade,
        _check_stop_target,
        _entry_fill_price,
        _exit_fill_price,
        _ist_day_bounds_utc,
        lookback_start_utc,
    )
    from state_runner import MachineStepper, PositionView

    tf = machine.timeframe
    combo = f"{machine.name}/{instrument}"

    from_utc = lookback_start_utc(tf, min_candles_required(machine), now_utc)
    df = client.fetch_historical_candles(
        instrument_token, tf, from_utc, now_utc, closed_only=False, now_utc=now_utc
    )
    closed = drop_forming_candle(df, tf, now_utc)
    if closed.empty:
        summary.skipped.append(f"{combo}: no closed candles returned")
        return

    last_ts = closed.index[-1]
    forming = df[df.index > last_ts]
    next_open = float(forming["open"].iloc[0]) if len(forming) else None
    next_open_ts = forming.index[0].to_pydatetime() if len(forming) else None

    summary.checked += 1
    if summary.candle_ts is None or last_ts.to_pydatetime() > summary.candle_ts:
        summary.candle_ts = last_ts.to_pydatetime()

    saved = store.get_machine_state(machine.name, instrument)
    if saved and saved.get("last_candle_ts") is not None:
        if saved["last_candle_ts"] >= last_ts.to_pydatetime():
            summary.skipped.append(f"{combo}: already stepped on this candle (re-run)")
            return

    def persist(stepper) -> None:
        """Write the machine back, or forget it if it is at its start.

        A row saying "initial state, no variables" is indistinguishable from
        never having run, while making the table grow by one row per symbol
        per strategy forever.
        """
        # `bars_in_state` only changes behaviour when the state has a
        # `timeout:` counting toward something. A machine idling in its
        # initial state with no timeout and nothing captured is in exactly
        # the condition it started in, however many candles it has watched —
        # so requiring the counter to be zero would keep a row for every
        # symbol of every universe strategy, forever, saying nothing.
        initial_state = machine.state(machine.initial)
        at_start = (
            stepper.state == machine.initial
            and not stepper.variables
            and (initial_state.timeout is None or stepper.bars_in_state == 0)
        )
        if at_start and position is None:
            store.clear_machine_state(machine.name, instrument)
        else:
            store.save_machine_state(
                machine.name, instrument,
                state=stepper.state, variables=stepper.variables,
                bars_in_state=stepper.bars_in_state,
                last_candle_ts=last_ts.to_pydatetime(),
            )

    # ---- Open position: stop, target, then square-off — the same worst-case
    # ordering the backtester uses, so a strategy cannot pass a backtest under
    # one interpretation and trade under another.
    if position is not None:
        if last_ts.to_pydatetime() < position.entry_fill_ts:
            summary.skipped.append(
                f"{combo}: position entered on the still-forming candle; "
                "awaiting its first close"
            )
            return
        hit = _check_stop_target(position, closed.iloc[-1])
        if hit is None and machine.session.square_off is not None:
            if last_ts.astimezone(IST).time() >= machine.session.square_off:
                hit = (float(closed["open"].iloc[-1]), "square_off")
        if hit is not None:
            intended, reason = hit
            trade = _build_trade(
                position,
                exit_signal_ts=last_ts.to_pydatetime(),
                exit_fill_ts=last_ts.to_pydatetime(),
                intended_exit=intended,
                exit_price=_exit_fill_price(
                    intended, position.position_type, settings.slippage_pct
                ),
                exit_reason=reason,
                cost_per_trade_inr=settings.cost_per_trade_inr,
            )
            if store.close_position(position, trade):
                summary.exits += 1
                # The position is gone, so the machine must be told — exactly
                # as the backtester does. Left unsaid it would wait in
                # `in_position` for an exit that can never come.
                store.save_machine_state(
                    machine.name, instrument,
                    state=machine.on_position_closed or machine.initial,
                    variables=(saved or {}).get("variables", {}),
                    bars_in_state=0,
                    last_candle_ts=last_ts.to_pydatetime(),
                )
            else:
                summary.skipped.append(f"{combo}: exit already recorded (re-run)")
            return

    # ---- Resume, then step ONCE on the newest closed candle.
    stepper = MachineStepper(machine, closed)
    if saved:
        stepper.restore(
            state=saved["state"],
            variables=saved["variables"],
            bars_in_state=saved["bars_in_state"],
        )

    view = PositionView(
        is_open=position is not None,
        is_long=(position.position_type == "long") if position else False,
        entry_price=position.entry_price if position else float("nan"),
        bars_held=0,
        last_price=float(closed["close"].iloc[-1]),
    )
    result = stepper.step(len(closed) - 1, view)

    # ---- Act on what it decided.
    if position is not None:
        if result.exit is not None:
            _handle_machine_exit(
                result, position, machine, instrument, combo, closed, last_ts,
                next_open, next_open_ts, settings, store, summary,
            )
        persist(stepper)
        return

    if result.entry is None:
        persist(stepper)
        return

    _handle_machine_entry(
        result, machine, instrument, combo, closed, last_ts,
        next_open, next_open_ts, now_utc, settings, store, summary,
    )
    persist(stepper)


def _handle_machine_exit(
    result, position, machine, instrument, combo, closed, last_ts,
    next_open, next_open_ts, settings, store, summary,
) -> None:
    from paper_engine import _build_trade, _exit_fill_price

    if next_open is None:
        summary.skipped.append(
            f"{combo}: exit signal on the day's final candle — no next open "
            "to fill at; will re-evaluate next trading day"
        )
        return
    if result.exit.fraction < 1.0:
        summary.skipped.append(
            f"{combo}: partial exit (fraction {result.exit.fraction:.2f}) is "
            "not supported by the paper engine — `positions` cannot hold a "
            "reduced quantity. Backtest it, or use a full exit to trade it."
        )
        return

    trade = _build_trade(
        position,
        exit_signal_ts=last_ts.to_pydatetime(),
        exit_fill_ts=next_open_ts,
        intended_exit=next_open,
        exit_price=_exit_fill_price(
            next_open, position.position_type, settings.slippage_pct
        ),
        exit_reason=result.exit.reason or "signal",
        cost_per_trade_inr=settings.cost_per_trade_inr,
    )
    if store.close_position(position, trade):
        summary.exits += 1
    else:
        summary.skipped.append(f"{combo}: exit already recorded (re-run)")


def _handle_machine_entry(
    result, machine, instrument, combo, closed, last_ts,
    next_open, next_open_ts, now_utc, settings, store, summary,
) -> None:
    from paper_engine import _entry_fill_price, _ist_day_bounds_utc

    if machine.risk.trailing_stop is not None:
        summary.skipped.append(
            f"{combo}: entry ignored — trailing_stop has no schema support in "
            "the paper engine (positions cannot persist a running best price "
            "across stateless runs)."
        )
        return

    if next_open is None:
        summary.skipped.append(
            f"{combo}: entry signal on the day's final candle — no next open "
            "to fill at; skipped"
        )
        return

    day_start, day_end = _ist_day_bounds_utc(now_utc)
    if store.completed_cycles_between(
        machine.name, instrument, day_start, day_end
    ) >= machine.max_cycles_per_day:
        summary.skipped.append(
            f"{combo}: entry ignored — max_cycles_per_day "
            f"({machine.max_cycles_per_day}) already used"
        )
        return

    fill_time = next_open_ts.astimezone(IST).time()
    if not machine.session.allows_entry_at(fill_time):
        summary.skipped.append(
            f"{combo}: entry ignored — a fill at {fill_time:%H:%M} IST falls "
            "outside this strategy's session rules"
        )
        return

    side = result.entry.side
    entry_price = _entry_fill_price(next_open, side, settings.slippage_pct)
    qty = resolve_quantity(machine.sizing, entry_price)
    if qty < 1:
        summary.skipped.append(
            f"{combo}: entry ignored — sizing buys 0 shares at the entry fill "
            f"price {entry_price:,.2f}."
        )
        return

    # A level the machine named beats the risk block, exactly as in the
    # backtester; the risk block remains the fallback so every entry has a
    # stop whether or not the machine thought to set one.
    sl_price, tgt_price = stop_and_target(
        machine, entry_price, signal_idx=len(closed) - 1,
        atr_series=build_atr_series(closed, machine),
    )
    if result.entry.stop is not None:
        sl_price = float(result.entry.stop)
    if result.entry.target is not None:
        tgt_price = float(result.entry.target)

    created = store.open_position(
        strategy_name=machine.name,
        instrument=instrument,
        position_type=side,
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
