"""Momentum page: the momentum + cash-rule paper account (research/momentum_paper.py).

Reads research/journal/momentum/paper.json, which the momentum-paper workflow
rewrites every weekday evening from NSE's own index levels. No real money.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, page_header

BOOK = Path(__file__).resolve().parent.parent / "research" / "journal" / "momentum" / "paper.json"

EVIDENCE = pd.DataFrame([
    {"": "Momentum 30 + the cash rule", "2009 to Oct 2025, a year": "17.5%", "worst fall": "22.9%",
     "exam year Oct 2025 to Oct 2026": "+1.2%"},
    {"": "Just holding Momentum 30", "2009 to Oct 2025, a year": "21.1%", "worst fall": "34.0%",
     "exam year Oct 2025 to Oct 2026": "-7.9%"},
    {"": "Just holding NIFTY 50", "2009 to Oct 2025, a year": "15.0%", "worst fall": "38.3%",
     "exam year Oct 2025 to Oct 2026": "-10.5%"},
])


def render(ctx: AppContext) -> None:
    page_header("Momentum", "Paper account: NSE Nifty200 Momentum 30 with a cash rule. No real money.", ctx)
    st.markdown(
        "**The rule.** On each month's last trading day: if NIFTY 50 is at or above its 200-day "
        "average, hold the Momentum 30 index next month; otherwise hold cash (6% a year). Switches "
        "happen the next day and cost 0.2%. Fixed before the account started; it cannot be changed "
        "after the fact.")
    if not BOOK.exists():
        empty_state("No paper account yet", "It is written by `scripts/momentum_paper.py`, every "
                    "weekday evening on GitHub.", "📈")
        return
    b = json.loads(BOOK.read_text(encoding="utf-8"))
    if b["state"] == "not started":
        st.info(f"The paper account starts on {b['start']} with Rs {b['capital']:,.0f}. "
                "The first numbers appear after that day's close.")
    else:
        pct = lambda v: f"{100 * (v / b['capital'] - 1):+.1f}%"        # noqa: E731
        a, m, n = st.columns(3)
        a.metric("Paper account", f"Rs {b['value']:,.0f}", pct(b["value"]))
        m.metric("Just holding Momentum 30", f"Rs {b['momentum_hold']:,.0f}", pct(b["momentum_hold"]))
        n.metric("Just holding NIFTY 50", f"Rs {b['nifty_hold']:,.0f}", pct(b["nifty_hold"]))
        st.caption(f"As of {b['as_of']}, since {b['start']}. This month: "
                   f"{'holding the Momentum 30 index' if b['state'] == 'invested' else 'in cash'}. "
                   f"NIFTY 50 is {b['signal_now']} its 200-day average "
                   f"({b['nifty_close']:,.0f} against {b['nifty_average']:,.0f}, total-return levels).")
        if b["equity"]:
            curve = pd.DataFrame(b["equity"], columns=["day", "Paper account", "Just holding Momentum 30",
                                                       "Just holding NIFTY 50"]).set_index("day")
            st.line_chart(curve)
        if b["decisions"]:
            st.subheader("Switches")
            st.dataframe(pd.DataFrame(b["decisions"]), use_container_width=True, hide_index=True)
    st.subheader("The evidence it rests on")
    st.dataframe(EVIDENCE, use_container_width=True, hide_index=True)
    st.caption("NSE total-return indexes, dividends included, each date's real members - no hindsight. "
               "A fund tracking the index charges a fee the index does not. One exam year is one year. "
               "Full write-up: docs/research/2026-10-20-momentum-rotation.md. Not investment advice.")
