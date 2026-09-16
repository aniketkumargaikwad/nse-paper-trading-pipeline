"""Stop and target price levels, shared by the backtester and paper engine.

This module exists for one reason: the batch backtester and the paper engine
must compute a stop or target level IDENTICALLY. If they diverged, a strategy
could show one result in a backtest and behave differently when deployed —
the single most misleading failure this platform could have, because the
backtest is the only evidence used to decide whether to deploy at all.

So both rules live here, in one place, rather than in each engine:

* ATR is read at the SIGNAL candle — the last closed candle before the fill.
  Both engines fill at the next candle's open, so reading ATR at the fill
  candle would let a level depend on data the fill could not have seen.

* A frame too short for the ATR period is a HARD ERROR, never a silently-NaN
  level. A strategy that produced no trades because its indicator never
  warmed up looks exactly like one whose edge does not exist, and that
  ambiguity is what this refuses to allow.

* A signal that fires while the ATR is still warming up - RSI(2) is ready on
  bar 3, ATR(14) on bar 15 - is SKIPPED and recorded, not fatal. It used to
  be fatal, and that threw away eight years of a combination for the sake of
  its first two weeks: measured on 16 September 2026, an RSI(2) dip rule
  lost 8 of 19 combinations to it. `atr_ready` is how a simulator asks.

Pure module: no network, no database, no clock.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

import indicators
from strategy_schema import StopSpec, Strategy


class RiskLevelError(RuntimeError):
    """A strategy's stop or target cannot be computed as configured.

    Reserved for problems the caller must fix before any result means
    anything — e.g. an ATR period longer than the available history.
    """


def atr_periods_for(strategy: Strategy) -> set[int]:
    """Every distinct ATR period this strategy's risk config needs.

    `trailing_stop` is included even where an engine does not yet act on it,
    so its history requirement is never silently ignored.
    """
    return {
        spec.period
        for spec in (
            strategy.risk.stop_loss,
            strategy.risk.target,
            strategy.risk.trailing_stop,
        )
        if spec is not None and spec.type == "atr"
    }


def build_atr_series(df: pd.DataFrame, strategy: Strategy) -> dict[int, np.ndarray]:
    """Precompute each ATR series the strategy needs, once.

    Computed up front rather than per candle, and a period exceeding the
    available history fails here — naming the strategy, the period, and both
    candle counts — rather than producing NaN levels downstream.
    """
    series: dict[int, np.ndarray] = {}
    for period in atr_periods_for(strategy):
        if len(df) <= period:
            raise RiskLevelError(
                f"strategy {strategy.name!r} uses an ATR({period}) stop but only "
                f"{len(df)} candles are available; at least {period + 1} are "
                "needed. Backfill more history, or use a shorter ATR period."
            )
        series[period] = indicators.atr(df, period).to_numpy()
    return series


def atr_ready(specs, signal_idx: int, atr_series: dict[int, np.ndarray]) -> bool:
    """Are every ATR-based level's inputs usable at `signal_idx`?

    `specs` are StopSpecs (None allowed). Percent levels are always ready.
    """
    for spec in specs:
        if spec is None or spec.type != "atr":
            continue
        value = float(atr_series[spec.period][signal_idx])
        if not value > 0 or value != value:      # zero or NaN
            return False
    return True


# The SkippedEntry reason for a signal that fired before its ATR warmed up.
WARMING_UP = "risk_warming_up"


def level_from_spec(
    spec: StopSpec,
    entry_price: float,
    signal_idx: int,
    atr_series: dict[int, np.ndarray],
    *,
    favourable: bool,
    is_long: bool,
) -> float:
    """Absolute price for a stop or target.

    `favourable` marks a target (moves in the position's favour); a stop moves
    against it. ATR is read at `signal_idx` — the last closed candle before the
    fill — so the level never depends on data the fill could not have seen.
    """
    if spec.type == "percent":
        distance = entry_price * spec.value / 100.0
    else:
        atr_value = float(atr_series[spec.period][signal_idx])
        # Reachable even when the length check above passed: Wilder's ATR is
        # NaN until `period` bars have accumulated, so an early signal in a
        # frame that is long enough overall still has no usable value.
        if not atr_value > 0 or atr_value != atr_value:  # zero or NaN
            raise RiskLevelError(
                f"ATR({spec.period}) is not available at the entry candle; "
                "the series is still warming up. Backfill more history."
            )
        distance = atr_value * spec.multiplier

    moves_up = favourable if is_long else not favourable
    return entry_price + distance if moves_up else entry_price - distance


def stop_and_target(
    strategy: Strategy,
    entry_price: float,
    signal_idx: int,
    atr_series: dict[int, np.ndarray],
) -> tuple[float, float]:
    """(stop_loss_price, target_price) for a new entry — the pair, together.

    Both engines need both levels from the same inputs, so computing them as a
    pair removes the chance of one caller getting the `favourable` flag right
    and the other getting it backwards.
    """
    is_long = strategy.position_type == "long"
    stop = level_from_spec(
        strategy.risk.stop_loss, entry_price, signal_idx, atr_series,
        favourable=False, is_long=is_long,
    )
    target = level_from_spec(
        strategy.risk.target, entry_price, signal_idx, atr_series,
        favourable=True, is_long=is_long,
    )
    return stop, target
