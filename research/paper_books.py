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
  index      an NSE total-return index held while NIFTY 50 is above its 200-day
             average, cash (6% a year) otherwise - the momentum account's rule

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
    index: str | None = None          # index: the NSE index name
    start: date = START               # the day this book began trading forward


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
    # Added 23 Oct 2026, at the owner's request: the one tested version that clears the pass
    # rule as loosened that day (t 1.65 exactly against the paper-traded momentum rule).
    Book("rotation-12-own-trend", "rotation", "Momentum rotation, 12 stocks above their own trend (AI, 20 Oct)",
         500_000.0,
         "2.65% a month in training, +2.9% a year over the paper-traded momentum rule on the same "
         "stocks (luck test t = 1.65), worst fall 24% - on today's stock list, so flattered by hindsight",
         variant=Variant("M-20261020-concentrated-12-1-momentum-12-stocks-own-v1", 12, 1, 12, True,
                         above_sma=200, trend_sessions=200), start=date(2026, 10, 23)),
    Book("options-banknifty-condor", "options", "BANKNIFTY same-day iron condor (options AI, 19 Oct)", 500_000.0,
         "6.0% a month in training on only 33 trades, worst day -11%; failed the -10% day rule",
         rules="options-banknifty-condor.json", underlying="BANKNIFTY"),
    # Added 21 Oct 2026 from the factor-index check (docs/research/2026-10-21-factor-indexes.md):
    # the strongest evidence found, NSE's own index, no stock-list hindsight.
    Book("index-midcap-momentum", "index", "Midcap150 Momentum 50 + the cash rule", 500_000.0,
         "23.7% a year 2009-Oct 2025 with the cash rule (27.3% just holding, luck test t = 3.9), "
         "worst fall 32%; 29.9% a year 2021-Oct 2025; -44% in 2008 even with the rule - NSE's real "
         "index, its early years back-calculated",
         index="NIFTY MIDCAP150 MOMENTUM 50"),
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


def stock_book(book: Book, ohlcv: pd.DataFrame, *, start: date | None = None, slippage_pct: float,
               cost_model: Any) -> dict[str, Any]:
    from backtest import simulate_any
    from research.account import replay
    from strategy.v3 import is_v3_document, parse_machine
    from strategy_schema import parse_strategy_dict

    start = start or book.start
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


def rotation_book(book: Book, closes: pd.DataFrame, *, start: date | None = None) -> dict[str, Any]:
    start = start or book.start
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


def options_book(book: Book, root: Path, *, start: date | None = None) -> dict[str, Any]:
    start = start or book.start
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


def index_book(book: Book, tri: pd.DataFrame, *, start: date | None = None) -> dict[str, Any]:
    """`book.index` while NIFTY 50 is above its 200-day average, cash at 6% otherwise (NSE TRI levels)."""
    start = start or book.start
    from research.rotation import index_switch
    nifty = tri["NIFTY 50"].dropna()
    sessions = sorted(set(nifty.index) & set(tri[book.index].dropna().index))
    if not sessions or sessions[-1] < start:
        return _empty(book, start, sessions[-1] if sessions else None, "not started")
    result = index_switch(tri[book.index], nifty, start=start, end=sessions[-1], cash_yield=0.06,
                          cost_pct=ROTATION_COST_PCT, capital=book.capital)
    hold, curve = _nifty_hold(nifty, start, book.capital)
    held = [r for r in result.rebalances if r.filled <= sessions[-1]]
    invested = bool(held and held[-1].holdings)
    return {**_empty(book, start, sessions[-1], "running"), "complete_through": sessions[-1].isoformat(),
            "value": round(float(result.equity.iloc[-1]), 2), "nifty_hold": hold,
            "equity": [[d.isoformat(), round(v, 2), round(float(curve.get(d, float("nan"))), 2)]
                       for d, v in result.equity.items()],
            "open": [{"holding": book.index if invested else "cash (6% a year)"}],
            "closed": [{"decided": r.decided.isoformat(), "filled": r.filled.isoformat(),
                        "action": "buy the index" if r.holdings else "sell to cash"} for r in held]}


def summary_line(b: Mapping[str, Any]) -> str:
    if b["state"] != "running":
        return f"{b['title']}: {b['state']}"
    pct = lambda v: f"{100 * (v / b['capital'] - 1):+.1f}%"     # noqa: E731
    card = scorecard(b)
    return (f"{b['title']}: {pct(b['value'])} (holding {b.get('benchmark', 'NIFTY 50')}: {pct(b['nifty_hold'])}), "
            f"worst fall {card['worst_fall_pct']:.1f}% - {card['verdict'].upper()}: {card['detail']}")


