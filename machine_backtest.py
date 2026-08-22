"""Backtesting a v3 state machine, with the same fill realism as v2.

WHAT CHANGES AND WHAT MUST NOT
------------------------------
Only the SIGNAL SOURCE changes. v2 asks a boolean Series "is this a bar to
enter on"; v3 asks a state machine. Everything downstream — next-open fills,
adverse slippage, worst-case stop-before-target ordering, square-off, the
itemised cost model, skipped-entry accounting — is deliberately identical,
because those are what make a result comparable to the ones already in the
database. A v3 run that priced fills differently could not be ranked against a
v2 run, and the whole point of the research loop is that they can.

THE LOCKSTEP PROBLEM
--------------------
v2 can precompute its signals because a boolean per candle depends on nothing
the simulator does. A machine does: if a stop takes the position out at 11:15,
the machine must know that at 11:15, or it sits in `in_position` waiting for an
exit rule to fire on a position that no longer exists. It would then ignore
every subsequent setup and report a strategy that simply stopped trading —
plausible, clean, and wrong.

So the machine is stepped INSIDE the bar loop, after that bar's position
management, and is told `position_closed()` whenever the simulator closes a
trade by any means other than the machine's own `exit:`.

ONE HONEST DISCREPANCY
----------------------
A signal at bar i fills at bar i+1's open, so between the machine's `exit:`
firing and the fill landing, the machine has already moved on while the
position is still open. `position.is_open` reports the truth (still open)
during that bar. This is the same one-bar lag v2 has; it is a property of
next-open fills, not of the machine.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from backtest import _make_trade
from backtest_types import SimResult, SimTrade, SkippedEntry
from config import IST
from risk_levels import level_from_spec
from state_runner import MachineStepper, PositionView
from strategy.parse import resolve_quantity
from strategy.v3 import StateMachine


def _atr_series_for(df: pd.DataFrame, machine: StateMachine) -> dict:
    """ATR series the risk block needs, precomputed once.

    Only the risk block is consulted: a machine's own `stop:` expression is
    evaluated by the expression engine, which computes its own indicators.
    """
    from risk_levels import build_atr_series

    class _RiskOnly:
        risk = machine.risk

    return build_atr_series(df, _RiskOnly())


def simulate_machine(
    df: pd.DataFrame,
    machine: StateMachine,
    *,
    slippage_pct: float,
    cost_per_trade_inr: float,
    cost_model: Any = None,
) -> SimResult:
    """Replay one instrument's history under a v3 state machine."""
    if df.empty or len(df) < 2:
        return SimResult(trades=[], skipped=[])

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    index = df.index

    slip = slippage_pct / 100.0
    session = machine.session
    ist_times = [ts.astimezone(IST).time() for ts in index]
    atr_series = _atr_series_for(df, machine)

    stepper = MachineStepper(machine, df)

    trades: list[SimTrade] = []
    skipped: list[SkippedEntry] = []

    in_pos = False
    is_long = True
    entry_bar = 0
    e_signal_ts = e_fill_ts = None
    e_intended = e_price = sl_price = tgt_price = 0.0
    qty = 0
    entries_by_day: dict = {}

    # Queued from a bar's close, acted on at the next bar's open.
    pending_entry: Any = None      # the EntryEvent that asked for it
    pending_exit: Any = None       # the ExitEvent that asked for it

    def fill_buy(price: float) -> float:
        return price * (1 + slip)

    def fill_sell(price: float) -> float:
        return price * (1 - slip)

    def close_position(
        i: int, intended: float, reason: str, *,
        fraction: float = 1.0, from_signal: bool = False,
    ) -> None:
        """Close all of the position, or a slice of it.

        A partial keeps the position open with the remainder, so `qty` is the
        RUNNING quantity from here on — the stop and target below protect
        what is left rather than what was originally bought.

        Each slice is its own SimTrade against the same entry price. Both
        halves really were bought at the same moment, so scoring the runner
        against anything else would be fiction; and each sale really is a
        separate order, so it pays its own costs.
        """
        nonlocal in_pos, pending_exit, qty

        if fraction >= 1.0:
            slice_qty = qty
        else:
            slice_qty = int(qty * fraction)
            if slice_qty < 1:
                # One share cannot be halved. Doing nothing quietly would
                # leave the strategy believing it had reduced risk.
                skipped.append(SkippedEntry(
                    signal_ts=index[i], price=float(intended),
                    reason="partial_below_one_share",
                ))
                pending_exit = None
                return

        exit_price = fill_sell(intended) if is_long else fill_buy(intended)
        trades.append(_make_trade(
            entry_signal_ts=e_signal_ts, entry_fill_ts=e_fill_ts,
            exit_signal_ts=index[i - 1] if from_signal else index[i],
            exit_fill_ts=index[i],
            position_type="long" if is_long else "short", quantity=slice_qty,
            intended_entry=e_intended, entry_price=e_price,
            intended_exit=intended, exit_price=exit_price,
            exit_reason=reason, cost_per_trade_inr=cost_per_trade_inr,
            cost_model=cost_model,
        ))
        qty -= slice_qty
        if qty < 1:
            in_pos = False
        pending_exit = None

    for i in range(len(df)):
        # ---- At this candle's OPEN: act on what the previous close queued.
        if in_pos and pending_exit is not None:
            close_position(
                i, opens[i],
                pending_exit.reason if pending_exit.reason != "rule" else "signal",
                fraction=pending_exit.fraction, from_signal=True,
            )
        elif not in_pos and pending_entry is not None:
            event = pending_entry
            fill_day = index[i].astimezone(IST).date()
            allowed = (
                entries_by_day.get(fill_day, 0) < machine.max_cycles_per_day
                and session.allows_entry_at(ist_times[i])
            )
            if not allowed:
                # The machine believes it is in a position. It is not, and
                # nothing downstream would ever tell it — so say so, or it
                # waits forever for an exit that cannot come.
                stepper.position_closed()
            else:
                is_long = event.side == "long"
                e_signal_ts = index[event.bar]
                e_fill_ts = index[i]
                e_intended = opens[i]
                e_price = fill_buy(opens[i]) if is_long else fill_sell(opens[i])
                qty = resolve_quantity(machine.sizing, e_price)
                if qty < 1:
                    skipped.append(SkippedEntry(
                        signal_ts=index[event.bar], price=float(opens[i]),
                        reason="notional_below_price",
                    ))
                    stepper.position_closed()
                else:
                    # A level the machine named wins over the risk block: it
                    # is the whole reason for `stop: confirm_high`. The risk
                    # block remains the fallback so every machine has a stop
                    # whether or not it thought to set one.
                    sl_price = (
                        event.stop if event.stop is not None
                        else level_from_spec(
                            machine.risk.stop_loss, e_price, event.bar,
                            atr_series, favourable=False, is_long=is_long,
                        )
                    )
                    tgt_price = (
                        event.target if event.target is not None
                        else level_from_spec(
                            machine.risk.target, e_price, event.bar,
                            atr_series, favourable=True, is_long=is_long,
                        )
                    )
                    in_pos = True
                    entry_bar = i
                    entries_by_day[fill_day] = entries_by_day.get(fill_day, 0) + 1
            pending_entry = None

        # ---- During the candle: stop before target, gaps fill at the open.
        # Identical ordering to v2 — see simulate_with_skips for the reasoning.
        closed_by_risk = False
        if in_pos:
            if is_long:
                if opens[i] <= sl_price:
                    close_position(i, opens[i], "stop_loss"); closed_by_risk = True
                elif lows[i] <= sl_price:
                    close_position(i, sl_price, "stop_loss"); closed_by_risk = True
                elif opens[i] >= tgt_price:
                    close_position(i, opens[i], "target"); closed_by_risk = True
                elif highs[i] >= tgt_price:
                    close_position(i, tgt_price, "target"); closed_by_risk = True
            else:
                if opens[i] >= sl_price:
                    close_position(i, opens[i], "stop_loss"); closed_by_risk = True
                elif highs[i] >= sl_price:
                    close_position(i, sl_price, "stop_loss"); closed_by_risk = True
                elif opens[i] <= tgt_price:
                    close_position(i, opens[i], "target"); closed_by_risk = True
                elif lows[i] <= tgt_price:
                    close_position(i, tgt_price, "target"); closed_by_risk = True

        if in_pos and session.square_off and ist_times[i] >= session.square_off:
            close_position(i, opens[i], "square_off")
            closed_by_risk = True

        if closed_by_risk:
            stepper.position_closed()

        # ---- At this candle's CLOSE: step the machine, then queue.
        view = PositionView(
            is_open=in_pos, is_long=is_long,
            entry_price=e_price if in_pos else float("nan"),
            bars_held=(i - entry_bar) if in_pos else 0,
            last_price=float(closes[i]),
        )
        result = stepper.step(i, view)

        if result.exit is not None and in_pos and pending_exit is None:
            pending_exit = result.exit
        if result.entry is not None and not in_pos and pending_entry is None:
            pending_entry = result.entry

    # ---- History exhausted with a position open: close it so every trade is
    # a complete round-trip, flagged so it is visibly synthetic.
    if in_pos:
        last = len(df) - 1
        close_position(last, closes[last], "end_of_data")

    return SimResult(trades=trades, skipped=skipped)
