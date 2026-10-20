"""Paper books: the strategies the owner chose (20 Oct 2026), replayed forward. NO real money.

Every strategy that averaged more than 3% a month on its training years goes
here, as the owner asked - not because those numbers are believed (each is
flattered: hindsight in the stock list, few trades, or both), but because
trading them forward from a fixed day is the test that settles it. From the
start date on nothing is hindsight: the stock list is today's and the future
is unknown to it.

Each book is recomputed from START on every run, from fresh prices, with the
same code that tested it, so it can never drift from what was tested and
nothing in it can be edited after the fact:

  stock      the research's own simulator on daily candles, 200 stocks, then
             the 10-slot account the research judged (research.account)
  rotation   research.rotation.run, a fixed variant
  options    fno.intraday.run on the minute store; option minutes arrive only
             after each expiry, so the book says how far it is complete

The strategies' rules are frozen in research/paper/*.json.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from config import IST, UTC
from research.rotation import INDEX, Variant
from research.rotation import run as rotation_run

START = date(2026, 10, 21)
RULES = Path(__file__).resolve().parent / "paper"
OUT = Path("research/journal/paper")
WARMUP_SESSIONS = 420        # history fed before START so 250-session indicators exist
SLOTS, NOTIONAL = 10, 100_000.0
ROTATION_COST_PCT = 0.2


@dataclass(frozen=True)
class Book:
    key: str
    kind: str                # stock | rotation | options
    title: str
    capital: float
    training: str            # what it showed in training, in words, with the caveat
    rules: str | None = None          # file in research/paper/ (stock, options)
    variant: Variant | None = None    # rotation
    underlying: str | None = None     # options


BOOKS = (
    Book("stock-leader", "stock", "Leader buy-and-hold (stock research, 20 Sep)", SLOTS * NOTIONAL,
         "4.4% a month in training as a 10-slot account, 2009-2025 - on today's stock list, "
         "so flattered by hindsight; rejected then for trading 1.4 times a month",
         rules="stock-leader-buy-and-hold.json"),
    Book("rotation-r4", "rotation", "Momentum rotation, 10 stocks (R4)", 500_000.0,
         "3.5% a month in training, worst fall 43% - on today's stock list, about 1% a month of it hindsight",
         variant=Variant("R4", 12, 1, 10, False)),
    Book("rotation-r1", "rotation", "Momentum rotation, 20 stocks (R1)", 500_000.0,
         "3.1% a month in training, worst fall 43% - on today's stock list, about 1% a month of it hindsight",
         variant=Variant("R1", 12, 1, 20, False)),
    Book("options-banknifty-condor", "options", "BANKNIFTY same-day iron condor (options AI, 19 Oct)", 500_000.0,
         "6.0% a month in training on only 33 trades, worst day -11%; failed the -10% day rule",
         rules="options-banknifty-condor.json", underlying="BANKNIFTY"),
)


def rules(book: Book) -> dict[str, Any]:
    return json.loads((RULES / book.rules).read_text(encoding="utf-8"))


def frames(ohlcv: pd.DataFrame, *, until: date | None = None) -> dict[str, pd.DataFrame]:
    """Per stock: candles as the simulator reads them - IST midnight stamps in UTC, lower-case fields."""
    out = {}
    stamps = pd.DatetimeIndex([datetime.combine(d, time(0), IST) for d in ohlcv.index]).tz_convert(UTC)
    for ticker in sorted({t for t, _ in ohlcv.columns}):
        if ticker.startswith("^"):
            continue
        f = ohlcv[ticker].copy()
        f.index = stamps
        f.columns = [c.lower() for c in f.columns]
        f = f.dropna(subset=["open", "high", "low", "close"])
        f["volume"] = f["volume"].fillna(0.0)
        if until is not None:
            f = f[[d.astimezone(IST).date() <= until for d in f.index]]
        out[ticker.removesuffix(".NS")] = f
    return out


def _empty(book: Book, start: date, as_of: date | None, why: str) -> dict[str, Any]:
    return {"key": book.key, "kind": book.kind, "title": book.title, "training": book.training,
            "start": start.isoformat(), "as_of": as_of.isoformat() if as_of else None,
            "complete_through": None, "capital": book.capital, "value": book.capital,
            "nifty_hold": book.capital, "state": why, "equity": [], "open": [], "closed": []}


def _nifty_hold(nifty: pd.Series, start: date, capital: float) -> tuple[float, pd.Series]:
    s = nifty.dropna()
    s = s[s.index >= start]
    curve = capital * s / s.iloc[0]
    return round(float(curve.iloc[-1]), 2), curve


def stock_book(book: Book, ohlcv: pd.DataFrame, *, start: date = START, slippage_pct: float,
               cost_model: Any) -> dict[str, Any]:
    from backtest import simulate_any
    from research.account import replay
    from strategy.v3 import is_v3_document, parse_machine
    from strategy_schema import parse_strategy_dict

    nifty = ohlcv["^NSEI"]["Close"]
    sessions = sorted(nifty.dropna().index)
    if not sessions or sessions[-1] < start:
        return _empty(book, start, sessions[-1] if sessions else None, "not started")
    doc = dict(rules(book)["document"])
    doc["timeframe"] = "day"           # the reading being paper-traded was the daily account
    strategy = parse_machine(doc) if is_v3_document(doc) else parse_strategy_dict(doc, where="paper")
    first = sessions[max(0, sessions.index(next(d for d in sessions if d >= start)) - WARMUP_SESSIONS)]
    window = ohlcv.loc[ohlcv.index >= first]
    owner: dict[int, str] = {}
    trades = []
    for symbol, frame in frames(window).items():
        if len(frame) < 2:
            continue
        for t in simulate_any(frame, strategy, slippage_pct=slippage_pct, cost_per_trade_inr=0.0,
                              cost_model=cost_model).trades:
            if t.entry_fill_ts.astimezone(IST).date() >= start:
                owner[id(t)] = symbol
                trades.append(t)
    taken = replay(trades, slots=SLOTS).taken
    end = sessions[-1]
    closed = [t for t in taken if t.exit_reason != "end_of_data"]
    still = [t for t in taken if t.exit_reason == "end_of_data"]
    realised = pd.Series({t.exit_fill_ts.astimezone(IST).date(): 0.0 for t in closed}, dtype=float)
    for t in closed:
        realised[t.exit_fill_ts.astimezone(IST).date()] += t.net_pnl
    days = [d for d in sessions if d >= start]
    cum = realised.reindex(days).fillna(0.0).cumsum()
    value = book.capital + float(cum.iloc[-1]) + sum(t.net_pnl for t in still)
    hold, curve = _nifty_hold(nifty, start, book.capital)
    equity = [[d.isoformat(), round(book.capital + float(cum[d]) + (sum(t.net_pnl for t in still) if d == end else 0), 2),
               round(float(curve.get(d, float("nan"))), 2)] for d in days]
    row = lambda t: {"stock": owner[id(t)], "entered": t.entry_fill_ts.astimezone(IST).date().isoformat(),   # noqa: E731
                     "entry_price": round(t.entry_price, 2), "quantity": t.quantity,
                     "exit": t.exit_fill_ts.astimezone(IST).date().isoformat(),
                     "exit_price": round(t.exit_price, 2), "pnl": round(t.net_pnl, 2), "why": t.exit_reason}
    return {**_empty(book, start, end, "running"), "complete_through": end.isoformat(),
            "value": round(value, 2), "nifty_hold": hold, "equity": equity,
            "open": [{k: v for k, v in row(t).items() if k not in ("exit", "why")} | {"pnl_if_sold_now": row(t)["pnl"]}
                     for t in still],
            "closed": [row(t) for t in closed][-50:]}


def rotation_book(book: Book, closes: pd.DataFrame, *, start: date = START) -> dict[str, Any]:
    nifty = closes[INDEX]
    sessions = sorted(nifty.dropna().index)
    if not sessions or sessions[-1] < start:
        return _empty(book, start, sessions[-1] if sessions else None, "not started")
    result = rotation_run(closes, book.variant, start=start, end=sessions[-1], cost_pct=ROTATION_COST_PCT,
                          capital=book.capital)
    hold, curve = _nifty_hold(nifty, start, book.capital)
    last = result.rebalances[-1] if result.rebalances else None
    return {**_empty(book, start, sessions[-1], "running"), "complete_through": sessions[-1].isoformat(),
            "value": round(float(result.equity.iloc[-1]), 2), "nifty_hold": hold,
            "equity": [[d.isoformat(), round(v, 2), round(float(curve.get(d, float("nan"))), 2)]
                       for d, v in result.equity.items()],
            "open": [{"stock": s.removesuffix(".NS"), "since": last.filled.isoformat()} for s in last.holdings]
            if last else [],
            "closed": [{"decided": r.decided.isoformat(), "filled": r.filled.isoformat(),
                        "holdings": len(r.holdings), "cost": round(r.cost, 2)} for r in result.rebalances][-24:],
            "state": "running" if last else "waiting for the first month-end"}


def options_book(book: Book, root: Path, *, start: date = START) -> dict[str, Any]:
    from fno import intraday, minute
    from fno.intraday_market import IntradayMarket
    from fno.strategy import parse
    import yaml

    ledger = minute.load_ledger(root)
    fetched = sorted(e for (u, e), status in ledger.items() if u == book.underlying and status == "ok")
    market = IntradayMarket.load(root, book.underlying)
    sessions = market.daily.sessions_all
    through = min(sessions[-1], fetched[-1]) if fetched and sessions else None
    if through is None or through < start:
        return _empty(book, start, sessions[-1] if sessions else None,
                      f"waiting for option minutes: they arrive after each {book.underlying} expiry")
    doc = {**rules(book)["document"], "underlying": book.underlying}
    result = intraday.run(parse(yaml.safe_dump(doc, sort_keys=False)), market, start=start, end=through,
                          capital=book.capital)
    closes = market.daily.closes()
    nifty = pd.Series(closes)          # holding the index itself, on the same rupees
    hold, curve = _nifty_hold(nifty, start, book.capital)
    eq = result.equity.sort_index()
    return {**_empty(book, start, sessions[-1], "running"), "complete_through": through.isoformat(),
            "value": round(float(eq.iloc[-1]), 2), "nifty_hold": hold, "benchmark": book.underlying,
            "equity": [[d.isoformat(), round(v, 2), round(float(curve.get(d, float("nan"))), 2)] for d, v in eq.items()],
            "closed": [{"day": t.entered.isoformat(), "pnl": round(t.pnl, 2), "why": t.exit_reason}
                       for t in result.trades][-50:],
            "skipped": {k: sum(1 for s in result.skips if s.kind == k) for k in {s.kind for s in result.skips}}}


def summary_line(b: Mapping[str, Any]) -> str:
    if b["state"] != "running":
        return f"{b['title']}: {b['state']}"
    pct = lambda v: f"{100 * (v / b['capital'] - 1):+.1f}%"     # noqa: E731
    return (f"{b['title']}: {pct(b['value'])} (holding {b.get('benchmark', 'NIFTY 50')}: {pct(b['nifty_hold'])}), "
            f"complete through {b['complete_through']}")


def message(books: Sequence[Mapping[str, Any]], as_of: date) -> str:
    return "\n".join([f"Paper books, {as_of.isoformat()} (no real money)",
                      *[f"- {summary_line(b)}" for b in books]])
