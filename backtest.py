"""Batch backtester: strategies x instruments over years of history.

Run manually (GitHub Actions `workflow_dispatch`, or locally):

    python backtest.py                    # all enabled strategies, 2 years
    python backtest.py --years 3
    python backtest.py --strategy TF-EMA-RSI-15m-v1
    python backtest.py --no-db            # CSV only, skip Supabase writes

Outputs one row per strategy x instrument combination:
  * to the `backtest_results` table in Supabase (grouped by a batch_id), and
  * to a CSV in ./backtest_results/ for download/spreadsheet work.

FILL REALISM (identical philosophy to the paper engine)
------------------------------------------------------
* Signals are evaluated on CLOSED candles; fills happen at the NEXT candle's
  open. There are no same-candle-close fills anywhere.
* Slippage (config, default 0.05%) is applied adversely to every fill:
  buys pay more, sells receive less.
* A flat per-trade cost (config, default ₹30 round-trip) is subtracted from
  every completed trade.
* Stop-loss/target are monitored intra-candle using high/low. If BOTH could
  have been hit inside one candle we assume the STOP hit first (worst case).
  If price gaps beyond a level, the fill is at the open, not at the level.

KILL RULES (robustness flags)
-----------------------------
A combination "passes" only if ALL hold:
  * at least MIN_TRADES trades (too few = statistical noise),
  * net P&L positive AFTER costs,
  * max drawdown within MAX_DRAWDOWN_PCT of the capital base,
  * the strategy is profitable on at least MIN_PROFITABLE_SYMBOLS of its
    instruments (edge on one symbol only is usually curve-fitting).
The flags (with required-vs-actual numbers) are stored per row so the
dashboard can show WHY something failed, not just that it failed.
"""

from __future__ import annotations

import argparse
import math
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import indicators
import signals
from backtest_types import SimResult, SimTrade, SkippedEntry
from metrics import (
    MAX_DRAWDOWN_PCT,
    MIN_PROFITABLE_SYMBOLS,
    MIN_TRADES,
    ComboMetrics,
    compute_metrics,
    evaluate_kill_rules,
)
from risk_levels import build_atr_series, level_from_spec
from config import IST, UTC, Settings, get_settings
from data_provider import create_data_client, describe_provider
from db import SupabaseStore
from kite_client import KiteClientError, TokenExpiredError
from strategy_schema import (
    Strategy,
    StopSpec,
    load_strategies,
    load_strategy_documents,
    resolve_quantity,
)

# --- Kill-rule thresholds ---------------------------------------------------

CSV_OUTPUT_DIR = Path("backtest_results")




# ---------------------------------------------------------------------------
# Pure simulation core (no I/O — fully unit-tested)
# ---------------------------------------------------------------------------




def _make_trade(
    *,
    entry_signal_ts, entry_fill_ts, exit_signal_ts, exit_fill_ts,
    position_type: str, quantity: int,
    intended_entry: float, entry_price: float,
    intended_exit: float, exit_price: float,
    exit_reason: str, cost_per_trade_inr: float,
) -> SimTrade:
    if position_type == "long":
        gross = (exit_price - entry_price) * quantity
    else:  # short: profit when price falls
        gross = (entry_price - exit_price) * quantity
    return SimTrade(
        entry_signal_ts=entry_signal_ts, entry_fill_ts=entry_fill_ts,
        exit_signal_ts=exit_signal_ts, exit_fill_ts=exit_fill_ts,
        position_type=position_type, quantity=quantity,
        intended_entry_price=intended_entry, entry_price=entry_price,
        intended_exit_price=intended_exit, exit_price=exit_price,
        exit_reason=exit_reason,
        gross_pnl=round(gross, 4),
        costs=cost_per_trade_inr,
        net_pnl=round(gross - cost_per_trade_inr, 4),
    )


