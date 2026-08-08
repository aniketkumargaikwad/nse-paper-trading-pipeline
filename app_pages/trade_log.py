"""Trade log: every simulated round-trip, filterable and exportable."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, load_all, page_header, rupees, to_ist


def render(ctx: AppContext) -> None:
    page_header("📜 Trade log", "Every completed simulated round-trip.", ctx)

    trades = load_all(ctx)["trades"]
    if trades.empty:
        empty_state(
            "No trades recorded yet",
            "Completed round-trips appear here. Run a backtest to test ideas, "
            "or wait for a live strategy to close its first trade.",
            "📜",
        )
        return

    t = trades.copy()
    t["net_pnl"] = t["net_pnl"].astype(float)
    t["Entry (IST)"] = to_ist(t["entry_fill_ts"])
    t["Exit (IST)"] = to_ist(t["exit_fill_ts"])
    t["Held (min)"] = ((t["Exit (IST)"] - t["Entry (IST)"]).dt.total_seconds() / 60).round(0)

    # ---- filters -----------------------------------------------------------
    f1, f2, f3, f4 = st.columns(4)
    strategy = f1.selectbox("Strategy", ["(all)"] + sorted(t["strategy_name"].unique()))
    instrument = f2.selectbox("Instrument", ["(all)"] + sorted(t["instrument"].unique()))
    reason = f3.selectbox("Exit reason", ["(all)"] + sorted(t["exit_reason"].unique()))
    outcome = f4.selectbox("Outcome", ["(all)", "winners", "losers"])

    view = t
    if strategy != "(all)":
        view = view[view["strategy_name"] == strategy]
    if instrument != "(all)":
        view = view[view["instrument"] == instrument]
    if reason != "(all)":
        view = view[view["exit_reason"] == reason]
    if outcome == "winners":
        view = view[view["net_pnl"] > 0]
    elif outcome == "losers":
        view = view[view["net_pnl"] < 0]

    dates = st.date_input(
        "Date range (IST, by exit)",
        value=(t["Exit (IST)"].min().date(), t["Exit (IST)"].max().date()),
    )
    if isinstance(dates, tuple) and len(dates) == 2:
        start, end = dates
        view = view[
            (view["Exit (IST)"].dt.date >= start) & (view["Exit (IST)"].dt.date <= end)
        ]

    # ---- summary of the filtered slice --------------------------------------
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades shown", len(view))
    c2.metric("Net P&L", rupees(view["net_pnl"].sum()) if len(view) else "—")
    c3.metric(
        "Win rate",
        f"{100*(view['net_pnl'] > 0).sum()/len(view):.0f}%" if len(view) else "—",
    )
    c4.metric(
        "Avg / trade",
        rupees(view["net_pnl"].mean()) if len(view) else "—",
    )

    if view.empty:
        st.caption("No trades match these filters.")
        return

    st.dataframe(
        view.sort_values("Exit (IST)", ascending=False)[
            ["strategy_name", "instrument", "position_type", "quantity",
             "Entry (IST)", "Exit (IST)", "Held (min)",
             "intended_entry_price", "entry_price",
             "intended_exit_price", "exit_price",
             "exit_reason", "gross_pnl", "costs", "net_pnl"]
        ].rename(columns={
            "strategy_name": "Strategy", "instrument": "Instrument",
            "position_type": "Side", "quantity": "Qty",
            "intended_entry_price": "Entry (intended)", "entry_price": "Entry (filled)",
            "intended_exit_price": "Exit (intended)", "exit_price": "Exit (filled)",
            "exit_reason": "Why", "gross_pnl": "Gross ₹",
            "costs": "Costs ₹", "net_pnl": "Net ₹",
        }),
        use_container_width=True, hide_index=True, height=460,
    )
    st.caption(
        "**Intended vs filled** shows the simulated slippage: the price the "
        "signal wanted versus what the fill model actually gave."
    )

    st.download_button(
        "📥 Download filtered trades (CSV)",
        view.to_csv(index=False).encode(),
        file_name="trades.csv",
        mime="text/csv",
    )
