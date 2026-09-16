"""Research: what the daily loop found, one row per run.

The grid is design 7.1, the detail 7.2. Every row is a run that was measured
on training years only; the locked year beside it was opened once, at the end.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, fetch_optional_table, page_header, to_ist

GRID_COLUMNS = [
    "Date", "Strategy", "Description", "Trades/month", "Traded on",
    "Best timeframe", "Return/month", "Success ratio", "₹1 lakh → became",
    "Just holding → became", "Worst dip", "Verdict", "Beat holding", "Broad or lucky",
    "Versions", "Run status",
]


def _missing(value: Any) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):      # lists and other containers
        return False


def _rupees(value: Any) -> str:
    """Indian grouping: Rs 1,07,461 rather than Rs 107,461."""
    if _missing(value):
        return "—"
    whole = f"{int(round(float(value))):d}"
    negative = whole.startswith("-")
    digits = whole.lstrip("-")
    if len(digits) <= 3:
        return ("-" if negative else "") + f"₹{digits}"
    head, tail = digits[:-3], digits[-3:]
    groups: list[str] = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ("-" if negative else "") + "₹" + ",".join(groups + [tail])


def _text(value: Any) -> str:
    return "—" if _missing(value) else str(value)


def _percent(value: Any, digits: int = 0) -> str:
    return "—" if _missing(value) else f"{float(value):.{digits}f}%"


def verdict_label(passed: Any) -> str:
    if _missing(passed):
        return "—"
    return "✅ Passed" if bool(passed) else "❌ Failed"


def grid_frame(runs: pd.DataFrame, descriptions: dict[str, str]) -> pd.DataFrame:
    """One display row per run, newest first (design 7.1)."""
    if runs.empty:
        return pd.DataFrame(columns=GRID_COLUMNS)

    r = runs.copy()
    r["_when"] = pd.to_datetime(r["started_at"], utc=True, errors="coerce")
    r = r.sort_values("_when", ascending=False)

    out = pd.DataFrame({
        "Date": to_ist(r["started_at"]).dt.strftime("%d %b %Y"),
        "Strategy": r["final_strategy_name"].map(_text),
        "Description": r["final_strategy_name"].map(lambda n: descriptions.get(n) or "—"),
        "Trades/month": r["trades_per_month"].map(
            lambda v: "—" if _missing(v) else f"{float(v):.1f}"),
        "Traded on": r["pick_symbol"].map(_text),
        "Best timeframe": r["pick_timeframe"].map(_text),
        # The basket's average locked month (runs from 16 Sep 2026 on); a
        # dash for the earlier single-stock runs, which never measured it.
        "Return/month": (
            r["locked_avg_month_pct"].map(lambda v: "—" if _missing(v) else f"{float(v):+.2f}%")
            if "locked_avg_month_pct" in r.columns else ["—"] * len(r)
        ),
        "Success ratio": r["win_rate_pct"].map(lambda v: _percent(v)),
        "₹1 lakh → became": r["lakh_end_value"].map(_rupees),
        "Just holding → became": r["hold_end_value"].map(_rupees),
        "Worst dip": r["worst_dip_pct"].map(lambda v: _percent(v, 1)),
        "Verdict": r["verdict_passed"].map(verdict_label),
        "Beat holding": r["beat_holding"].map(
            lambda v: "—" if _missing(v) else ("yes" if v else "no")),
        "Broad or lucky": [
            "—" if _missing(p) or _missing(t) else f"{int(p)} of {int(t)}"
            for p, t in zip(r["combos_profitable"], r["combos_tested"])
        ],
        "Versions": r["versions_tried"].map(_text),
        "Run status": r["status"].map(_text),
    })
    return out.reset_index(drop=True)


@st.cache_data(ttl=45, show_spinner=False)
def _children(_client, table: str, run_id: str, order_by: str) -> pd.DataFrame:
    """One run's child rows, filtered in the DATABASE.

    Fetching whole child tables and filtering here would break quietly: a
    single run stores about 1,213 combination rows, so after a few runs a
    5,000-row page read would stop reaching the older ones.
    """
    try:
        resp = (
            _client.table(table).select("*")
            .eq("run_id", run_id).order(order_by).limit(5000).execute()
        )
    except Exception as exc:        # noqa: BLE001 - an absent table is not fatal
        message = str(exc).lower()
        if "does not exist" in message or "not find the table" in message:
            return pd.DataFrame()
        raise
    return pd.DataFrame(resp.data)


def version_timeline(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One line per version the day tried, valid or not (design 7.2).

    A rejected version still keeps its row: an idea Opus could not express is
    a result about the idea. It says what the checker refused rather than
    showing an empty training line, which would read as a flat outcome.
    """
    timeline = []
    for row in sorted(rows, key=lambda r: (r.get("idea_no") or 0, r.get("version_no") or 0)):
        summary = row.get("training_summary") or {}
        if row.get("valid") and summary:
            training = (
                f"{summary.get('combos_profitable', 0)} of "
                f"{summary.get('combos_tested', 0)} profitable"
            )
            # Runs from before the excess ranking have no count. Saying "0
            # beat holding" there would be a measurement nobody made.
            if "combos_beating_hold" in summary:
                training += f", {summary['combos_beating_hold']} beat holding"
        elif row.get("valid"):
            training = "tested, no summary stored"
        else:
            training = f"rejected: {(row.get('error') or 'no reason recorded').splitlines()[0]}"
        timeline.append({
            "Version": f"{row.get('idea_no')}.{row.get('version_no')}",
            "Strategy": row.get("strategy_name") or "—",
            "Changed": row.get("change_note") or "—",
            "Training": training,
            "Decision": row.get("decision") or "—",
            "Lessons": row.get("lessons") or "—",
        })
    return timeline