def simulate_with_skips(
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    slippage_pct: float,
    cost_per_trade_inr: float,
) -> SimResult:
    """Replay one instrument's candle history under the fill-realism model.

    The DataFrame must be the canonical closed-candle frame (UTC index,
    ohlcv columns, sorted). Returns completed trades plus any entry signals
    that could not be sized into a trade (see SkippedEntry), both in time
    order.
    """
    if df.empty or len(df) < 2:
        return SimResult(trades=[], skipped=[])

    entry_sig = signals.entry_series(df, strategy)
    exit_sig = signals.exit_series(df, strategy)

    opens = df["open"].to_numpy()
    highs = df["high"].to_numpy()
    lows = df["low"].to_numpy()
    closes = df["close"].to_numpy()
    index = df.index

    slip = slippage_pct / 100.0
    is_long = strategy.position_type == "long"

    # Each candle's IST wall-clock time, computed once. Session rules are
    # expressed in IST because that is how a trader thinks about the NSE day,
    # while the frame itself is UTC.
    session = strategy.session
    ist_times = [ts.astimezone(IST).time() for ts in index]

    # Precompute every ATR series this strategy's risk config needs, ONCE,
    # rather than recomputing it per candle inside the loop below. A period
    # that exceeds the available history is a hard error here rather than a
    # silently-NaN stop: a strategy that produced no trades because its
    # indicator never warmed up looks identical to one whose edge does not
    # exist, and that ambiguity is exactly what this guard prevents.
    atr_series = build_atr_series(df, strategy)

    # Adverse slippage: buying pays more, selling receives less. For a long,
    # entry is a buy and exit a sell; for a short it is the reverse.
    def buy_fill(price: float) -> float:
        return price * (1 + slip)

    def sell_fill(price: float) -> float:
        return price * (1 - slip)

    entry_fill = buy_fill if is_long else sell_fill
    exit_fill = sell_fill if is_long else buy_fill

    trades: list[SimTrade] = []
    skipped: list[SkippedEntry] = []
    in_pos = False
    pending_entry_from: int | None = None  # index of the signal candle
    pending_exit_from: int | None = None
    entries_by_day: dict = {}  # IST date -> count, for max_cycles_per_day

    # Open-position state. Quantity is derived from THIS trade's own entry
    # fill price (floor(notional / price) differs on every symbol — that is
    # the whole point of notional sizing) and held here, not in a module- or
    # loop-level variable, so close_position always uses the right trade's
    # own quantity rather than whatever a later entry happened to compute.
    e_signal_ts = e_fill_ts = None
    e_intended = e_price = sl_price = tgt_price = 0.0
    qty = 0
    # Trailing-stop state. `best_price` is the best price seen since entry
    # (highest high for a long, lowest low for a short); `trail_price` is the
    # trailing level derived from it, or None until the first candle after
    # entry has closed. Both reset on every entry, exactly like `qty` above —
    # a value leaked from a previous trade would silently corrupt this one.
    best_price = 0.0
    trail_price: float | None = None

    def close_position(i: int, intended: float, reason: str) -> None:
        nonlocal in_pos, pending_exit_from
        trades.append(
            _make_trade(
                entry_signal_ts=e_signal_ts, entry_fill_ts=e_fill_ts,
                exit_signal_ts=index[i - 1] if reason == "signal" else index[i],
                exit_fill_ts=index[i],
                position_type=strategy.position_type, quantity=qty,
                intended_entry=e_intended, entry_price=e_price,
                intended_exit=intended, exit_price=exit_fill(intended),
                exit_reason=reason, cost_per_trade_inr=cost_per_trade_inr,
            )
        )
        in_pos = False
        pending_exit_from = None

    for i in range(len(df)):
        # ---- At this candle's OPEN: act on signals from the previous close.
        if in_pos and pending_exit_from is not None:
            close_position(i, opens[i], "signal")
        elif not in_pos and pending_entry_from is not None:
            fill_day = index[i].astimezone(IST).date()
            if (
                entries_by_day.get(fill_day, 0) < strategy.max_cycles_per_day
                and session.allows_entry_at(ist_times[i])
            ):
                e_signal_ts = index[pending_entry_from]
                e_fill_ts = index[i]
                e_intended = opens[i]
                e_price = entry_fill(opens[i])
                qty = resolve_quantity(strategy.sizing, e_price)
                if qty < 1:
                    # A quantity-0 trade would post a P&L of exactly 0 and
                    # land in the results as a flat trade that never
                    # happened — record the skip instead of the trade.
                    #
                    # Deliberately NOT `continue`: the block at the bottom of
                    # this loop queues a fresh entry from THIS candle's close,
                    # and it runs on the signal alone, not on in_pos. Skipping
                    # it would drop a signal that fired on the skip candle
                    # entirely — no trade AND no SkippedEntry — which is the
                    # very invisibility this skip record exists to prevent.
                    # The stop/target block below is a no-op while flat.
                    skipped.append(
                        SkippedEntry(
                            signal_ts=index[pending_entry_from],
                            price=float(opens[i]),
                            reason="notional_below_price",
                        )
                    )
                else:
                    sl_price = level_from_spec(
                        strategy.risk.stop_loss, e_price, pending_entry_from,
                        atr_series, favourable=False, is_long=is_long,
                    )
                    tgt_price = level_from_spec(
                        strategy.risk.target, e_price, pending_entry_from,
                        atr_series, favourable=True, is_long=is_long,
                    )
                    best_price = e_price
                    trail_price = None
                    in_pos = True
                    entries_by_day[fill_day] = entries_by_day.get(fill_day, 0) + 1
            pending_entry_from = None

        # ---- During the candle: stop-loss / target on high-low range.
        # Worst-case ordering: the stop is checked before the target, and a
        # gap beyond a level fills at the open, not at the level. A trailing
        # exit is a STOP, so it keeps that same precedence over the target —
        # the effective stop used below is just the tighter of the fixed
        # stop and the current trail (trail_price is None until a candle has
        # closed since entry, so the fixed stop alone applies until then).
        if in_pos:
            effective_stop, reason = sl_price, "stop_loss"
            if trail_price is not None:
                tighter = max(sl_price, trail_price) if is_long else min(sl_price, trail_price)
                if tighter != sl_price:
                    effective_stop, reason = tighter, "trailing_stop"

            if is_long:
                if opens[i] <= effective_stop:
                    close_position(i, opens[i], reason)
                elif lows[i] <= effective_stop:
                    close_position(i, effective_stop, reason)
                elif opens[i] >= tgt_price:
                    close_position(i, opens[i], "target")
                elif highs[i] >= tgt_price:
                    close_position(i, tgt_price, "target")
            else:
                if opens[i] >= effective_stop:
                    close_position(i, opens[i], reason)
                elif highs[i] >= effective_stop:
                    close_position(i, effective_stop, reason)
                elif opens[i] <= tgt_price:
                    close_position(i, opens[i], "target")
                elif lows[i] <= tgt_price:
                    close_position(i, tgt_price, "target")

        # ---- AFTER this candle's exit checks, and only while still in the
        # position: advance the trail from this candle's extreme (high for a
        # long, low for a short), so the new level applies starting NEXT
        # candle, never this one.
        #
        # This ordering is the entire correctness question a trailing stop
        # raises. Consider a candle that pushes to a new high and then falls
        # back through the level that high implies, all within itself: at
        # the instant its low printed, that high had not yet happened — the
        # candle isn't a stream of ticks here, high and low are just two
        # numbers describing a closed bar. Updating the trail from this
        # candle's own high and THEN checking this same candle's low against
        # it would let the exit use information that did not exist yet when
        # the low occurred: pure look-ahead, and the kind that makes a
        # backtest look quietly, plausibly better than the strategy really
        # is. So the update happens strictly after the checks above, using
        # `i` as the "signal candle" for any ATR trailing spec — safe
        # because candle i is now fully closed, unlike the entry case where
        # ATR is read at the signal candle rather than the fill candle.
        # ---- Square-off, checked AFTER the stop and target so it stays last
        # in the engine's worst-case ordering: if a candle both hit the stop
        # and reached square-off time, the stop is the worse outcome and the
        # one that would really have fired first. Filling at this candle's
        # open matches how every other exit here is priced.
        if in_pos and session.square_off and ist_times[i] >= session.square_off:
            close_position(i, opens[i], "square_off")

        if in_pos and strategy.risk.trailing_stop is not None:
            best_price = max(best_price, highs[i]) if is_long else min(best_price, lows[i])
            candidate = level_from_spec(
                strategy.risk.trailing_stop, best_price, i,
                atr_series, favourable=False, is_long=is_long,
            )
            # The trail only ever ratchets toward the position — never away
            # from it — so a pullback in `best_price` (a lower subsequent
            # high on a long, a higher subsequent low on a short) can never
            # loosen a level already locked in.
            trail_price = (
                candidate if trail_price is None
                else (max(trail_price, candidate) if is_long else min(trail_price, candidate))
            )

        # ---- At this candle's CLOSE: queue signals for the next open.
        # An entry is only queued while flat: after an exit fills at candle
        # i's open, re-entry needs a FRESH signal on candle i or later.
        if in_pos:
            if bool(exit_sig.iloc[i]):
                pending_exit_from = i
        else:
            if bool(entry_sig.iloc[i]):
                pending_entry_from = i

    # ---- History exhausted with a position still open: close at the last
    # close so every backtest trade is a complete round-trip. Flagged with
    # its own reason so it is visibly synthetic.
    if in_pos:
        last = len(df) - 1
        close_position(last, closes[last], "end_of_data")
        # close_position stamps exit_fill at index[last]; that is the candle
        # close time conceptually — acceptable for the single final trade.

    return SimResult(trades=trades, skipped=skipped)