def message(books: Sequence[Mapping[str, Any]], as_of: date) -> list:
    """Blocks in the morning message's layout (research.blocks): one per book."""
    from research.blocks import Block
    blocks = [Block(f"\U0001f4d2 Paper books — {as_of.isoformat()}",
                    note="No real money. Each book trades its tested rules forward from the day it started; "
                         f"first review {REVIEW_6M}, final review {REVIEW_12M} (docs/TRACKING.md).")]
    for b in books:
        if b["state"] != "running":
            blocks.append(Block(b["title"], note=b["state"].capitalize() + "."))
            continue
        card = scorecard(b)
        pct = lambda v: f"{100 * (v / b['capital'] - 1):+.1f}%"     # noqa: E731
        rows = [("paper account", pct(b["value"])),
                (f"hold {b.get('benchmark', 'NIFTY 50')}"[:14], pct(b["nifty_hold"])),
                ("worst fall", f"{card['worst_fall_pct']:.1f}%")]
        if card["closed_trades"] is not None:
            rows.append(("closed trades", str(card["closed_trades"])))
        rows.append(("review", card["verdict"].upper()))
        blocks.append(Block(b["title"], tuple(rows), f"{card['detail']}. Complete through {b['complete_through']}."))
    return blocks


# --- the review rule, fixed on 21 Oct 2026 before any book had a single day -----------------
#
# Written down in advance so the conclusion cannot be bent to the result (docs/TRACKING.md):
# - STOP at any time if the paper account falls more than 30% from its high.
# - At 6 months (21 Apr 2027): DROP if more than 5 percentage points behind just holding
#   its benchmark from the same day. Books that trade stock by stock or options need 20
#   closed trades before a 6-month verdict; with fewer, wait for 12 months.
# - At 12 months (21 Oct 2027): PASSED if ahead of its benchmark with a worst fall within
#   30%; otherwise FAILED. Only a passed book is worth discussing with real money - and that
#   decision is the owner's, never the tool's.
STOP_FALL_PCT = 30.0
DROP_BEHIND_PTS = 5.0
MIN_TRADES = 20
REVIEW_6M = date(2027, 4, 21)
REVIEW_12M = date(2027, 10, 21)
COUNTS_TRADES = ("stock", "options")


def scorecard(b: Mapping[str, Any], *, today: date | None = None) -> dict[str, Any]:
    """Where a book stands against the review rule above."""
    if b.get("state") != "running" or not b.get("equity"):
        return {"verdict": "not started", "detail": b.get("state", "")}
    values = [row[1] for row in b["equity"]]
    peak, fall = values[0], 0.0
    for v in values:
        peak = max(peak, v)
        fall = max(fall, 100 * (1 - v / peak))
    mine = 100 * (b["value"] / b["capital"] - 1)
    bench = 100 * (b["nifty_hold"] / b["capital"] - 1)
    lead = mine - bench
    trades = len(b.get("closed", [])) if b.get("kind") in COUNTS_TRADES else None
    day = today or date.fromisoformat(b["complete_through"])
    card = {"return_pct": round(mine, 2), "benchmark_pct": round(bench, 2), "lead_pts": round(lead, 2),
            "worst_fall_pct": round(fall, 1), "closed_trades": trades,
            "months": round((day - date.fromisoformat(b["start"])).days / 30.44, 1)}
    if fall > STOP_FALL_PCT:
        return {**card, "verdict": "STOP", "detail": f"fell {fall:.1f}% from its high (limit {STOP_FALL_PCT:.0f}%)"}
    if day >= REVIEW_12M:
        ok = lead > 0
        return {**card, "verdict": "PASSED" if ok else "FAILED",
                "detail": f"12-month review: {lead:+.1f} points against its benchmark"}
    if day >= REVIEW_6M:
        if trades is not None and trades < MIN_TRADES:
            return {**card, "verdict": "waiting", "detail": f"only {trades} closed trades: judged at 12 months"}
        if lead < -DROP_BEHIND_PTS:
            return {**card, "verdict": "DROP", "detail": f"{lead:+.1f} points behind its benchmark at 6 months"}
        return {**card, "verdict": "on track", "detail": f"6-month review passed: {lead:+.1f} points; final review {REVIEW_12M}"}
    return {**card, "verdict": "too early", "detail": f"{lead:+.1f} points against its benchmark so far; "
                                                      f"first review {REVIEW_6M}"}


def from_momentum(m: Mapping[str, Any]) -> dict[str, Any]:
    """The momentum account's own file (research/momentum_paper.py) in a book's shape."""
    running = m.get("state") in ("invested", "cash")
    return {"key": "momentum-30", "kind": "index", "title": "Momentum 30 + the cash rule",
            "training": "17.5% a year 2009-Oct 2025, worst fall 23%; NSE's real index",
            "start": m["start"], "as_of": m.get("as_of"), "complete_through": m.get("as_of") if running else None,
            "capital": m["capital"], "value": m["value"], "nifty_hold": m["nifty_hold"],
            "state": "running" if running else m.get("state", "not started"),
            "equity": [[row[0], row[1]] for row in m.get("equity", [])], "open": [], "closed": []}
