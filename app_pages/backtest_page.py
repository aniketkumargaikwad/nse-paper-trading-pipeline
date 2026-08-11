"""Backtest page: run tests on history and read the results honestly."""

from __future__ import annotations

import json
import subprocess
import sys

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, load_all, page_header, to_ist
from metrics import MIN_PROFITABLE_SYMBOL_PCT

KILL_RULE_LABELS = {
    "min_trades": "Enough trades (≥30)",
    "net_positive_after_costs": "Profitable after costs",
    "drawdown_within_cap": "Drawdown ≤ 20%",
    # Phase 2 made this a proportion. A fixed count of 3 was a 60% bar across
    # five hand-picked symbols, but only a 6% bar on NIFTY50 — it would have
    # passed a strategy losing money on 47 of 50 stocks.
    "symbol_robustness": f"Profitable on ≥{MIN_PROFITABLE_SYMBOL_PCT:g}% of symbols",
}



def _render_verdict(runs: pd.DataFrame, batch_id) -> None:
    """The strategy-level verdict for one batch.

    Per-symbol rows answer "what happened on each stock". This answers the
    question the universe was for: did the edge hold broadly, or did one lucky
    name carry it? Those two disagree exactly when it matters most.
    """
    st.subheader("Strategy verdict")

    if runs.empty:
        st.info(
            "No strategy-level verdict for this run.\n\n"
            "If this is the first run since upgrading, apply "
            "`sql/004_backtest_runs.sql` in the Supabase SQL editor and run the "
            "backtest again. Per-instrument results below are unaffected."
        )
        return

    mine = runs[runs["batch_id"] == batch_id]
    if mine.empty:
        st.info(
            "This run predates the strategy-level verdict. Re-run the backtest "
            "to get one; the per-instrument results below are unaffected."
        )
        return

    for _, r in mine.iterrows():
        passed = bool(r["passed_kill_rules"])
        scope = (
            f"universe **{r['universe_name']}** (list dated {r['constituents_as_of']})"
            if r.get("universe_name")
            else "an explicit instrument list"
        )
        resolved, requested = int(r["symbols_resolved"]), int(r["symbols_requested"])

        with st.container(border=True):
            st.markdown(
                f"### {'✅' if passed else '❌'} {r['strategy_name']}"
                f"  ·  {r['timeframe']}"
            )
            st.caption(f"Tested over {scope} — {resolved} of {requested} symbols.")

            if resolved < requested:
                missing = r.get("symbols_missing") or []
                if isinstance(missing, str):
                    missing = json.loads(missing)
                st.warning(
                    f"**{requested - resolved} symbol(s) produced no candles** "
                    f"and are NOT in these numbers: {', '.join(missing[:10])}"
                    + (" …" if len(missing) > 10 else "")
                )

            a, b, c, d = st.columns(4)
            a.metric("Net P&L", f"₹{float(r['net_pnl']):,.0f}")
            b.metric(
                "Symbols profitable",
                f"{int(r['symbols_profitable'])} / {resolved}",
                help=(
                    "The dispersion check. A big net P&L earned on very few "
                    "symbols usually means one outlier carried the result."
                ),
            )
            c.metric("Trades", int(r["total_trades"]))
            d.metric("Max drawdown", f"{float(r['max_drawdown_pct']):.1f}%")

            e, f_, g, h = st.columns(4)
            e.metric(
                "Sharpe (daily)", _fmt(r["sharpe_daily"]),
                help=(
                    "Return per unit of risk, annualised from daily P&L. "
                    "Deliberately conservative: it assumes every symbol could "
                    "hold a position at once, so it reads low rather than "
                    "flattering. Blank means not measurable from this data."
                ),
            )
            f_.metric("Sortino (daily)", _fmt(r["sortino_daily"]),
                      help="Like Sharpe, but only counts downside volatility.")
            g.metric("Expectancy / trade", f"₹{float(r['expectancy_per_trade']):,.0f}",
                     help="Average net P&L per trade, after costs.")
            h.metric("Median symbol", f"₹{float(r['median_symbol_pnl']):,.0f}",
                     help="The typical stock's result — unmoved by one outlier.")

            best, worst = r.get("best_symbol"), r.get("worst_symbol")
            if best and worst:
                st.caption(
                    f"Best: **{best}** ₹{float(r['best_symbol_pnl']):,.0f}  ·  "
                    f"Worst: **{worst}** ₹{float(r['worst_symbol_pnl']):,.0f}"
                )

            if int(r.get("entries_skipped") or 0):
                st.caption(
                    f"⚠️ {int(r['entries_skipped'])} entry signal(s) were skipped "
                    "because the rupees-per-trade setting was below the share "
                    "price. Those signals produced no trade."
                )

            if r.get("universe_name"):
                st.caption(
                    "ℹ️ Index membership is **today's**, applied to past data. "
                    "Stocks join an index after they have already risen, so a "
                    "result like this is flattered by survivorship."
                )


def _fmt(value) -> str:
    """Blank for a null metric. A null means 'not measurable from this data' —
    showing 0.00 would present an absent measurement as a measured zero."""
    return "—" if value is None or pd.isna(value) else f"{float(value):.2f}"


def _run_backtest_ui(ctx: AppContext, strategies: pd.DataFrame) -> None:
    """Launch backtest.py as a subprocess and stream its output."""
    st.markdown(
        "Runs the batch backtester against historical data and stores the "
        "results below."
    )
    st.caption(
        "How far back a run can reach depends on your data provider. Dhan "
        "serves ~5 years of intraday history from the local candle store; the "
        "free Yahoo feed serves only ~58 days of 15m/30m, in which case use "
        "**60m or day** for evidence you can trust. The run prints a warning "
        "when it has to shorten the window."
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
        runs = data.get("backtest_runs", pd.DataFrame())
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

        _render_verdict(runs, chosen_batch)

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