def simulate(
    df: pd.DataFrame,
    strategy: Strategy,
    *,
    slippage_pct: float,
    cost_per_trade_inr: float,
) -> list[SimTrade]:
    """Completed trades only. See simulate_with_skips for skipped entries."""
    return simulate_with_skips(
        df, strategy,
        slippage_pct=slippage_pct,
        cost_per_trade_inr=cost_per_trade_inr,
    ).trades


# ---------------------------------------------------------------------------
# Metrics + kill rules (pure)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Orchestration (I/O)
# ---------------------------------------------------------------------------


def run_backtest(
    *,
    settings: Settings,
    store: SupabaseStore | None,
    client: MarketDataClient,
    strategies: list[Strategy],
    years: float,
    now_utc: datetime | None = None,
) -> tuple[uuid.UUID, list[dict[str, Any]]]:
    """Fetch, simulate, and score every strategy x instrument combination.

    Returns (batch_id, result_rows). Rows are shaped exactly like the
    backtest_results table so they can be inserted or written to CSV as-is.
    A fetch failure on one instrument skips that combination with a warning
    instead of killing the whole batch.
    """
    now = now_utc or datetime.now(tz=UTC)
    from_utc = now - timedelta(days=math.ceil(years * 365.25))
    batch_id = uuid.uuid4()
    today_ist = now.astimezone(IST).date()

    all_instruments = sorted({i for s in strategies for i in s.instruments})
    tokens = client.resolve_instrument_tokens(all_instruments, today_ist)

    rows: list[dict[str, Any]] = []
    requested_days = math.ceil(years * 365.25)
    for strategy in strategies:
        print(f"\n=== {strategy.name} ({strategy.timeframe}, {strategy.position_type}) ===")

        # Warn (but still run) when the provider cannot serve the requested
        # window — e.g. the free yfinance feed caps 15m history at ~60 days.
        # Running anyway is deliberate: the kill rules below will flag a thin
        # sample via min_trades, so nothing is hidden or silently trusted.
        max_days = getattr(client, "max_history_days", lambda _tf: None)(strategy.timeframe)
        if max_days is not None and requested_days > max_days:
            print(
                f"  NOTE: you asked for ~{requested_days} days but this data "
                f"provider only serves ~{max_days} days of {strategy.timeframe} "
                f"candles. Backtesting the shorter window instead — treat the "
                f"result as WEAK evidence.\n"
                f"        For deeper history, test the same rules on the 60m "
                f"(~730 days) or day (years) timeframe."
            )

        per_symbol: dict[str, tuple[ComboMetrics, pd.DataFrame]] = {}

        for instrument in strategy.instruments:
            try:
                df = client.fetch_historical_candles(
                    tokens[instrument], strategy.timeframe, from_utc, now,
                    closed_only=True, now_utc=now,
                )
            except KiteClientError as exc:
                print(f"  WARN  {instrument}: fetch failed, skipping — {exc}", file=sys.stderr)
                continue
            if df.empty:
                print(f"  WARN  {instrument}: no candles returned, skipping", file=sys.stderr)
                continue
            result = simulate_with_skips(
                df, strategy,
                slippage_pct=settings.slippage_pct,
                cost_per_trade_inr=settings.cost_per_trade_inr,
            )
            metrics = compute_metrics(result.trades)
            per_symbol[instrument] = (metrics, df)
            print(
                f"  {instrument:<16} trades={metrics.total_trades:>4} "
                f"net=₹{metrics.net_pnl:>10.2f} win%={metrics.win_rate_pct:>5.1f} "
                f"dd%={metrics.max_drawdown_pct:>5.1f}"
            )
            if result.skipped:
                print(
                    f"    {len(result.skipped)} entry signal(s) skipped: "
                    f"notional_per_trade is below the share price"
                )

        profitable = sum(1 for m, _ in per_symbol.values() if m.net_pnl > 0)
        for instrument, (metrics, df) in per_symbol.items():
            passed, flags = evaluate_kill_rules(metrics, profitable, len(strategy.instruments))
            rows.append(
                {
                    "batch_id": str(batch_id),
                    "strategy_name": strategy.name,
                    "instrument": instrument,
                    "timeframe": strategy.timeframe,
                    "start_date": df.index[0].astimezone(IST).date().isoformat(),
                    "end_date": df.index[-1].astimezone(IST).date().isoformat(),
                    "total_trades": metrics.total_trades,
                    "winning_trades": metrics.winning_trades,
                    "net_pnl": metrics.net_pnl,
                    "win_rate_pct": metrics.win_rate_pct,
                    "profit_factor": metrics.profit_factor,
                    "max_drawdown_pct": metrics.max_drawdown_pct,
                    "longest_losing_streak": metrics.longest_losing_streak,
                    "passed_kill_rules": passed,
                    "kill_rule_flags": flags,
                }
            )
    return batch_id, rows


