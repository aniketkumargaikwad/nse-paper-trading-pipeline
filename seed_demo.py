"""Populate Supabase with SAMPLE data so you can preview the dashboard.

This lets you see the dashboard fully populated BEFORE you finish the Kite
setup. It needs only your two Supabase values (SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY) in `.env` — no Kite keys required.

Everything it writes is clearly marked as demo:
  * strategy names start with "DEMO-"
  * every trade/position/backtest row belongs to a DEMO- strategy
  * audit rows have a reason starting with "DEMO:"
so it can be removed cleanly and never collides with real data.

USAGE (from the project folder)
-------------------------------
    .venv\\Scripts\\python.exe seed_demo.py             # insert sample data
    .venv\\Scripts\\python.exe seed_demo.py --dry-run   # build rows, print, DON'T connect
    .venv\\Scripts\\python.exe seed_demo.py --clear      # remove all DEMO- data and exit

After seeding, open the dashboard (with your anon key configured) and you
should see a leaderboard, equity/drawdown charts, open positions, today's
trades, and a green "last engine run" strip. When you're done exploring:

    .venv\\Scripts\\python.exe seed_demo.py --clear

IMPORTANT: this is fake data for UI preview only. It is NOT a backtest and
means nothing about any strategy's real performance.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any

# Importing config loads the .env file (via python-dotenv) as a side effect,
# so the two Supabase environment variables become available below.
from config import IST, UTC

DEMO_PREFIX = "DEMO-"
DEMO_AUDIT_REASON_PREFIX = "DEMO:"

# A small, liquid instrument set purely for realistic-looking labels.
INSTRUMENTS = ["NSE:RELIANCE", "NSE:HDFCBANK", "NSE:ICICIBANK", "NSE:INFY", "NSE:TCS"]

# Rough per-share price anchors so P&L numbers look believable.
BASE_PRICE = {
    "NSE:RELIANCE": 2900.0,
    "NSE:HDFCBANK": 1650.0,
    "NSE:ICICIBANK": 1200.0,
    "NSE:INFY": 1550.0,
    "NSE:TCS": 3900.0,
}

# Two demo strategies. enabled=False makes clear on the dashboard that these
# are not live — and the real TF-EMA-RSI-15m-v1 strategy is left untouched.
DEMO_STRATEGIES = [
    {"name": f"{DEMO_PREFIX}Momentum-15m", "timeframe": "15m", "stop": 0.7, "target": 1.5},
    {"name": f"{DEMO_PREFIX}MeanReversion-60m", "timeframe": "60m", "stop": 1.0, "target": 2.0},
]

COST_PER_TRADE = 30.0
QTY = 1


# ---------------------------------------------------------------------------
# Pure row builders (unit-tested, no network) — each returns table-shaped dicts
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    """Serialize an aware datetime as a UTC ISO string for Supabase."""
    if dt.tzinfo is None:
        raise ValueError("refusing to serialize a naive datetime")
    return dt.astimezone(UTC).isoformat()


def _recent_weekdays(now_utc: datetime, count: int) -> list[date]:
    """The `count` most recent IST weekdays, oldest first (holidays ignored —
    this is cosmetic demo data, not a real calendar)."""
    days: list[date] = []
    d = now_utc.astimezone(IST).date()
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def build_strategy_rows(now_utc: datetime) -> list[dict[str, Any]]:
    rows = []
    for s in DEMO_STRATEGIES:
        rows.append(
            {
                "name": s["name"],
                "enabled": False,
                "position_type": "long",
                "timeframe": s["timeframe"],
                "definition": {"demo": True, "note": "sample strategy for dashboard preview"},
                "updated_at": _iso(now_utc),
            }
        )
    return rows


def build_trade_rows(now_utc: datetime, rng: random.Random) -> list[dict[str, Any]]:
    """A believable history of CLOSED trades across the demo strategies.

    Ensures a few trades exit 'today' (IST) on weekdays so the dashboard's
    today panels are non-empty. Entry timestamps are unique per
    (strategy, instrument) because each instrument is used at most once per
    day — satisfying the trades table's unique constraint.
    """
    rows: list[dict[str, Any]] = []
    trading_days = _recent_weekdays(now_utc, 20)
    now_ist = now_utc.astimezone(IST)

    for strat in DEMO_STRATEGIES:
        step_min = 15 if strat["timeframe"] == "15m" else 60
        for day in trading_days:
            # 0–2 trades that day, on distinct instruments.
            for instrument in rng.sample(INSTRUMENTS, k=rng.randint(0, 2)):
                # Entry at a random in-session slot; exit a few candles later.
                entry_slot = rng.randint(0, 12)
                entry_ist = datetime.combine(day, time(9, 30), tzinfo=IST) + timedelta(
                    minutes=step_min * entry_slot
                )
                # Keep 'today' trades in the past (before "now").
                if day == now_ist.date() and entry_ist >= now_ist:
                    continue
                exit_ist = entry_ist + timedelta(minutes=step_min * rng.randint(1, 5))

                base = BASE_PRICE[instrument]
                entry_price = round(base * (1 + rng.uniform(-0.02, 0.02)), 2)

                reason = rng.choices(
                    ["signal", "target", "stop_loss"], weights=[0.5, 0.3, 0.2]
                )[0]
                if reason == "target":
                    move = strat["target"] / 100
                elif reason == "stop_loss":
                    move = -strat["stop"] / 100
                else:  # discretionary signal exit: small win or loss
                    move = rng.uniform(-0.9, 1.2) / 100
                exit_price = round(entry_price * (1 + move), 2)

                gross = round((exit_price - entry_price) * QTY, 4)
                rows.append(
                    {
                        "strategy_name": strat["name"],
                        "instrument": instrument,
                        "position_type": "long",
                        "quantity": QTY,
                        "entry_signal_candle_ts": _iso(entry_ist - timedelta(minutes=step_min)),
                        "entry_fill_ts": _iso(entry_ist),
                        "intended_entry_price": entry_price,
                        "entry_price": entry_price,
                        "exit_signal_candle_ts": _iso(exit_ist - timedelta(minutes=step_min)),
                        "exit_fill_ts": _iso(exit_ist),
                        "intended_exit_price": exit_price,
                        "exit_price": exit_price,
                        "exit_reason": reason,
                        "gross_pnl": gross,
                        "costs": COST_PER_TRADE,
                        "net_pnl": round(gross - COST_PER_TRADE, 4),
                    }
                )
    return rows


def build_position_rows(now_utc: datetime) -> list[dict[str, Any]]:
    """Two OPEN positions (distinct strategy+instrument pairs)."""
    picks = [(DEMO_STRATEGIES[0], "NSE:RELIANCE"), (DEMO_STRATEGIES[1], "NSE:INFY")]
    rows = []
    for i, (strat, instrument) in enumerate(picks):
        entry_ts = now_utc - timedelta(hours=2 + i)
        base = BASE_PRICE[instrument]
        entry_price = round(base, 2)
        rows.append(
            {
                "strategy_name": strat["name"],
                "instrument": instrument,
                "position_type": "long",
                "quantity": QTY,
                "entry_signal_candle_ts": _iso(entry_ts - timedelta(minutes=15)),
                "entry_fill_ts": _iso(entry_ts),
                "intended_entry_price": entry_price,
                "entry_price": entry_price,
                "stop_loss_price": round(entry_price * (1 - strat["stop"] / 100), 2),
                "target_price": round(entry_price * (1 + strat["target"] / 100), 2),
            }
        )
    return rows


def build_audit_rows(now_utc: datetime) -> list[dict[str, Any]]:
    """A short run history; the most recent is a green 'ok' paper run."""
    rows = []
    samples = [
        (0, "ok", None, {"checked": 10, "entries": 1, "exits": 0}),
        (15, "ok", None, {"checked": 10, "entries": 0, "exits": 1}),
        (30, "ok", None, {"checked": 10, "entries": 0, "exits": 0}),
        (24 * 60, "skipped", "market closed: outside run window", None),
    ]
    for mins_ago, status, extra_reason, details in samples:
        started = now_utc - timedelta(minutes=mins_ago)
        reason = f"{DEMO_AUDIT_REASON_PREFIX} sample run"
        if extra_reason:
            reason = f"{DEMO_AUDIT_REASON_PREFIX} {extra_reason}"
        rows.append(
            {
                "run_type": "paper",
                "status": status,
                "run_started_at": _iso(started),
                "run_finished_at": _iso(started + timedelta(seconds=40)),
                "candle_ts": _iso(started - timedelta(minutes=2)) if status == "ok" else None,
                "reason": reason,
                "details": {**(details or {}), "demo": True},
            }
        )
    return rows


def build_backtest_rows(now_utc: datetime, rng: random.Random) -> list[dict[str, Any]]:
    """One backtest batch: a row per demo strategy x instrument."""
    # Derive the id from the seeded RNG so build_all stays deterministic.
    batch_id = str(uuid.UUID(int=rng.getrandbits(128)))
    end = now_utc.astimezone(IST).date()
    start = end - timedelta(days=365 * 2)
    rows = []
    for strat in DEMO_STRATEGIES:
        for instrument in INSTRUMENTS:
            trades = rng.randint(18, 60)
            win_rate = round(rng.uniform(38, 62), 2)
            wins = int(round(trades * win_rate / 100))
            net = round(rng.uniform(-4000, 12000), 2)
            dd = round(rng.uniform(6, 28), 2)
            profitable_symbols = rng.randint(2, 5)
            flags = {
                "min_trades": {"required": 30, "actual": trades, "passed": trades >= 30},
                "net_positive_after_costs": {"required": "> 0", "actual": net, "passed": net > 0},
                "drawdown_within_cap": {
                    "required_max_pct": 20.0, "actual_pct": dd, "passed": dd <= 20.0
                },
                "symbol_robustness": {
                    "required_profitable_symbols": 3,
                    "actual_profitable_symbols": profitable_symbols,
                    "passed": profitable_symbols >= 3,
                },
            }
            rows.append(
                {
                    "batch_id": batch_id,
                    "strategy_name": strat["name"],
                    "instrument": instrument,
                    "timeframe": strat["timeframe"],
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "total_trades": trades,
                    "winning_trades": wins,
                    "net_pnl": net,
                    "win_rate_pct": win_rate,
                    "profit_factor": round(rng.uniform(0.6, 2.4), 4),
                    "max_drawdown_pct": dd,
                    "longest_losing_streak": rng.randint(2, 8),
                    "passed_kill_rules": all(f["passed"] for f in flags.values()),
                    "kill_rule_flags": flags,
                }
            )
    return rows


def build_all(now_utc: datetime, seed: int = 7) -> dict[str, list[dict[str, Any]]]:
    """All demo rows, keyed by table name. Deterministic for a given seed."""
    rng = random.Random(seed)
    return {
        "strategies": build_strategy_rows(now_utc),
        "trades": build_trade_rows(now_utc, rng),
        "positions": build_position_rows(now_utc),
        "run_audit": build_audit_rows(now_utc),
        "backtest_results": build_backtest_rows(now_utc, rng),
    }


# ---------------------------------------------------------------------------
# I/O (network) — kept separate from the builders above
# ---------------------------------------------------------------------------


def _connect():
    """Supabase client from the two Supabase env vars only (no Kite needed)."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    missing = [n for n, v in [("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)] if not v]
    if missing:
        print(
            "Missing environment variable(s): " + ", ".join(missing)
            + ".\nCopy .env.example to .env and fill in your Supabase URL and "
            "service_role key (Supabase → Project Settings → API). "
            "Kite keys are NOT needed for seeding.",
            file=sys.stderr,
        )
        return None
    return create_client(url, key)


