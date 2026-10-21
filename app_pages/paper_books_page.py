"""Paper books page: every strategy being paper-traded forward (research/paper_books.py). No real money.

Reads research/journal/paper/*.json, rewritten every weekday evening by the
paper-books workflow, and the momentum account's own file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, page_header
from research.paper_books import (DROP_BEHIND_PTS, MIN_TRADES, REVIEW_6M, REVIEW_12M, STOP_FALL_PCT,
                                  from_momentum, scorecard)

ROOT = Path(__file__).resolve().parent.parent / "research" / "journal"


def _pct(value: float, capital: float) -> str:
    return f"{100 * (value / capital - 1):+.1f}%"


def _book(b: dict) -> None:
    st.subheader(b["title"])
    st.caption(f"Training: {b['training']}. Paper-traded from {b['start']} with Rs {b['capital']:,.0f}.")
    if b["state"] != "running":
        st.info(f"{b['state'].capitalize()}.")
        return
    card = scorecard(b)
    left, right, verdict = st.columns(3)
    left.metric("Paper account", f"Rs {b['value']:,.0f}", _pct(b["value"], b["capital"]))
    bench = b.get("benchmark", "NIFTY 50")
    right.metric(f"Just holding {bench}", f"Rs {b['nifty_hold']:,.0f}", _pct(b["nifty_hold"], b["capital"]))
    verdict.metric("Review", card["verdict"].upper(), help=card["detail"])
    st.caption(f"{card['detail']}. Worst fall so far {card['worst_fall_pct']:.1f}%"
               + (f", {card['closed_trades']} closed trades" if card["closed_trades"] is not None else "") + ".")
    st.caption(f"Complete through {b['complete_through']}"
               + (" (option minutes arrive after each expiry)" if b["kind"] == "options" else "")
               + (". A stock trade counts when it closes; the latest point includes open positions at "
                  "today's close." if b["kind"] == "stock" else "."))
    if b["equity"]:
        curve = pd.DataFrame(b["equity"], columns=["day", "Paper account", f"Just holding {bench}"]).set_index("day")
        st.line_chart(curve)
    if b["open"]:
        with st.expander(f"Holding now ({len(b['open'])})"):
            st.dataframe(pd.DataFrame(b["open"]), use_container_width=True, hide_index=True)
    if b["closed"]:
        with st.expander(f"Recent trades ({len(b['closed'])})"):
            st.dataframe(pd.DataFrame(b["closed"]), use_container_width=True, hide_index=True)


def render(ctx: AppContext) -> None:
    page_header("Paper books", "Every strategy being paper-traded forward from a fixed day. No real money.", ctx)
    st.markdown(
        "Each book replays its strategy's exact tested rules on fresh prices every weekday evening, "
        "from the day it started. Nothing before that day counts, so there is no hindsight in these "
        "numbers - this is the honest test of the training results.")
    st.markdown(
        f"**How each book is judged - fixed on 21 Oct 2026, before any book had a day.** "
        f"STOP at any time if it falls more than {STOP_FALL_PCT:.0f}% from its high. "
        f"At 6 months ({REVIEW_6M}): DROP if more than {DROP_BEHIND_PTS:.0f} points behind just holding "
        f"its benchmark (stock and options books need {MIN_TRADES} closed trades first). "
        f"At 12 months ({REVIEW_12M}): PASSED if ahead of its benchmark with a worst fall within "
        f"{STOP_FALL_PCT:.0f}%. Full plan: docs/TRACKING.md.")
    books = []
    momentum = ROOT / "momentum" / "paper.json"
    if momentum.exists():
        books.append(from_momentum(json.loads(momentum.read_text(encoding="utf-8"))))
    books += [json.loads(p.read_text(encoding="utf-8")) for p in sorted((ROOT / "paper").glob("*.json"))]
    if not books:
        empty_state("No paper books yet", "They are written by `scripts/paper_books.py`.", "📒")
        return
    table = []
    for b in books:
        card = scorecard(b)
        table.append({"Book": b["title"], "Return": (f"{card['return_pct']:+.1f}%" if "return_pct" in card else "-"),
                      "Benchmark": (f"{card['benchmark_pct']:+.1f}%" if "benchmark_pct" in card else "-"),
                      "Worst fall": (f"{card['worst_fall_pct']:.1f}%" if "worst_fall_pct" in card else "-"),
                      "Review": card["verdict"].upper()})
    st.dataframe(pd.DataFrame(table), use_container_width=True, hide_index=True)
    for b in books:
        _book(b)
    st.caption("Training figures are from history and flattered (today's stock list, few trades). "
               "Not investment advice.")
