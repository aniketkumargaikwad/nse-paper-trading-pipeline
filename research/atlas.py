"""The atlas: baseline signals measured once, so the AI designs from evidence.

    python -m research.atlas --strategy RSI2-DIP-TREND
    python -m research.atlas --all --max-stocks 5 --stock-timeframes day --no-save
    python -m research.atlas --list

WHY
---
Every morning Opus proposed from folklore: three days running it re-invented
the same "hold above the 200-day average" family the memos had already
measured as beta. What it lacked was a table of what the ordinary building
blocks actually earn on THIS data - RSI dips, channel breakouts, band
reverts, gap fades, VWAP reverts - each as a ten-slot account, per month,
per timeframe. Measured once (prices are frozen), stored, and shown in every
proposal prompt, so a version is spent combining what works rather than
re-testing what does not.

TRAINING YEARS ONLY. The atlas never opens the locked year; its rows are
built from the same TrainingSummary Opus already sees (design 2.4).

Every baseline goes through research.checker like a proposal, so the same
research rules apply: notional sizing, placeholder symbols, paused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from config import IST, UTC, get_settings, use_utf8_stdout

ATR_RISK = """\
risk:
  stop_loss: {type: atr, period: 14, multiplier: 2.0}
  target: {type: atr, period: 14, multiplier: 6.0}
"""

INTRADAY_RISK = """\
risk:
  stop_loss: {type: percent, value: 1.0}
  target: {type: percent, value: 3.0}
"""


def _machine(entry: str, exit_: str, *, side: str = "long", risk: str = ATR_RISK,
             square_off: str | None = None, max_cycles: int = 1) -> str:
    """A two-state machine: wait for `entry`, hold until `exit_`."""
    session = f'session:\n  square_off: "{square_off}"\n' if square_off else ""
    return f"""\
