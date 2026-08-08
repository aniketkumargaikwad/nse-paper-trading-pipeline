"""Overview page: is the system healthy, and what should I do next?"""

from __future__ import annotations

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


def render(ctx: AppContext) -> None:
    page_header(
        "📊 Overview",
        "Simulation only — no real orders are placed anywhere in this system.",
        ctx,
    )

    data = load_all(ctx)
    trades, positions = data["trades"], data["positions"]
    audits, strategies = data["run_audit"], data["strategies"]

    # ---- engine health -----------------------------------------------------
    st.subheader("Engine health")
    if audits.empty:
        st.warning(
            "⚪ **The engine has never run.** Enable the schedule "
            "(`gh workflow enable paper-engine`) or trigger it once from the "
            "repo's Actions tab."
        )
    else:
        last = audits.iloc[0]
        started = to_ist(pd.Series([last["run_started_at"]])).iloc[0]
        status = str(last["status"])
        age_min = (now_ist().replace(tzinfo=None) - started).total_seconds() / 60
        badge = {"ok": "🟢", "skipped": "🟡", "error": "🔴"}.get(status, "⚪")
        reason = f" — {last['reason']}" if last.get("reason") else ""
        msg = (
            f"{badge} Last run **{status}**{reason} · "
            f"{started.strftime('%d %b %H:%M')} IST ({age_min:.0f} min ago)"
        )
        (st.error if status == "error" else st.info)(msg)
        if status == "error":
            st.caption(
                "Check the repo's Actions tab for the log. Common cause: the "
                "two Supabase secrets are missing."
            )

    # ---- headline numbers --------------------------------------------------
    st.subheader("Performance")
    if trades.empty:
        total = today = 0.0
        n_trades = wins = 0
    else:
        t = trades.copy()
        t["net_pnl"] = t["net_pnl"].astype(float)
        t["exit_date"] = to_ist(t["exit_fill_ts"]).dt.date
        total = float(t["net_pnl"].sum())
        today = float(t.loc[t["exit_date"] == now_ist().date(), "net_pnl"].sum())
        n_trades = len(t)
        wins = int((t["net_pnl"] > 0).sum())

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Net P&L", rupees(total), delta=None if total == 0 else f"{total:+,.0f}")
    c2.metric("Today", rupees(today), delta=None if today == 0 else f"{today:+,.0f}")
    c3.metric("Closed trades", n_trades)
    c4.metric("Win rate", f"{(100*wins/n_trades):.0f}%" if n_trades else "—")
    c5.metric("Open now", len(positions))

    st.divider()

    left, right = st.columns(2)

    # ---- equity curve ------------------------------------------------------
    with left:
        st.subheader("Equity curve (all strategies)")
        if trades.empty:
            empty_state(
                "No closed trades yet",
                "The curve appears once the engine completes its first "
                "round-trip. Until then, use **Backtest** to test ideas on "
                "historical data.",
                "📈",
            )
        else:
            t = trades.copy()
            t["ts"] = pd.to_datetime(t["exit_fill_ts"], utc=True, format="ISO8601")
            t = t.sort_values("ts")
            curve = pd.DataFrame(
                {"equity": t["net_pnl"].astype(float).cumsum().values},
                index=t["ts"].dt.tz_convert("Asia/Kolkata").dt.tz_localize(None),
            )
            st.line_chart(curve, height=260)

    # ---- strategy status ---------------------------------------------------
    with right:
        st.subheader("Strategies")
        if strategies.empty:
            empty_state(
                "No strategies stored",
                "Go to **Strategies** to create one, or run the engine once "
                "to seed the defaults from `strategies.yaml`.",
                "🧠",
            )
        else:
            s = strategies.copy()
            live = int(s["enabled"].sum())
            st.metric("Live now", f"{live} of {len(s)}")
            show = s[["name", "enabled", "timeframe", "position_type"]].rename(
                columns={
                    "name": "Strategy", "enabled": "Live",
                    "timeframe": "TF", "position_type": "Side",
                }
            )
            st.dataframe(show, use_container_width=True, hide_index=True, height=200)

    # ---- open positions ----------------------------------------------------
    st.subheader("Open positions")
    if positions.empty:
        st.caption("Flat — no simulated positions are open.")
    else:
        p = positions.copy()
        p["Entered (IST)"] = to_ist(p["entry_fill_ts"])
        st.dataframe(
            p[["strategy_name", "instrument", "position_type", "quantity",
               "Entered (IST)", "entry_price", "stop_loss_price", "target_price"]]
            .rename(columns={
                "strategy_name": "Strategy", "instrument": "Instrument",
                "position_type": "Side", "quantity": "Qty",
                "entry_price": "Entry", "stop_loss_price": "Stop",
                "target_price": "Target",
            }),
            use_container_width=True, hide_index=True,
        )

    # ---- next steps --------------------------------------------------------
    with st.expander("✅ Setup checklist — what to do next", expanded=trades.empty):
        checks = [
            (not strategies.empty, "Strategies stored in the database"),
            (not audits.empty, "Paper engine has run at least once"),
            (
                not data["backtest_results"].empty,
                "At least one backtest completed",
            ),
            (not trades.empty, "At least one simulated trade closed"),
        ]
        for done, label in checks:
            st.write(("✅ " if done else "⬜ ") + label)
        if not ctx.can_edit:
            st.caption(
                "You're in view-only mode. To create strategies or run "
                "backtests from this app, run it locally: "
                "`.venv\\Scripts\\streamlit.exe run dashboard.py`"
            )
