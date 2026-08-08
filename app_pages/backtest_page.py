"""Backtest page: run tests on history and read the results honestly."""

from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, load_all, page_header, to_ist

KILL_RULE_LABELS = {
    "min_trades": "Enough trades (≥30)",
    "net_positive_after_costs": "Profitable after costs",
    "drawdown_within_cap": "Drawdown ≤ 20%",
    "symbol_robustness": "Works on ≥3 symbols",
}


def _run_backtest_ui(ctx: AppContext, strategies: pd.DataFrame) -> None:
    """Launch backtest.py as a subprocess and stream its output."""
    st.markdown(
        "Runs the batch backtester against historical data and stores the "
        "results below. Free-data limits apply: **15m/30m reach back only "
        "~58 days**, so use **60m or day** for evidence you can trust."
    )
    c1, c2 = st.columns([1, 2])
    years = c1.number_input("Years of history", 0.5, 10.0, value=2.0, step=0.5)
    names = ["(all enabled)"] + (list(strategies["name"]) if not strategies.empty else [])
    chosen = c2.selectbox("Strategy", names)

    if st.button("▶️ Run backtest", type="primary", disabled=not ctx.can_edit):
        cmd = [sys.executable, "backtest.py", "--years", str(years)]
        if chosen != "(all enabled)":
            cmd += ["--strategy", chosen]
        with st.status("Running backtest…", expanded=True) as status:
            st.caption(" ".join(cmd[1:]))
            try:
                proc = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=1800,
                )
            except subprocess.TimeoutExpired:
                status.update(label="Timed out after 30 minutes", state="error")
                return
            output = (proc.stdout or "") + (proc.stderr or "")
            st.code(output[-6000:] or "(no output)")
            if proc.returncode == 0:
                status.update(label="Backtest finished", state="complete")
                st.cache_data.clear()
            else:
                status.update(label=f"Failed (exit {proc.returncode})", state="error")


def render(ctx: AppContext) -> None:
    page_header(
        "🔬 Backtest",
        "Test rules against history before risking anything — even paper.",
        ctx,
    )

    data = load_all(ctx)
    results, strategies = data["backtest_results"], data["strategies"]

    tab_results, tab_run = st.tabs(["📈 Results", "▶️ Run a backtest"])

    with tab_run:
        if not ctx.can_edit:
            st.warning(
                "Running backtests needs **edit mode**. Locally:\n\n"
                "```\n.venv\\Scripts\\streamlit.exe run dashboard.py\n```\n"
                "or straight from the terminal:\n\n"
                "```\n.venv\\Scripts\\python.exe backtest.py --years 2\n```"
            )
        else:
            _run_backtest_ui(ctx, strategies)

    with tab_results:
        if results.empty:
            empty_state(
                "No backtest results yet",
                "Switch to **Run a backtest** (or run "
                "`.venv\\Scripts\\python.exe backtest.py` in a terminal). "
                "Results land here automatically.",
                "🔬",
            )
            return

        df = results.copy()
        df["created_at_ist"] = to_ist(df["created_at"])
        batches = (
            df.groupby("batch_id")["created_at_ist"].max().sort_values(ascending=False)
        )
        labels = {
            bid: f"{ts.strftime('%d %b %Y, %H:%M')} — {len(df[df.batch_id == bid])} combos"
            for bid, ts in batches.items()
        }
        chosen_batch = st.selectbox(
            "Backtest run", list(batches.index), format_func=lambda b: labels[b]
        )
        batch = df[df["batch_id"] == chosen_batch].copy()

        passed = int(batch["passed_kill_rules"].sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Combinations", len(batch))
        c2.metric("Passed all rules", f"{passed} / {len(batch)}")
        c3.metric("Best net P&L", f"₹{batch['net_pnl'].astype(float).max():,.0f}")
        c4.metric("Total trades", int(batch["total_trades"].sum()))

        if passed == 0:
            st.warning(
                "**Nothing passed the robustness rules.** That is a normal and "
                "useful result — it means these rules have not earned live use. "
                "Common causes: too few trades (short free-data history on 15m), "
                "or the flat ₹30 cost swallowing a small target (raise quantity)."
            )

        st.subheader("Per instrument")
        show = batch[[
            "strategy_name", "instrument", "timeframe", "total_trades",
            "win_rate_pct", "net_pnl", "profit_factor", "max_drawdown_pct",
            "longest_losing_streak", "passed_kill_rules",
        ]].rename(columns={
            "strategy_name": "Strategy", "instrument": "Instrument",
            "timeframe": "TF", "total_trades": "Trades",
            "win_rate_pct": "Win %", "net_pnl": "Net ₹",
            "profit_factor": "Profit factor", "max_drawdown_pct": "Max DD %",
            "longest_losing_streak": "Worst streak", "passed_kill_rules": "Passed",
        })
        st.dataframe(
            show.sort_values("Net ₹", ascending=False),
            use_container_width=True, hide_index=True,
            column_config={
                "Passed": st.column_config.CheckboxColumn(disabled=True),
                "Net ₹": st.column_config.NumberColumn(format="%.2f"),
            },
        )

        st.subheader("Why did a combination fail?")
        pick = st.selectbox(
            "Inspect", batch.index,
            format_func=lambda i: f"{batch.loc[i,'strategy_name']} · {batch.loc[i,'instrument']}",
        )
        flags = batch.loc[pick, "kill_rule_flags"]
        if isinstance(flags, str):
            flags = json.loads(flags)
        for key, detail in (flags or {}).items():
            label = KILL_RULE_LABELS.get(key, key)
            ok = detail.get("passed")
            bits = ", ".join(
                f"{k.replace('_', ' ')}: **{v}**"
                for k, v in detail.items() if k != "passed"
            )
            st.write(("✅ " if ok else "❌ ") + f"{label} — {bits}")

        with st.expander("📥 Download this batch as CSV"):
            st.download_button(
                "Download CSV",
                batch.to_csv(index=False).encode(),
                file_name=f"backtest_{str(chosen_batch)[:8]}.csv",
                mime="text/csv",
            )
