"""Evaluate one stored strategy the way the research loop will.

    python -m research.evaluate --strategy N200-PULLBACK-DAY
    python -m research.evaluate --strategy N200-PULLBACK-DAY --max-stocks 5 --stock-timeframes day

1. Test every stock x timeframe (and index x timeframe) on TRAINING years only.
2. Pick one TIMEFRAME by the fixed rule: the basket of every stock on it.
3. Open the locked final year ONCE, for that basket: what Rs 1 lakh spread
   equally across the stocks became, month by month.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
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


def basket_label(stocks: int) -> str:
    """What the grid's 'traded on' column says for a basket."""
    return f"NIFTY200 basket ({stocks} stocks)"


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


@dataclass(frozen=True)
class LockedBasket:
    """The exam, for one timeframe's basket (design 2026-09-16 §2.4)."""

    lakh: Any                       # research.lakh.LakhResult
    hold_end_value: float | None
    basket: Any                     # research.portfolio.Basket
    trades: list                    # every locked-year SimTrade across the basket
    equity: list[dict[str, Any]]    # daily rows for the chart
    stocks_short: list[str]         # stocks whose candles stop before DATA_END


def locked_year(
    strategy: Any, timeframe: str, stocks: Sequence[str], reader: Any, windows: Any, *,
    slippage_pct: float, cost_model: Any, workers: int = 1,
) -> LockedBasket | None:
    """Open the locked year ONCE, for one timeframe's basket of every stock.

    Every stock is simulated over the FULL window so its indicators are warm
    at the boundary, then only the locked part is kept (by entry, as
    walk_forward.split_trades does). Both research commands come through
    here, so the one place the locked year is opened is the same code whether
    a human or the loop chose the strategy.

    Returns None when not one stock produced a locked-year result.
    """
    from research.lakh import LakhResult
    from research.portfolio import build_basket, daily_equity, dip_of_equity, restrict_to
    from research.sweep import run_sweep
    from research.universe import Combo
    from research.windows import LOCKED_DAYS

    combos = [Combo(s, timeframe, False) for s in stocks]
    full = run_sweep(
        strategy, combos, reader, lambda c: windows.full(is_index=False, timeframe=timeframe),
        slippage_pct=slippage_pct, cost_model=cost_model, workers=workers,
    )
    locked = [restrict_to(r, windows.locked_from_utc) for r in full if r.skipped_reason is None]
    basket = build_basket(locked, timeframe=timeframe)
    if basket is None:
        return None

    closes = {}
    short: list[str] = []
    for r in locked:
        frame = reader.candles(r.symbol, "day", windows.locked_from_utc, windows.end_utc)
        if frame.empty:
            continue
        closes[r.symbol] = frame["close"]
        if frame.index[-1].astimezone(IST).date() < windows.data_end:
            short.append(r.symbol)

    equity = daily_equity(locked, closes_by_symbol=closes)
    trades = [t for r in locked for t in r.trades]
    end_value = basket.end_value()
    years = LOCKED_DAYS / 365.25
    cagr = (round(((end_value / 100_000.0) ** (1 / years) - 1) * 100, 4)
            if end_value > 0 else None)
    lakh = LakhResult(
        start_value=100_000.0,
        end_value=end_value,
        trades=basket.trades,
        winning_trades=basket.winning_trades,
        worst_dip_pct=dip_of_equity(equity) if equity else basket.worst_dip_pct,
        cagr_pct=cagr,
    )
    return LockedBasket(
        lakh=lakh, hold_end_value=basket.holding_end_value(), basket=basket,
        trades=trades, equity=equity, stocks_short=short,
    )


