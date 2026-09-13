"""Evaluate one stored strategy the way the research loop will.

    python -m research.evaluate --strategy N200-PULLBACK-DAY
    python -m research.evaluate --strategy N200-PULLBACK-DAY --max-stocks 5 --stock-timeframes day

1. Test every stock x timeframe (and index x timeframe) on TRAINING years only.
2. Pick one combination by the fixed rule.
3. Open the locked final year ONCE, for that pick: what Rs 1 lakh became.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from config import IST, UTC, get_settings, use_utf8_stdout

BOOKKEEPING = ("status", "raw_source", "validation_errors")
FAR_PAST = datetime(2000, 1, 1, tzinfo=UTC)
FAR_FUTURE = datetime(2100, 1, 1, tzinfo=UTC)
# How far back to scan 5-minute candles when placing DATA_END. Only the tail
# of a history can carry the answer, and reading nine years for 200 stocks
# costs about two minutes before the sweep even starts.
SCAN_DAYS = 180
# The children are built before the run row exists; save_run replaces this with
# the real id. Naming it beats a bare empty string in a row that must not ship.
_PENDING = "pending"


def strategy_document(docs: Sequence[Mapping[str, Any]], name: str) -> dict[str, Any]:
    """The stored definition of `name`, ready for the parser."""
    for doc in docs:
        if doc.get("name") == name:
            if doc.get("status", "valid") != "valid":
                raise ValueError(f"strategy {name!r} is a draft and cannot be evaluated")
            return {k: v for k, v in doc.items() if k not in BOOKKEEPING}
    available = ", ".join(sorted(str(d.get("name")) for d in docs)) or "(none)"
    raise KeyError(f"no strategy named {name!r}. Available: {available}")


def _rupees(value: float | None) -> str:
    return "n/a" if value is None else f"Rs {value:,.0f}"


def prepare_run(settings: Any, store: Any, stocks: Sequence[str]) -> tuple[Any, Any, Any, list]:
    """The frozen prices and the research calendar every research command needs.

    Returns `(reader, windows, data_end, symbol_ends)`, where `symbol_ends`
    holds one date per stock whose history ends in a complete session - the
    coverage DATA_END was placed from, which the caller prints.

    Raises ValueError when not one stock has a daily candle, because DATA_END
    cannot be placed without one.
    """
    from research.prices import FrozenPriceReader, load_adjustments, load_instrument_ids
    from research.universe import INDEXES
    from research.windows import ResearchWindows, data_end_from, universe_data_end

    ids = load_instrument_ids(store._client, [*stocks, *INDEXES])
    stock_ids = [ids[s] for s in stocks]
    reader = FrozenPriceReader(settings.candle_root, ids, load_adjustments(store._client, stock_ids),
                               frozenset(INDEXES))

    # DATA_END comes from the whole universe, never one reference symbol. In
    # August 2026, 184 of 200 stocks lost their closing candles every day; a
    # single symbol would have let that month into the locked year.
    #
    # Daily candles are small, so they are read in full; the 5-minute scan
    # starts SCAN_DAYS before the newest daily candle, because only the tail
    # of a history can decide where it ends.
    day_index = {s: reader.candles(s, "day", FAR_PAST, FAR_FUTURE).index for s in stocks}
    newest_daily = max((idx.max() for idx in day_index.values() if len(idx)), default=None)
    if newest_daily is None:
        raise ValueError("no daily candles for any stock, so DATA_END cannot be placed")
    scan_from = newest_daily - timedelta(days=SCAN_DAYS)

    symbol_ends = []
    for symbol in stocks:
        try:
            symbol_ends.append(data_end_from(
                reader.candles(symbol, "5m", scan_from, FAR_FUTURE).index,
                day_index[symbol],
            ))
        except ValueError:
            continue        # no complete session in the scan window: counts against coverage
    data_end = universe_data_end(symbol_ends)
    return reader, ResearchWindows(data_end), data_end, symbol_ends


def locked_year(
    strategy: Any, result: Any, reader: Any, windows: Any, *,
    slippage_pct: float, cost_model: Any,
) -> tuple[Any, float | None, list, Any]:
    """Open the locked year ONCE, for one pick.

    Returns `(locked, hold_end_value, locked_trades, frame)`. Both commands
    come through here, so the one place the locked year is opened is the same
    code whether a human or the loop chose the strategy.
    """
    from backtest import simulate_any
    from research.lakh import compound, just_holding
    from research.windows import LOCKED_DAYS
    from walk_forward import split_trades

    frame = reader.candles(result.symbol, result.timeframe,
                           *windows.full(is_index=result.is_index, timeframe=result.timeframe))
    simulated = simulate_any(frame, strategy, slippage_pct=slippage_pct,
                             cost_per_trade_inr=0.0, cost_model=cost_model)
    _, locked_trades = split_trades(simulated.trades, windows.locked_from_utc)
    locked = compound(locked_trades, cost_model, window_days=LOCKED_DAYS)
    hold = just_holding(reader.candles(result.symbol, "day", windows.locked_from_utc, windows.end_utc),
                        cost_model)
    return locked, hold, list(locked_trades), frame


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Evaluate one strategy across every combination.")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--max-stocks", type=int, default=0, help="test only the first N stocks (quick run)")
    parser.add_argument("--stock-timeframes", default="", help="comma-separated, e.g. day,60m (quick run)")
    parser.add_argument("--no-save", action="store_true",
                        help="print the result without storing it")
    args = parser.parse_args(argv)
    started_at = datetime.now(UTC)

    from costs import HoldingCostModel
    from db import SupabaseStore
    from research.lakh import equity_series, passed
    from research.picker import pick_best
    from research.records import combo_rows, equity_rows, locked_trade_rows, run_row
    from research.store import ResearchStoreError, save_run
    from research.sweep import count_results, run_sweep
    from research.universe import STOCK_TIMEFRAMES, document_uses_volume, research_combos
    from strategy.v3 import is_v3_document, parse_machine
    from strategy_schema import parse_strategy_dict
    from universes import newest_snapshot, parse_constituent_csv

    settings = get_settings(require_supabase=True)
    store = SupabaseStore.connect(settings)

    doc = strategy_document(store.list_strategy_documents(), args.strategy)
    strategy = parse_machine(doc) if is_v3_document(doc) else parse_strategy_dict(doc)

    snapshot_path, as_of = newest_snapshot("NIFTY200")
    stocks = list(parse_constituent_csv(snapshot_path.read_text(encoding="utf-8")))
    if args.max_stocks:
        stocks = stocks[: args.max_stocks]
    timeframes = tuple(t.strip() for t in args.stock_timeframes.split(",") if t.strip()) or STOCK_TIMEFRAMES

    try:
        reader, windows, data_end, symbol_ends = prepare_run(settings, store, stocks)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    volume_rules = document_uses_volume(doc)
    combos = research_combos(stocks, include_indexes=not volume_rules, stock_timeframes=timeframes)
    cost_model = HoldingCostModel()

    print(f"strategy   {args.strategy}")
    print(f"prices     frozen at {data_end} (DATA_END, complete for "
          f"{len(symbol_ends)} of {len(stocks)} stocks)")
    print(f"training   before {windows.locked_from}")
    print(f"locked     {windows.locked_from} -> {data_end}  (opened once, at the end)")
    print(f"universe   NIFTY200 as of {as_of} ({len(stocks)} stocks)"
          + ("" if not volume_rules else "; indexes skipped: rules read volume"))
    print(f"testing    {len(combos)} combinations on {args.workers} worker(s)...")

    started = datetime.now(UTC)
    results = run_sweep(
        strategy, combos, reader,
        lambda c: windows.training(is_index=c.is_index, timeframe=c.timeframe),
        slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
    )
    elapsed = (datetime.now(UTC) - started).total_seconds()
    counts = count_results(results)
    print(f"\nTRAINING   {counts.tested} tested, {counts.skipped} skipped, "
          f"{counts.profitable} profitable after fees  ({elapsed:.0f}s)")
    for r in sorted((r for r in results if r.skipped_reason is None), key=lambda r: -r.net_pnl)[:10]:
        print(f"  {r.symbol:22s} {r.timeframe:4s} trades={len(r.trades):5d}  net={_rupees(r.net_pnl)}")

    def window_days_for(r) -> int:
        return windows.training_days(
            is_index=r.is_index, timeframe=r.timeframe,
            data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
        )

    pick = pick_best(results, cost_model, window_days_for=window_days_for)
    if pick is None:
        print("\nNo qualifying pick (needs 30+ training trades and a worst dip within 30%). "
              "Locked year not opened.")
        if args.no_save:
            print("\nnot saved (--no-save)")
            return 0
        try:
            saved = save_run(
                store._client,
                run=run_row(
                    started_at=started_at, finished_at=datetime.now(UTC), status="completed",
                    data_end=data_end, locked_from=windows.locked_from,
                    strategy_name=args.strategy, pick_symbol=None, pick_timeframe=None,
                    locked=None, hold_end_value=None,
                    combos_profitable=counts.profitable, combos_tested=counts.tested,
                    warnings=[f"no qualifying pick among {counts.tested} combinations"],
                ),
                combos=combo_rows(_PENDING, results, cost_model, window_days_for=window_days_for),
            )
        except ResearchStoreError as exc:
            print(f"\nWARNING: the result was NOT stored: {exc}", file=sys.stderr)
            return 1
        print(f"\nsaved as run {saved}")
        return 0

    p = pick.result
    print(f"\nPICK       {p.symbol} · {p.timeframe}  (training: {pick.training.trades} trades, "
          f"CAGR {pick.training.cagr_pct:.2f}%, worst dip {pick.training.worst_dip_pct:.1f}%)")

    locked, hold, locked_trades, frame = locked_year(
        strategy, p, reader, windows,
        slippage_pct=settings.slippage_pct, cost_model=cost_model,
    )
    win_rate = 100 * locked.winning_trades / locked.trades if locked.trades else 0.0

    print("\nLOCKED YEAR")
    print(f"  Rs 1,00,000 -> {_rupees(locked.end_value)}")
    print(f"  just holding {p.symbol} -> {_rupees(hold)}"
          + ("  (the market's move; this strategy is short)" if strategy.position_type == "short" else ""))
    print(f"  {locked.trades} trades ({locked.trades / 12:.1f}/month) · won {win_rate:.0f}% · "
          f"worst dip {locked.worst_dip_pct:.1f}%")

    # Coverage is a floor, so up to 10% of stocks may end before DATA_END. If
    # the winner is one of them, its locked year is partly empty while still
    # being judged over a full 365 days.
    pick_last = frame.index[-1].astimezone(IST).date() if len(frame) else None
    if pick_last is not None and pick_last < data_end:
        print(f"  NOTE: this combination's candles stop {pick_last}, before DATA_END "
              f"{data_end} - its locked year is only partly covered")

    beat = hold is not None and locked.end_value > hold
    print(f"  verdict: {'PASSED' if passed(locked) else 'FAILED'} · beat holding: {'yes' if beat else 'no'}")
    print(f"  broad or lucky: profitable in {counts.profitable} of {counts.tested} training combinations")

    warnings = [
        f"prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)",
        f"{counts.tested} combinations tried: some look good in training by luck alone",
    ]
    if pick_last is not None and pick_last < data_end:
        warnings.append(f"the pick's candles stop {pick_last}, before DATA_END {data_end}")

    print("\nWARNINGS")
    for line in warnings:
        print(f"  {line}")

    if args.no_save:
        print("\nnot saved (--no-save)")
        return 0

    day_candles = reader.candles(p.symbol, "day", windows.locked_from_utc, windows.end_utc)
    try:
        saved = save_run(
            store._client,
            run=run_row(
                started_at=started_at, finished_at=datetime.now(UTC), status="completed",
                data_end=data_end, locked_from=windows.locked_from,
                strategy_name=args.strategy, pick_symbol=p.symbol, pick_timeframe=p.timeframe,
                locked=locked, hold_end_value=hold,
                combos_profitable=counts.profitable, combos_tested=counts.tested,
                warnings=warnings,
            ),
            combos=combo_rows(_PENDING, results, cost_model, window_days_for=window_days_for),
            locked_trades=locked_trade_rows(_PENDING, locked_trades, cost_model),
            equity=equity_rows(_PENDING, equity_series(
                locked_trades, cost_model,
                day_index=day_candles.index,
                closes=day_candles["close"] if not day_candles.empty else None,
            )),
        )
    except ResearchStoreError as exc:
        print(f"\nWARNING: the result was NOT stored: {exc}", file=sys.stderr)
        return 1
    print(f"\nsaved as run {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