version: 3
initial: waiting
on_position_closed: waiting
states:
  - name: waiting
    transitions:
      - when: "{entry}"
        enter: {{side: {side}}}
        goto: holding
  - name: holding
    transitions:
      - when: "{exit_}"
        exit: {{reason: rule}}
        goto: waiting
{risk}{session}max_cycles_per_day: {max_cycles}
"""


# name -> (one-line description, strategy YAML). Descriptions are what Opus
# reads beside the numbers, so they say the rule, not the name.
STRATEGIES: dict[str, tuple[str, str]] = {
    "RSI2-DIP-TREND": (
        "buy when RSI(2) < 10 above the 200-bar average; sell when RSI(2) > 60 or after 5 bars",
        _machine("rsi(2) < 10 and close > sma(200)", "rsi(2) > 60 or position.bars_held >= 5"),
    ),
    "RSI2-DIP-ANY": (
        "buy when RSI(2) < 10, no trend filter; sell when RSI(2) > 60 or after 5 bars",
        _machine("rsi(2) < 10", "rsi(2) > 60 or position.bars_held >= 5"),
    ),
    "DONCHIAN-20-10": (
        "buy a close above the 20-bar high; sell a close below the 10-bar low",
        _machine("close > donchian.upper(20)[1]", "close < donchian.lower(10)[1]"),
    ),
    "EMA-20-50-CROSS": (
        "buy when EMA20 crosses above EMA50; sell when it crosses back below",
        _machine("ema(20) > ema(50) and ema(20)[1] <= ema(50)[1]", "ema(20) < ema(50)"),
    ),
    "BB-LOWER-REVERT": (
        "buy a close under the lower Bollinger band (20, 2); sell at the middle band or after 10 bars",
        _machine("close < bbands.lower(20, 2)",
                 "close > bbands.middle(20, 2) or position.bars_held >= 10"),
    ),
    "GAP-UP-FADE": (
        "short a bar that opens 1.5% above yesterday's close and closes down; cover after 6 bars, "
        "squared off 15:10",
        _machine("open > prev_day.close * 1.015 and candle.is_bearish", "position.bars_held >= 6",
                 side="short", risk=INTRADAY_RISK, square_off="15:10"),
    ),
    "GAP-DOWN-BOUNCE": (
        "buy a bar that opens 1.5% below yesterday's close and closes up; sell after 6 bars, "
        "squared off 15:10",
        _machine("open < prev_day.close * 0.985 and candle.is_bullish", "position.bars_held >= 6",
                 risk=INTRADAY_RISK, square_off="15:10"),
    ),
    "VWAP-REVERT-LONG": (
        "buy 1% under session VWAP with RSI(14) < 30; sell back at VWAP, squared off 15:15",
        _machine("close < vwap() * 0.99 and rsi(14) < 30", "close > vwap()",
                 risk=INTRADAY_RISK, square_off="15:15", max_cycles=2),
    ),
    "SUPERTREND-10-3": (
        "buy when Supertrend(10, 3) turns up; sell when it turns down",
        _machine("supertrend.direction(10, 3) == 1 and supertrend.direction(10, 3)[1] == -1",
                 "supertrend.direction(10, 3) == -1"),
    ),
    "MACD-ZERO-CROSS": (
        "buy when the MACD line crosses above zero; sell when it falls below zero",
        _machine("macd.line(12, 26, 9) > 0 and macd.line(12, 26, 9)[1] <= 0",
                 "macd.line(12, 26, 9) < 0"),
    ),
    "HIGH-250-BREAKOUT": (
        "buy a close above the 250-bar high; sell a close below the 50-bar average",
        _machine("close > donchian.upper(250)[1]", "close < sma(50)"),
    ),
    "PULLBACK-EMA20-UPTREND": (
        "in an EMA20 > EMA50 uptrend, buy a down-day-then-up-day below EMA20; sell 3% above "
        "EMA20 or after 10 bars",
        _machine("ema(20) > ema(50) and close < ema(20) and close > close[1]",
                 "close > ema(20) * 1.03 or position.bars_held >= 10"),
    ),
    "ROC-20-MOMENTUM": (
        "buy when the 20-bar rate of change exceeds 10% above the 50-bar average; sell when it "
        "turns negative",
        _machine("roc(20) > 10 and close > sma(50)", "roc(20) < 0"),
    ),
    "SQUEEZE-BREAKOUT": (
        "buy a close above the upper Bollinger band when 20-bar volatility is under 2%; sell at "
        "the middle band or after 10 bars",
        _machine("stddev(20) / close < 0.02 and close > bbands.upper(20, 2)",
                 "close < bbands.middle(20, 2) or position.bars_held >= 10"),
    ),
    "RSI2-RIP-SHORT": (
        "short when RSI(2) > 90 below the 200-bar average; cover when RSI(2) < 50 or after 8 "
        "bars, squared off 15:10",
        _machine("rsi(2) > 90 and close < sma(200)", "rsi(2) < 50 or position.bars_held >= 8",
                 side="short", risk=INTRADAY_RISK, square_off="15:10"),
    ),
}


def atlas_line(row: Mapping[str, Any]) -> str:
    """One line Opus reads: the rule, its best account, how broadly it worked."""
    facts = row.get("training_summary") or {}
    if isinstance(facts, str):
        try:
            facts = json.loads(facts)
        except ValueError:
            facts = {}
    parts = [f"{row.get('name')} ({row.get('description')})"]
    accounts = facts.get("accounts") or []
    if accounts:
        pool = [a for a in accounts if a.get("qualifies")] or accounts
        best = max(pool, key=lambda a: a.get("avg_month_pct") if a.get("avg_month_pct") is not None
                   else float("-inf"))
        parts.append(
            f"best account {best.get('timeframe')}: {best.get('avg_month_pct', 0):+.2f}%/month, "
            f"{best.get('months_positive_pct', 0):.0f}% months up, {best.get('luck_check')}"
            + ("" if best.get("qualifies") else f" (not pickable: {best.get('why_not')})")
        )
        per_tf = ", ".join(f"{a.get('timeframe')} {a.get('avg_month_pct', 0):+.2f}"
                           for a in accounts)
        parts.append(f"per timeframe: {per_tf}")
    tested, beat = facts.get("combos_tested"), facts.get("combos_beating_hold")
    if tested:
        parts.append(f"{beat if beat is not None else '?'} of {tested} beat holding")
    return " — ".join(parts)


def atlas_lines(client: Any) -> list[str]:
    """Every stored baseline, as prompt lines. Empty when the atlas has not been built."""
    try:
        rows = (
            client.table("research_atlas")
            .select("name,description,training_summary")
            .order("name").execute().data
        ) or []
    except Exception:       # noqa: BLE001 - an absent atlas is the state before it is built
        return []
    return [atlas_line(row) for row in rows]


def main(argv: list[str] | None = None) -> int:
    use_utf8_stdout()
    parser = argparse.ArgumentParser(description="Measure baseline signals on the training years.")
    parser.add_argument("--strategy", action="append", default=[], help="a baseline by name")
    parser.add_argument("--all", action="store_true", help="every baseline in turn")
    parser.add_argument("--list", action="store_true", help="print the baselines and exit")
    parser.add_argument("--workers", type=int, default=os.cpu_count() or 1)
    parser.add_argument("--max-stocks", type=int, default=0)
    parser.add_argument("--stock-timeframes", default="")
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args(argv)

    if args.list:
        for name, (description, _) in STRATEGIES.items():
            print(f"{name:24s} {description}")
        return 0

    names = list(STRATEGIES) if args.all else args.strategy
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown or not names:
        print(f"ERROR: unknown baseline(s) {unknown or '(none given)'}; --list shows them",
              file=sys.stderr)
        return 1

    from costs import HoldingCostModel
    from db import SupabaseStore
    from research.checker import check_proposal
    from research.evaluate import prepare_run
    from research.summary import build_summary
    from research.sweep import count_results, run_sweep
    from research.universe import (
        STOCK_TIMEFRAMES,
        document_uses_volume,
        index_timeframes_within,
        research_combos,
    )
    from universes import newest_snapshot, parse_constituent_csv

    settings = get_settings(require_supabase=True)
    store = SupabaseStore.connect(settings)
    snapshot_path, as_of = newest_snapshot("NIFTY200")
    stocks = list(parse_constituent_csv(snapshot_path.read_text(encoding="utf-8")))
    if args.max_stocks:
        stocks = stocks[: args.max_stocks]
    timeframes = tuple(t.strip() for t in args.stock_timeframes.split(",") if t.strip()) \
        or STOCK_TIMEFRAMES
    try:
        reader, windows, data_end, _ = prepare_run(settings, store, stocks, timeframes=timeframes)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    cost_model = HoldingCostModel()
    day = datetime.now(IST).date()

    def window_days_for(r) -> int:
        return windows.training_days(
            is_index=r.is_index, timeframe=r.timeframe,
            data_from=r.first_candle.astimezone(IST).date() if r.first_candle else None,
        )

    print(f"atlas      {len(names)} baseline(s) · NIFTY200 as of {as_of} ({len(stocks)} stocks) · "
          f"training before {windows.locked_from} · {args.workers} worker(s)")
    for name in names:
        description, yaml_text = STRATEGIES[name]
        checked = check_proposal(yaml_text, idea=name, day=day, version=1)
        combos = research_combos(stocks, include_indexes=not document_uses_volume(checked.document),
                                 stock_timeframes=timeframes,
                                 index_timeframes=index_timeframes_within(timeframes))
        started = datetime.now(UTC)
        results = run_sweep(
            checked.strategy, combos, reader,
            lambda c: windows.training(is_index=c.is_index, timeframe=c.timeframe),
            slippage_pct=settings.slippage_pct, cost_model=cost_model, workers=args.workers,
        )
        elapsed = (datetime.now(UTC) - started).total_seconds()
        counts = count_results(results)
        summary = build_summary(results, cost_model, window_days_for=window_days_for)
        row = {
            "name": name, "description": description, "strategy_yaml": yaml_text,
            "data_end": data_end.isoformat(), "training_summary": summary.as_dict(),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        print(f"\n{name}: {counts.tested} tested, {summary.combos_beating_hold} beat holding "
              f"({elapsed:.0f}s)")
        print("  " + atlas_line(row))
        if args.no_save:
            continue
        try:
            store._client.table("research_atlas").upsert(row, on_conflict="name").execute()
            print("  stored")
        except Exception as exc:        # noqa: BLE001 - one row's failure must not end the batch
            print(f"  WARNING: not stored: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