def write_csv(batch_id: uuid.UUID, rows: list[dict[str, Any]], now_utc: datetime) -> Path:
    CSV_OUTPUT_DIR.mkdir(exist_ok=True)
    stamp = now_utc.astimezone(IST).strftime("%Y%m%d_%H%M%S")
    path = CSV_OUTPUT_DIR / f"backtest_{stamp}_{str(batch_id)[:8]}.csv"
    frame = pd.DataFrame(rows)
    if not frame.empty:
        # jsonb column is a nested dict; stringify it for the CSV.
        frame["kill_rule_flags"] = frame["kill_rule_flags"].apply(str)
    frame.to_csv(path, index=False)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch backtester (paper research only).")
    parser.add_argument("--years", type=float, default=2.0, help="years of history (default 2)")
    parser.add_argument("--strategy", help="run only this strategy name")
    parser.add_argument("--no-db", action="store_true", help="skip Supabase writes (CSV only)")
    args = parser.parse_args(argv)

    started = datetime.now(tz=UTC)
    try:
        # `--no-db` writes only a CSV, so on the free provider it needs no
        # Supabase account at all — you can try the system with zero signups.
        settings = get_settings(require_supabase=not args.no_db)
        if args.no_db and settings.requires_daily_login:
            # Kite still needs Supabase to read the daily token.
            settings = get_settings(require_supabase=True)

        # Prefer strategies stored in the database (what the dashboard edits);
        # fall back to the YAML file when running fully offline with --no-db.
        strategies: list[Strategy] = []
        if not args.no_db:
            probe = SupabaseStore.connect(settings)
            probe.seed_strategies_if_empty(load_strategy_documents())
            strategies = probe.list_strategies()
        if not strategies:
            strategies = load_strategies()
        strategies = [s for s in strategies if s.enabled]
        if args.strategy:
            strategies = [s for s in strategies if s.name == args.strategy]
            if not strategies:
                print(f"ERROR: no enabled strategy named {args.strategy!r}", file=sys.stderr)
                return 1
        if not strategies:
            print("ERROR: no enabled strategies in strategies.yaml", file=sys.stderr)
            return 1

        store = None if args.no_db else SupabaseStore.connect(settings)
        # Only the Kite provider needs a database connection to read the
        # daily token, so `--no-db` with the free provider needs no Supabase
        # setup at all (results go to CSV only).
        token_store = store
        if settings.requires_daily_login and token_store is None:
            token_store = SupabaseStore.connect(settings)
        client = create_data_client(settings, token_store, started.astimezone(IST).date())
        print(f"data provider: {describe_provider(settings)}")

        batch_id, rows = run_backtest(
            settings=settings, store=store, client=client,
            strategies=strategies, years=args.years,
        )

        csv_path = write_csv(batch_id, rows, started)
        print(f"\nCSV written: {csv_path}")

        if store is not None:
            inserted = store.insert_backtest_results(rows)
            print(f"Supabase: inserted {inserted} backtest_results rows (batch {batch_id})")
            store.write_run_audit(
                run_type="backtest", status="ok",
                run_started_at=started, run_finished_at=datetime.now(tz=UTC),
                reason=f"batch {batch_id}",
                details={
                    "combinations": len(rows),
                    "passed_kill_rules": sum(1 for r in rows if r["passed_kill_rules"]),
                    "years": args.years,
                },
            )

        passed = sum(1 for r in rows if r["passed_kill_rules"])
        print(f"\nDone: {len(rows)} combinations, {passed} passed all kill rules.")
        return 0

    except (TokenExpiredError, KiteClientError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # audit unexpected failures, then re-raise info
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        try:
            if not args.no_db:
                SupabaseStore.connect(get_settings()).write_run_audit(
                    run_type="backtest", status="error",
                    run_started_at=started, run_finished_at=datetime.now(tz=UTC),
                    reason=str(exc)[:500],
                )
        except Exception:
            pass  # auditing a failure must never mask the failure itself
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
