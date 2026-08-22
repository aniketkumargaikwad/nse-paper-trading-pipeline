"""Paper trading page: what the live simulation is doing right now."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app_common import (
    AppContext,
    empty_state,
    load_all,
    now_ist,
    page_header,
    rupees,
    to_ist,
)
from strategy.v3 import is_v3_document, parse_machine, setups_in_progress


def _v3_machines(strategies: pd.DataFrame) -> dict:
    """Parse the stored v3 documents, keyed by name.

    A strategy that no longer parses is skipped rather than raising: this
    panel is a read-only view, and one broken document must not take the
    whole page down with it.
    """
    machines: dict = {}
    if strategies is None or strategies.empty:
        return machines
    for _, row in strategies.iterrows():
        definition = row.get("definition") or {}
        if isinstance(definition, str):
            try:
                definition = json.loads(definition)
            except ValueError:
                continue
        if not is_v3_document(definition):
            continue
        try:
            machines[row["name"]] = parse_machine(definition)
        except Exception:
            continue
    return machines


def _equity_and_drawdown(trades: pd.DataFrame) -> pd.DataFrame:
    df = trades.copy()
    df["ts"] = pd.to_datetime(df["exit_fill_ts"], utc=True, format="ISO8601")
    df = df.sort_values("ts")
    equity = df["net_pnl"].astype(float).cumsum()
    out = pd.DataFrame(
        {"equity": equity.round(2).values,
         "drawdown": (equity - equity.cummax()).round(2).values},
        index=df["ts"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None),
    )
    out.index.name = "Exit time (IST)"
    return out


def render(ctx: AppContext) -> None:
    page_header(
        "📈 Paper trading",
        "Live simulated results. No real orders exist anywhere in this system.",
        ctx,
    )

    data = load_all(ctx)
    trades, positions = data["trades"], data["positions"]

    # ---- setups being hunted ------------------------------------------------
    # Nothing else records this. `positions` shows what is open and `trades`
    # what finished; neither can say that eleven symbols swept their previous
    # day's low this morning and are waiting for a confirming candle — which
    # is most of what a state machine spends its day doing. Without it a v3
    # strategy looks idle right up until it trades.
    machine_rows = data.get("machine_state")
    if machine_rows is not None and not machine_rows.empty:
        machines = _v3_machines(data["strategies"])
        setups = setups_in_progress(machine_rows.to_dict("records"), machines)
        if setups:
            st.subheader("Setups in progress")
            st.caption(
                f"{len(setups)} symbol(s) part-way through a state machine. "
                "These are not positions — nothing is open until a machine "
                "reaches an entry."
            )
            st.dataframe(
                pd.DataFrame(setups), use_container_width=True, hide_index=True,
            )
            st.divider()

    # ---- open positions ----------------------------------------------------
    st.subheader("Open positions")
    if positions.empty:
        st.caption("Flat — nothing open right now.")
    else:
        p = positions.copy()
        p["Entered (IST)"] = to_ist(p["entry_fill_ts"])
        for col in ("entry_price", "stop_loss_price", "target_price"):
            p[col] = p[col].astype(float)
        p["Risk ₹"] = ((p["entry_price"] - p["stop_loss_price"]).abs()
                       * p["quantity"].astype(int)).round(2)
        p["Reward ₹"] = ((p["target_price"] - p["entry_price"]).abs()
                         * p["quantity"].astype(int)).round(2)
        st.dataframe(
            p[["strategy_name", "instrument", "position_type", "quantity",
               "Entered (IST)", "entry_price", "stop_loss_price",
               "target_price", "Risk ₹", "Reward ₹"]]
            .rename(columns={
                "strategy_name": "Strategy", "instrument": "Instrument",
                "position_type": "Side", "quantity": "Qty",
                "entry_price": "Entry", "stop_loss_price": "Stop",
                "target_price": "Target",
            }),
            use_container_width=True, hide_index=True,
        )

    st.divider()

    if trades.empty:
        empty_state(
            "No closed trades yet",
            "Once a live strategy completes a round-trip it appears here with "
            "its equity curve. Check **Strategies** to confirm at least one is "
            "🟢 live, and **System** to confirm the engine is running.",
            "📈",
        )
        return

    t = trades.copy()
    t["net_pnl"] = t["net_pnl"].astype(float)
    t["exit_date"] = to_ist(t["exit_fill_ts"]).dt.date

    # ---- filter ------------------------------------------------------------
    names = ["(all strategies)"] + sorted(t["strategy_name"].unique())
    chosen = st.selectbox("Strategy", names)
    view = t if chosen == "(all strategies)" else t[t["strategy_name"] == chosen]

    wins = int((view["net_pnl"] > 0).sum())
    losses = int((view["net_pnl"] < 0).sum())
    gross_win = view.loc[view["net_pnl"] > 0, "net_pnl"].sum()
    gross_loss = abs(view.loc[view["net_pnl"] < 0, "net_pnl"].sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Net P&L", rupees(view["net_pnl"].sum()))
    c2.metric("Today", rupees(view.loc[view["exit_date"] == now_ist().date(), "net_pnl"].sum()))
    c3.metric("Trades", len(view))
    c4.metric("Win rate", f"{100*wins/len(view):.0f}%" if len(view) else "—")
    c5.metric("Profit factor",
              f"{gross_win/gross_loss:.2f}" if gross_loss else "∞" if gross_win else "—")

    st.caption(f"{wins} winners · {losses} losers · costs paid "
               f"{rupees(view['costs'].astype(float).sum())}")

    # ---- charts ------------------------------------------------------------
    curve = _equity_and_drawdown(view)
    left, right = st.columns(2)
    with left:
        st.caption("Cumulative net P&L (₹)")
        st.line_chart(curve["equity"], height=260)
    with right:
        st.caption("Drawdown from peak (₹)")
        st.area_chart(curve["drawdown"], height=260)

    # ---- exit reasons ------------------------------------------------------
    st.subheader("How trades ended")
    reasons = (
        view.groupby("exit_reason")
        .agg(trades=("net_pnl", "size"), net=("net_pnl", "sum"))
        .reset_index()
        .rename(columns={"exit_reason": "Exit reason", "trades": "Trades", "net": "Net ₹"})
    )
    rc1, rc2 = st.columns([1, 1])
    rc1.dataframe(reasons, use_container_width=True, hide_index=True)
    rc2.bar_chart(reasons.set_index("Exit reason")["Trades"], height=220)

    # ---- today's trades ----------------------------------------------------
    st.subheader("Today's closed trades")
    today = view[view["exit_date"] == now_ist().date()]
    if today.empty:
        st.caption("Nothing closed today (IST).")
    else:
        d = today.copy()
        d["Exit (IST)"] = to_ist(d["exit_fill_ts"])
        st.dataframe(
            d.sort_values("Exit (IST)", ascending=False)[
                ["strategy_name", "instrument", "position_type", "quantity",
                 "Exit (IST)", "entry_price", "exit_price", "exit_reason",
                 "gross_pnl", "costs", "net_pnl"]
            ].rename(columns={
                "strategy_name": "Strategy", "instrument": "Instrument",
                "position_type": "Side", "quantity": "Qty",
                "entry_price": "Entry", "exit_price": "Exit",
                "exit_reason": "Why", "gross_pnl": "Gross ₹",
                "costs": "Costs ₹", "net_pnl": "Net ₹",
            }),
            use_container_width=True, hide_index=True,
        )
