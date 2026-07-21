"""Read-only Streamlit dashboard for the paper-trading pipeline.

Run locally:

    .venv\\Scripts\\streamlit run dashboard.py        (Windows)
    .venv/bin/streamlit run dashboard.py              (Mac/Linux)

Deploy: push the repo to GitHub, create an app on Streamlit Community Cloud
pointing at dashboard.py, and set two secrets (App settings -> Secrets):

    SUPABASE_URL = "https://<your-ref>.supabase.co"
    SUPABASE_ANON_KEY = "<the anon/public key>"

SECURITY
--------
* Use the ANON key only. Row Level Security (sql/001_init.sql) gives it
  read-only SELECT on display tables and NO access to daily_token, so the
  Kite token can never leak through the dashboard.
* This file refuses to start with a service-role key (checked below) —
  a paste-the-wrong-key mistake fails loudly instead of silently running
  the public dashboard with god-mode credentials.
* Everything here is display; there is no write path and no order path.

All timestamps are stored in UTC and converted to IST for display only.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Pure helpers (unit-tested without Streamlit running)
# ---------------------------------------------------------------------------


def is_forbidden_key(key: str) -> bool:
    """True if `key` looks like a SERVICE-ROLE/secret key (must never be
    used by the dashboard). Anon/publishable keys return False.

    Supabase legacy keys are JWTs whose payload carries a `role` claim;
    new-style secret keys start with 'sb_secret_'. Anything unparseable is
    allowed through — RLS is the real enforcement, this is just a tripwire.
    """
    key = key.strip()
    if key.startswith("sb_secret_"):
        return True
    parts = key.split(".")
    if len(parts) == 3:  # looks like a JWT
        try:
            payload_raw = parts[1] + "=" * (-len(parts[1]) % 4)  # fix b64 padding
            payload = json.loads(base64.urlsafe_b64decode(payload_raw))
        except (ValueError, binascii.Error):
            return False
        return payload.get("role") == "service_role"
    return False


def to_ist_display(series: pd.Series) -> pd.Series:
    """UTC timestamp series -> tz-naive IST for tables/charts.

    Streamlit renders tz-aware timestamps in UTC; converting to IST and
    dropping the tz makes every displayed time an IST wall-clock time.
    """
    ts = pd.to_datetime(series, utc=True, format="ISO8601")
    return ts.dt.tz_convert(IST).dt.tz_localize(None)


def leaderboard(trades: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """Aggregate the trades log into one row per strategy, best first."""
    if trades.empty:
        return pd.DataFrame(
            columns=["strategy", "trades", "win_rate_pct", "net_pnl", "today_pnl"]
        )
    df = trades.copy()
    df["net_pnl"] = df["net_pnl"].astype(float)
    df["exit_ist_date"] = to_ist_display(df["exit_fill_ts"]).dt.date
    today_ist = now_utc.astimezone(IST).date()

    rows = []
    for name, group in df.groupby("strategy_name"):
        wins = int((group["net_pnl"] > 0).sum())
        rows.append(
            {
                "strategy": name,
                "trades": len(group),
                "win_rate_pct": round(100.0 * wins / len(group), 1),
                "net_pnl": round(float(group["net_pnl"].sum()), 2),
                "today_pnl": round(
                    float(group.loc[group["exit_ist_date"] == today_ist, "net_pnl"].sum()), 2
                ),
            }
        )
    return (
        pd.DataFrame(rows)
        .sort_values("net_pnl", ascending=False)
        .reset_index(drop=True)
    )


def equity_and_drawdown(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative net P&L and running drawdown, indexed by IST exit time.

    equity[i]  = sum of net_pnl of the first i+1 closed trades
    drawdown[i]= equity[i] - running peak  (0 at peaks, negative below them)
    """
    if trades.empty:
        return pd.DataFrame(columns=["equity", "drawdown"])
    df = trades.copy()
    df["exit_ts"] = pd.to_datetime(df["exit_fill_ts"], utc=True, format="ISO8601")
    df = df.sort_values("exit_ts")
    df["net_pnl"] = df["net_pnl"].astype(float)
    equity = df["net_pnl"].cumsum()
    drawdown = equity - equity.cummax()
    out = pd.DataFrame(
        {"equity": equity.round(2).values, "drawdown": drawdown.round(2).values},
        index=df["exit_ts"].dt.tz_convert(IST).dt.tz_localize(None),
    )
    out.index.name = "exit time (IST)"
    return out