def print_locked(exam: LockedBasket, *, is_short: bool, data_end: Any) -> list[str]:
    """The exam, on the console. Returns the warnings it raises."""
    from research.lakh import passed
    from research.portfolio import luck_label
    from research.segment import meets_target

    b, lakh = exam.basket, exam.lakh
    win_rate = 100 * lakh.winning_trades / lakh.trades if lakh.trades else 0.0
    warnings: list[str] = []
    print("\nLOCKED YEAR")
    print(f"  Rs 1,00,000 spread over {b.stocks} stocks -> {_rupees(lakh.end_value)}")
    print(f"  just holding the same basket -> {_rupees(exam.hold_end_value)}"
          + ("  (the market's move; this strategy is short)" if is_short else ""))
    print(f"  average month {b.avg_month_pct:+.2f}%  ({meets_target(b.avg_month_pct)}) · "
          f"{b.months_positive_pct:.0f}% of months up · worst month {b.worst_month_pct:+.2f}%")
    print(f"  {lakh.trades} trades ({b.trades_per_month:.1f}/month) · won {win_rate:.0f}% · "
          f"worst dip {lakh.worst_dip_pct:.1f}% (closed trades) · luck check: {luck_label(b.edge_t)}")
    if exam.stocks_short:
        print(f"  NOTE: {len(exam.stocks_short)} stocks' candles stop before DATA_END {data_end}")
        warnings.append(f"{len(exam.stocks_short)} of the basket's stocks stop before DATA_END {data_end}")
    beat = exam.hold_end_value is not None and lakh.end_value > exam.hold_end_value
    print(f"  verdict: {'PASSED' if passed(lakh) else 'FAILED'} · beat holding: "
          f"{'yes' if beat else 'no'}")
    return warnings


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
    from research.picker import disqualified, pick_timeframe
    from research.records import basket_trade_rows, combo_rows, equity_rows, run_row
    from research.store import ResearchStoreError, save_run
    from research.summary import build_summary
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

    def window_days_for(r) -> int:
        return windows.training_days(
            is_index=r.is_index, timeframe=r.timeframe,
            data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
        )

    summary = build_summary(results, cost_model, window_days_for=window_days_for)
    print(f"\nTRAINING   {counts.tested} tested, {counts.skipped} skipped, "
          f"{counts.profitable} profitable after fees, {summary.combos_beating_hold} beat holding "
          f"({elapsed:.0f}s)")
    print("  baskets (every stock on one timeframe, Rs 1 lakh each, per month):")
    for b in summary.baskets:
        print(f"    {b['timeframe']:4s} {b['avg_month_pct']:+6.2f}%/month · "
              f"{b['months_positive_pct']:3.0f}% up · worst month {b['worst_month_pct']:+6.2f}% · "
              f"{b['trades_per_month']:6.1f} trades/month · {b['luck_check']}"
              + ("" if b["qualifies"] else f" · NOT PICKED: {b['why_not']}"))

    pick = pick_timeframe(results)
    warnings = [
        f"prices frozen at {data_end}; today's NIFTY200 list applied to the past (survivorship)",
    ]
    training_basket = None if pick is None else pick.basket

    if pick is None:
        print("\nNo qualifying timeframe: no basket beat holding while trading 10+ times a "
              "month within a 30% dip. Locked year not opened.")
        warnings.append(f"no qualifying timeframe among {counts.tested} combinations")
        exam = None
    else:
        b = pick.basket
        print(f"\nPICK       {pick.timeframe} basket of {b.stocks} stocks  (training: "
              f"{b.avg_month_pct:+.2f}%/month, {b.months_positive_pct:.0f}% months up, "
              f"worst dip {b.worst_dip_pct:.1f}%, {luck(b)})")
        exam = locked_year(
            strategy, pick.timeframe, stocks, reader, windows,
            slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
        )
        if exam is None:
            print("\nThe basket has no locked-year data. Locked year not opened.")
            warnings.append("the picked basket had no locked-year data")
        else:
            warnings += print_locked(exam, is_short=strategy.position_type == "short",
                                     data_end=data_end)
            if len(exam.trades) > 2000:
                warnings.append(f"{len(exam.trades)} locked-year trades: too many to store, "
                                "the daily balance is stored instead")

    print("\nWARNINGS")
    for line in warnings:
        print(f"  {line}")

    if args.no_save:
        print("\nnot saved (--no-save)")
        return 0

    row = run_row(
        started_at=started_at, finished_at=datetime.now(UTC), status="completed",
        data_end=data_end, locked_from=windows.locked_from,
        strategy_name=args.strategy,
        pick_symbol=None if pick is None or exam is None else basket_label(exam.basket.stocks),
        pick_timeframe=None if pick is None else pick.timeframe,
        locked=None if exam is None else exam.lakh,
        hold_end_value=None if exam is None else exam.hold_end_value,
        combos_profitable=counts.profitable, combos_tested=counts.tested,
        warnings=warnings,
        basket=None if exam is None else exam.basket, training_basket=training_basket,
    )
    children: dict[str, Any] = {
        "combos": combo_rows(_PENDING, results, cost_model, window_days_for=window_days_for),
    }
    if exam is not None:
        children["locked_trades"] = basket_trade_rows(_PENDING, exam.trades, stocks=exam.basket.stocks)
        children["equity"] = equity_rows(_PENDING, exam.equity)
    try:
        saved = save_run(store._client, run=row, **children)
    except ResearchStoreError as exc:
        print(f"\nWARNING: the result was NOT stored: {exc}", file=sys.stderr)
        return 1
    print(f"\nsaved as run {saved}")
    return 0


def luck(basket: Any) -> str:
    from research.portfolio import luck_label

    return luck_label(basket.edge_t)


if __name__ == "__main__":
    raise SystemExit(main())