def clear_demo(client) -> None:
    """Remove every DEMO- row from all tables. Safe to run repeatedly."""
    client.table("trades").delete().like("strategy_name", f"{DEMO_PREFIX}%").execute()
    client.table("positions").delete().like("strategy_name", f"{DEMO_PREFIX}%").execute()
    client.table("backtest_results").delete().like("strategy_name", f"{DEMO_PREFIX}%").execute()
    client.table("strategies").delete().like("name", f"{DEMO_PREFIX}%").execute()
    client.table("run_audit").delete().like("reason", f"{DEMO_AUDIT_REASON_PREFIX}%").execute()


def insert_all(client, data: dict[str, list[dict[str, Any]]]) -> None:
    # strategies first (nice ordering); the schema has no hard FK between
    # tables, but inserting parents first reads more sensibly.
    for table in ("strategies", "trades", "positions", "run_audit", "backtest_results"):
        rows = data[table]
        if rows:
            client.table(table).insert(rows).execute()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the dashboard with sample data.")
    parser.add_argument("--clear", action="store_true", help="remove all DEMO- data and exit")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="build the rows and print a summary WITHOUT connecting to Supabase",
    )
    args = parser.parse_args(argv)

    now_utc = datetime.now(tz=UTC)
    data = build_all(now_utc)

    if args.dry_run:
        print("DRY RUN — nothing was written. Row counts that WOULD be inserted:")
        for table, rows in data.items():
            print(f"  {table:<18} {len(rows)} rows")
        sample = data["trades"][0] if data["trades"] else {}
        print("\nSample trade row:\n" + json.dumps(sample, indent=2))
        return 0

    client = _connect()
    if client is None:
        return 1

    try:
        if args.clear:
            clear_demo(client)
            print("Removed all DEMO- data. The dashboard will show only real data now.")
            return 0

        # Re-seeding: clear first so running twice can't hit unique constraints.
        clear_demo(client)
        insert_all(client, data)
    except Exception as exc:  # supabase/postgrest raise assorted types
        print(
            f"FAILED talking to Supabase: {exc}\n"
            "Check that sql/001_init.sql was applied and your SUPABASE_URL / "
            "SUPABASE_SERVICE_ROLE_KEY are correct (run: python db.py).",
            file=sys.stderr,
        )
        return 1

    counts = ", ".join(f"{len(r)} {t}" for t, r in data.items())
    print(
        "Seeded sample data: " + counts + ".\n"
        "Open your dashboard to see it. When finished exploring, remove it with:\n"
        "    python seed_demo.py --clear"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
