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

ROOT = Path(__file__).resolve().parent.parent / "research" / "journal"


def _pct(value: float, capital: float) -> str:
    return f"{100 * (value / capital - 1):+.1f}%"


def _book(b: dict) -> None:
    st.subheader(b["title"])
    st.caption(f"Training: {b['training']}. Paper-traded from {b['start']} with Rs {b['capital']:,.0f}.")
    if b["state"] != "running":
        st.info(f"{b['state'].capitalize()}.")
        return
    left, right = st.columns(2)
    left.metric("Paper account", f"Rs {b['value']:,.0f}", _pct(b["value"], b["capital"]))
    bench = b.get("benchmark", "NIFTY 50")
    right.metric(f"Just holding {bench}", f"Rs {b['nifty_hold']:,.0f}", _pct(b["nifty_hold"], b["capital"]))
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
    momentum = ROOT / "momentum" / "paper.json"
    if momentum.exists():
        m = json.loads(momentum.read_text(encoding="utf-8"))
        st.subheader("Momentum 30 + the cash rule")
        if m["state"] == "not started":
            st.info(f"Starts {m['start']} - see the Momentum page.")
        else:
            st.metric("Paper account", f"Rs {m['value']:,.0f}", _pct(m["value"], m["capital"]))
            st.caption("Details on the Momentum page.")
    books = sorted((ROOT / "paper").glob("*.json"))
    if not books:
        empty_state("No paper books yet", "They are written by `scripts/paper_books.py`.", "📒")
        return
    for path in books:
        _book(json.loads(path.read_text(encoding="utf-8")))
    st.caption("Training figures are from history and flattered (today's stock list, few trades). "
               "Not investment advice.")