def todays_trades(trades: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """Trades whose EXIT fill falls on today's IST calendar date."""
    if trades.empty:
        return trades
    df = trades.copy()
    today_ist = now_utc.astimezone(IST).date()
    mask = to_ist_display(df["exit_fill_ts"]).dt.date == today_ist
    return df[mask]


# ---------------------------------------------------------------------------
# Streamlit app (only runs under `streamlit run`)
# ---------------------------------------------------------------------------


def _running_under_streamlit() -> bool:
    try:
        from streamlit import runtime
        return runtime.exists()
    except Exception:
        return False


def _app() -> None:  # pragma: no cover — exercised by `streamlit run`, not pytest
    import streamlit as st
    from supabase import create_client

    st.set_page_config(page_title="Paper Trading Dashboard", page_icon="📈", layout="wide")

    # ---- credentials: Streamlit secrets first, env vars for local dev ----
    def _secrets_file_exists() -> bool:
        # Probing st.secrets when no secrets.toml exists makes Streamlit
        # render its own "No secrets found" warning on the page; ask first.
        try:
            return bool(st.secrets.load_if_toml_exists())
        except Exception:
            return False

    have_secrets_file = _secrets_file_exists()

    def _secret(name: str) -> str:
        if have_secrets_file and name in st.secrets:
            return str(st.secrets[name]).strip()
        return os.environ.get(name, "").strip()

    url, key = _secret("SUPABASE_URL"), _secret("SUPABASE_ANON_KEY")
    if not url or not key:
        st.error(
            "Dashboard is not configured. Set **SUPABASE_URL** and "
            "**SUPABASE_ANON_KEY** in Streamlit secrets "
            "(App settings → Secrets, or `.streamlit/secrets.toml` locally). "
            "Use the *anon* key — never the service-role key."
        )
        st.stop()
    if is_forbidden_key(key):
        st.error(
            "SUPABASE_ANON_KEY looks like a **service-role/secret key**. "
            "The dashboard must use the anon (public) key only — replace it "
            "in your Streamlit secrets. (Found in Supabase: Project Settings "
            "→ API → `anon` `public`.)"
        )
        st.stop()

    @st.cache_resource
    def client():
        return create_client(url, key)

    @st.cache_data(ttl=50, show_spinner=False)
    def fetch(table: str, order_by: str | None = None, desc: bool = True,
              limit: int = 5000) -> pd.DataFrame:
        """Paged read of one table (anon key -> RLS enforces read-only)."""
        rows, page, start = [], 1000, 0
        q = client().table(table).select("*")
        if order_by:
            q = q.order(order_by, desc=desc)
        while start < limit:
            resp = q.range(start, min(start + page, limit) - 1).execute()
            rows.extend(resp.data)
            if len(resp.data) < page:
                break
            start += page
        return pd.DataFrame(rows)

    @st.fragment(run_every=60)  # auto-refresh: rerender this block every 60s
    def dashboard_body() -> None:
        now_utc = datetime.now(tz=ZoneInfo("UTC"))
        now_ist = now_utc.astimezone(IST)

        st.title("📈 Paper Trading Dashboard")
        st.caption(
            "**Simulation only — no real orders exist anywhere in this system.** "
            f"Auto-refreshes every minute · last refresh "
            f"{now_ist.strftime('%d %b %Y, %H:%M:%S')} IST"
        )

        try:
            trades = fetch("trades", order_by="exit_fill_ts")
            positions = fetch("positions", order_by="entry_fill_ts")
            audits = fetch("run_audit", order_by="run_started_at", limit=50)
            strategies = fetch("strategies")
        except Exception as exc:
            st.error(
                f"Could not read from Supabase: {exc}\n\n"
                "Check that sql/001_init.sql was applied and the anon key is valid."
            )
            return

        # ---- engine health strip ----------------------------------------
        if not audits.empty:
            last = audits.iloc[0]
            started_ist = to_ist_display(pd.Series([last["run_started_at"]])).iloc[0]
            status = str(last["status"])
            badge = {"ok": "🟢", "skipped": "🟡", "error": "🔴"}.get(status, "⚪")
            reason = f" — {last['reason']}" if last.get("reason") else ""
            st.info(
                f"{badge} Last engine run: **{status}**{reason} · "
                f"{started_ist.strftime('%d %b %H:%M')} IST · type: {last['run_type']}"
            )
        else:
            st.info("No engine runs recorded yet — the audit trail is empty.")

        # ---- headline numbers -------------------------------------------
        lb = leaderboard(trades, now_utc)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total net P&L (₹)", f"{lb['net_pnl'].sum():,.2f}" if not lb.empty else "0.00")
        c2.metric("Today's P&L (₹)", f"{lb['today_pnl'].sum():,.2f}" if not lb.empty else "0.00")
        c3.metric("Closed trades", int(lb["trades"].sum()) if not lb.empty else 0)
        c4.metric("Open positions", len(positions))

        # ---- leaderboard -------------------------------------------------
        st.subheader("Strategy leaderboard")
        if lb.empty:
            st.write("No closed trades yet. The table fills as the engine completes round-trips.")
        else:
            enabled_map = (
                dict(zip(strategies["name"], strategies["enabled"]))
                if not strategies.empty else {}
            )
            lb_disp = lb.copy()
            lb_disp.insert(1, "enabled", lb_disp["strategy"].map(enabled_map).fillna("?"))
            st.dataframe(lb_disp, use_container_width=True, hide_index=True)

        # ---- equity + drawdown per strategy ------------------------------
        st.subheader("Equity curve & drawdown")
        strategy_names = sorted(trades["strategy_name"].unique()) if not trades.empty else []
        if not strategy_names:
            st.write("Charts appear after the first closed trade.")
        else:
            chosen = st.selectbox("Strategy", strategy_names)
            curve = equity_and_drawdown(trades[trades["strategy_name"] == chosen])
            left, right = st.columns(2)
            with left:
                st.caption("Cumulative net P&L (₹)")
                st.line_chart(curve["equity"], use_container_width=True)
            with right:
                st.caption("Drawdown from peak (₹)")
                st.area_chart(curve["drawdown"], use_container_width=True)

        # ---- open positions ---------------------------------------------
        st.subheader("Open positions")
        if positions.empty:
            st.write("No open positions.")
        else:
            disp = positions.copy()
            disp["entry (IST)"] = to_ist_display(disp["entry_fill_ts"])
            st.dataframe(
                disp[
                    ["strategy_name", "instrument", "position_type", "quantity",
                     "entry (IST)", "entry_price", "stop_loss_price", "target_price"]
                ],
                use_container_width=True, hide_index=True,
            )

        # ---- today's trades ----------------------------------------------
        st.subheader("Today's closed trades")
        today = todays_trades(trades, now_utc)
        if today.empty:
            st.write("No trades closed today (IST).")
        else:
            disp = today.copy()
            disp["exit (IST)"] = to_ist_display(disp["exit_fill_ts"])
            disp = disp.sort_values("exit (IST)", ascending=False)
            st.dataframe(
                disp[
                    ["strategy_name", "instrument", "position_type", "quantity",
                     "exit (IST)", "entry_price", "exit_price", "exit_reason",
                     "gross_pnl", "costs", "net_pnl"]
                ],
                use_container_width=True, hide_index=True,
            )

        # ---- operational history -----------------------------------------
        with st.expander("Recent engine runs (troubleshooting)"):
            if audits.empty:
                st.write("No runs recorded.")
            else:
                adisp = audits.copy()
                adisp["started (IST)"] = to_ist_display(adisp["run_started_at"])
                st.dataframe(
                    adisp[["started (IST)", "run_type", "status", "reason", "details"]],
                    use_container_width=True, hide_index=True,
                )

    dashboard_body()


if _running_under_streamlit():  # pragma: no cover
    _app()
elif __name__ == "__main__":
    print("This is a Streamlit app. Run it with:\n  streamlit run dashboard.py")
