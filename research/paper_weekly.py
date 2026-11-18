"""The weekly paper-trading message: every paper book in plain words. NO real money.

Asked for on 18 Nov 2026: once a week, every strategy in paper trading in its
own box, beginner-friendly, its own Telegram bot, led by the average return a
month and the return since it was added, with prices. For each book:

  added on          the day it started trading forward
  money             what it started with and what it is worth now
  since added       return since that day
  avg a month       the average of its calendar months since it was added -
                    e.g. 3%, 5% and 1% read as 3%. The first month counts
                    from the day it was added and the current month so far.
  NIFTY 50 same     what just holding the index would have made, same days
  win rate          of its closed trades, how many made money
  profit factor     money made by winners / money lost by losers
  reward : risk     the average winner / the average loser
  trades a month    closed trades per month since it was added
  worst fall        the deepest drop from a high, since it was added
  holding now       what it holds, at the price it paid and today's

A "trade" depends on the book (research.paper_books.trip): a stock bought and
sold, an option position, a stock held by a rotation from one month-end to the
one that sold it, or an index held between two switches. Books that switch
monthly close few trades, so their trade figures stay "too few" for months -
said so, never dressed up.

🟢 / 🔴 on each book: its review verdict once it has one (paper_books.scorecard);
before that, whether it is ahead of or behind NIFTY 50 so far.

Pure: no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from research import paper_books as pb
from research.blocks import Block, mark

MIN_TRADES_TO_JUDGE = 5           # below this, win rate etc. are shown with "too few to judge"
MONTH_DAYS = 30.44
GOOD = ("PASSED", "on track")
BAD = ("STOP", "DROP", "FAILED")


def rupees(v: float) -> str:
    """Indian grouping: 1051918 -> 'Rs 10,51,918'."""
    n = f"{abs(round(v)):d}"
    head, tail = n[:-3], n[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if v < 0 else "") + "Rs " + ",".join([*groups, tail])


def price(v: float) -> str:
    return f"{v:,.2f}" if v < 10_000 else f"{v:,.0f}"


def monthly_returns(b: Mapping[str, Any]) -> list[tuple[str, float]]:
    """(YYYY-MM, % return) for each calendar month since the book started, current month so far."""
    ends: dict[str, float] = {}
    for row in b.get("equity", []):
        ends[row[0][:7]] = row[1]
    out, prev = [], b["capital"]
    for month in sorted(ends):
        out.append((month, 100 * (ends[month] / prev - 1)))
        prev = ends[month]
    return out


def numbers(b: Mapping[str, Any], today: date | None = None) -> dict[str, Any]:
    """Every figure the weekly message shows for one running book."""
    card = pb.scorecard(b)
    months = monthly_returns(b)
    trips = b.get("round_trips") or []
    closed = [t for t in trips if t.get("closed")]
    wins = [t["pnl"] for t in closed if t["pnl"] > 0]
    losses = [-t["pnl"] for t in closed if t["pnl"] <= 0]
    start = date.fromisoformat(b["start"])
    end = today or date.fromisoformat(b["complete_through"])
    elapsed = max((end - start).days, 1) / MONTH_DAYS
    return {
        "since_pct": 100 * (b["value"] / b["capital"] - 1),
        "bench_pct": 100 * (b["nifty_hold"] / b["capital"] - 1),
        "avg_month_pct": sum(r for _, r in months) / len(months) if months else None,
        "months": months,
        "closed": len(closed), "wins": len(wins),
        "win_rate_pct": 100 * len(wins) / len(closed) if closed else None,
        "profit_factor": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else None,
        "reward_risk": ((sum(wins) / len(wins)) / (sum(losses) / len(losses)))
        if wins and losses and sum(losses) > 0 else None,
        "trades_per_month": len(closed) / elapsed,
        "worst_fall_pct": card["worst_fall_pct"],
        "verdict": card["verdict"], "detail": card["detail"],
        "open": [t for t in trips if not t.get("closed")],
    }


def status_ok(n: Mapping[str, Any]) -> bool:
    if n["verdict"] in GOOD:
        return True
    if n["verdict"] in BAD:
        return False
    return n["since_pct"] >= n["bench_pct"]


def _when(iso: str) -> str:
    return date.fromisoformat(iso).strftime("%d %b %Y")


def _month_name(ym: str) -> str:
    return date.fromisoformat(ym + "-01").strftime("%b")


def book_block(b: Mapping[str, Any], as_of: date | None = None) -> Block:
    """One book in its own box."""
    if b.get("state") != "running" or not b.get("equity"):
        why = (b.get("state") or "not started").rstrip(".")
        return Block(f"{mark(None)} {b['title']}", (("added on", _when(b["start"])),),
                     f"Not trading yet: {why}.")
    n = numbers(b)
    bench = b.get("benchmark", "NIFTY 50")
    avg = n["avg_month_pct"]
    months_said = ", ".join(f"{_month_name(m)} {r:+.1f}%" for m, r in n["months"])
    few = n["closed"] < MIN_TRADES_TO_JUDGE
    if n["closed"] == 0:
        win = pf = rr = "no trade closed yet"
    else:
        win = f"{n['win_rate_pct']:.0f}% ({n['wins']} of {n['closed']})"
        pf = ("no losing trade" if n["profit_factor"] is None and n["wins"] else
              "no winning trade" if not n["wins"] else f"{n['profit_factor']:.2f}")
        rr = "n/a" if n["reward_risk"] is None else f"{n['reward_risk']:.2f}"
    rows = [("added on", _when(b["start"])),
            ("started with", rupees(b["capital"])),
            ("worth now", rupees(b["value"])),
            ("since added", f"{n['since_pct']:+.2f}%"),
            ("avg a month", "n/a" if avg is None else f"{avg:+.2f}% ({len(n['months'])} mo)"),
            (f"{bench} same"[:14], f"{n['bench_pct']:+.2f}%"),
            ("win rate", win), ("profit factor", pf), ("reward : risk", rr),
            ("trades a month", f"{n['trades_per_month']:.1f}"),
            ("worst fall", f"{n['worst_fall_pct']:.1f}%"),
            ("review", n["verdict"].upper())]
    holding = [t for t in n["open"] if "bought_at" in t]
    short = [t for t in holding if len(t["what"]) < 15]
    if short:
        rows.append(("holding now", "bought -> today"))
        rows += [(t["what"], f"{price(t['bought_at'])} -> {price(t['last'])} {t['return_pct']:+.1f}%") for t in short]
    notes = [f"Months: {months_said} (the first from the day it was added, the last so far)."]
    long_names = [t for t in holding if len(t["what"]) >= 15]
    if long_names:
        notes.append("Holding now: " + "; ".join(
            f"{t['what']} bought at {price(t['bought_at'])}, today {price(t['last'])} ({t['return_pct']:+.1f}%)"
            for t in long_names) + ".")
    elif not n["open"] and b.get("kind") in ("index", "alloc", "rotation"):
        notes.append("Holding now: cash.")
    if 0 < n["closed"] < MIN_TRADES_TO_JUDGE:
        notes.append(f"Only {n['closed']} closed trade(s): too few to judge win rate or profit factor yet.")
    through = date.fromisoformat(b["complete_through"])
    if as_of is not None and through < as_of:
        notes.append(f"Its figures run to {through:%d %b} only"
                     + (": option minute prices arrive after each expiry." if b.get("kind") == "options" else "."))
    notes.append(f"Review: {n['detail']}.")
    if few and b.get("kind") in ("index", "alloc", "rotation"):
        notes.append("It switches once a month or less, so it closes few trades by design.")
    return Block(f"{mark(status_ok(n))} {b['title']}", tuple(rows), " ".join(notes))


GLOSSARY = ("Win rate: of the trades it closed, the share that made money. "
            "Profit factor: money made by winning trades divided by money lost by losing ones - "
            "above 1 means it made money overall. "
            "Reward : risk: the average winning trade divided by the average losing one. "
            "Worst fall: the biggest drop from its own high. "
            "🟢 = ahead of NIFTY 50 so far (or passed its review); 🔴 = behind (or failed). "
            "Prices: stocks are adjusted for dividends; index books show the index level.")


def message(books: Sequence[Mapping[str, Any]], as_of: date, *, price_check: str = "") -> list[Block]:
    """Blocks: a short summary first, then one box per book, then what the words mean."""
    running = [b for b in books if b.get("state") == "running" and b.get("equity")]
    scored = [(b, numbers(b)) for b in running]
    ahead = sum(1 for _, n in scored if n["since_pct"] >= n["bench_pct"])
    best = max(scored, key=lambda p: p[1]["since_pct"], default=None)
    summary = [("books running", f"{len(running)} of {len(books)}"),
               ("ahead of NIFTY", f"{ahead} of {len(running)}")]
    if best:
        summary.append(("best so far", f"{best[1]['since_pct']:+.1f}% since added"))
    note = ("No real money: each strategy trades its tested rules forward on real NSE prices "
            "from the day it was added.")
    if best:
        note += f" Best so far: {best[0]['title']}."
    if price_check:
        note += " " + price_check
    blocks = [Block(f"\U0001f4d2 Paper trading, week to {as_of:%d %b %Y}", tuple(summary), note)]
    order = sorted(books, key=lambda b: (b.get("state") != "running", b["start"], b["title"]))
    blocks += [book_block(b, as_of) for b in order]
    blocks.append(Block("What the words mean", note=GLOSSARY))
    return blocks


def week_of(day: date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def due(today: date, sent_for: str) -> bool:
    """Friday's evening run sends it, once per week."""
    return today.weekday() == 4 and sent_for != week_of(today)