def _detail(ctx: AppContext, run: pd.Series) -> None:
    st.subheader(
        f"{_text(run.get('final_strategy_name'))} — "
        f"{_text(run.get('pick_symbol'))} · {_text(run.get('pick_timeframe'))}"
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("₹1 lakh became", _rupees(run.get("lakh_end_value")))
    c2.metric("Just holding", _rupees(run.get("hold_end_value")))
    c3.metric("Average month", _text(None if _missing(run.get("locked_avg_month_pct"))
                                     else f"{float(run['locked_avg_month_pct']):+.2f}%"))
    c4.metric("Worst dip", _percent(run.get("worst_dip_pct"), 1))
    c5.metric("Verdict", verdict_label(run.get("verdict_passed")))

    months = run.get("locked_months")
    if isinstance(months, list) and months:
        st.markdown("**Locked year, month by month (the whole basket)**")
        table = pd.DataFrame(months).rename(columns={
            "month": "Month", "strategy_pct": "Strategy %", "holding_pct": "Holding %",
            "trades": "Trades", "stocks": "Stocks"})
        st.dataframe(table, use_container_width=True, hide_index=True, height=240)

    st.caption(
        f"Locked year {run.get('locked_from')} → {run.get('locked_to')}, opened once. "
        f"Chosen on training years only, from {_text(run.get('combos_tested'))} combinations."
    )

    run_id = str(run["id"])

    equity = _children(ctx.client, "research_locked_equity", run_id, "day")
    if not equity.empty:
        st.markdown("**Locked year: ₹1 lakh against just holding**")
        chart = (
            equity.sort_values("day").set_index("day")[["lakh_balance", "hold_balance"]]
            .astype(float)
        )
        st.line_chart(chart.rename(columns={
            "lakh_balance": "₹1 lakh strategy", "hold_balance": "Just holding"}))

    combos = _children(ctx.client, "research_combo_results", run_id, "symbol")
    if not combos.empty:
        st.markdown("**Every stock and timeframe tested (training years)**")
        tested = combos[combos["skipped_reason"].isna()].copy()
        if not tested.empty:
            tested["net_pnl"] = tested["net_pnl"].astype(float)
            st.dataframe(
                tested.sort_values("net_pnl", ascending=False)[
                    ["symbol", "timeframe", "trades", "win_rate_pct", "net_pnl",
                     "cagr_pct", "worst_dip_pct"]
                ].rename(columns={
                    "symbol": "Symbol", "timeframe": "Timeframe", "trades": "Trades",
                    "win_rate_pct": "Win %", "net_pnl": "Net ₹",
                    "cagr_pct": "Yearly %", "worst_dip_pct": "Worst dip %"}),
                use_container_width=True, hide_index=True, height=320,
            )
        skipped = combos[combos["skipped_reason"].notna()]
        if not skipped.empty:
            st.caption(
                f"{len(skipped)} combinations skipped — "
                + ", ".join(sorted(skipped["skipped_reason"].unique())[:3])
            )

    trades = _children(ctx.client, "research_locked_trades", run_id, "entry_at")
    if not trades.empty:
        st.markdown("**Locked-year trades**")
        shown = trades.copy()
        shown["Entry (IST)"] = to_ist(shown["entry_at"])
        shown["Exit (IST)"] = to_ist(shown["exit_at"])
        st.dataframe(
            shown[["Entry (IST)", "Exit (IST)", "side", "entry_price", "exit_price",
                   "fees", "net_return_pct", "balance_after"]].rename(columns={
                "side": "Side", "entry_price": "Entry", "exit_price": "Exit",
                "fees": "Fees ₹", "net_return_pct": "Return %",
                "balance_after": "Balance ₹"}),
            use_container_width=True, hide_index=True, height=260,
        )

    warnings = run.get("warnings")
    if isinstance(warnings, list) and warnings:
        st.markdown("**Warnings**")
        for line in warnings:
            st.caption(f"⚠️ {line}")

    versions = _children(ctx.client, "research_versions", run_id, "id")
    timeline = version_timeline(versions.to_dict("records") if not versions.empty else [])
    if timeline:
        st.markdown("**Versions tried**")
        st.caption(
            "Every version the day tried, in order. Training numbers only — the "
            "locked year was opened once, after the last of them."
        )
        st.dataframe(pd.DataFrame(timeline), use_container_width=True, hide_index=True)

    review = run.get("ai_review")
    if review and not _missing(review):
        st.markdown("**Review**")
        st.write(review)


def render(ctx: AppContext) -> None:
    page_header(
        "🔬 Research",
        "One row per research run. Each was built on training years only; the "
        "locked final year was opened once, at the end.",
        ctx,
    )

    runs = fetch_optional_table(ctx.client, "research_runs", "started_at")
    if runs.empty:
        empty_state(
            "No research runs yet",
            "Run one from the project folder:\n\n"
            "`.venv\\Scripts\\python.exe -m research.evaluate --strategy NAME`\n\n"
            "It tests every stock, index and timeframe on training years, picks "
            "one combination, then opens the locked year once.",
            "🔬",
        )
        return

    strategies = fetch_optional_table(ctx.client, "strategies", "name")
    descriptions: dict[str, str] = {}
    if not strategies.empty and "description" in strategies.columns:
        descriptions = {
            row["name"]: row["description"]
            for _, row in strategies.iterrows()
            if row.get("description")
        }

    grid = grid_frame(runs, descriptions)
    event = st.dataframe(
        grid, use_container_width=True, hide_index=True, height=320,
        on_select="rerun", selection_mode="single-row",
    )

    chosen = list(getattr(event, "selection", {}).get("rows", []))
    if not chosen:
        st.caption("Select a row to see the full detail.")
        return

    ordered = runs.copy()
    ordered["_when"] = pd.to_datetime(ordered["started_at"], utc=True, errors="coerce")
    ordered = ordered.sort_values("_when", ascending=False).reset_index(drop=True)
    st.divider()
    _detail(ctx, ordered.iloc[chosen[0]])
