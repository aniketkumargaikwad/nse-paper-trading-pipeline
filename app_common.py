"""Shared plumbing for the Streamlit app: connection, caching, formatting.

TWO MODES, detected automatically from which Supabase key is configured
-----------------------------------------------------------------------
* **View mode** (anon key)  - read-only. Safe to deploy publicly. This is
  what Streamlit Community Cloud should use.
* **Edit mode** (service key) - adds strategy create/edit/enable and
  backtest running. Intended for running the app LOCALLY, where the key
  lives in your .env and never leaves your machine.

The app never silently gains write powers: edit mode shows a persistent
badge, and the System page explains exactly why it is on.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Key inspection (pure — mirrors dashboard.py's tripwire)
# ---------------------------------------------------------------------------


def key_role(key: str) -> str:
    """Best-effort role of a Supabase key: 'service_role', 'anon', 'unknown'."""
    key = (key or "").strip()
    if not key:
        return "unknown"
    if key.startswith("sb_secret_"):
        return "service_role"
    if key.startswith("sb_publishable_"):
        return "anon"
    parts = key.split(".")
    if len(parts) == 3:
        try:
            padded = parts[1] + "=" * (-len(parts[1]) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError, binascii.Error):
            return "unknown"
        role = payload.get("role")
        if role in ("service_role", "anon"):
            return role
    return "unknown"


# ---------------------------------------------------------------------------
# Secrets / configuration
# ---------------------------------------------------------------------------


def _secrets_file_exists() -> bool:
    # Probing st.secrets with no secrets.toml makes Streamlit render its own
    # "No secrets found" warning on the page; ask first.
    try:
        return bool(st.secrets.load_if_toml_exists())
    except Exception:
        return False


def get_config_value(name: str) -> str:
    """Streamlit secrets first, then environment (.env for local runs)."""
    if _secrets_file_exists():
        try:
            if name in st.secrets:
                return str(st.secrets[name]).strip()
        except Exception:
            pass
    return os.environ.get(name, "").strip()


def require_login() -> bool:
    """Password-gate the app when APP_PASSWORD is set. Returns True to proceed.

    Edit mode is granted by the SERVICE-ROLE Supabase key, which is full
    access to the database. A deployed dashboard therefore has to carry that
    key to be useful at all - and without a gate, anyone who finds the URL
    could create strategies, flip them live, and read every trade.

    So the rule is: if APP_PASSWORD is set, ask for it. Local runs usually
    have no APP_PASSWORD and are unaffected, which keeps development friction
    at zero; a HOSTED deployment must set one, and warns loudly if it did not.

    This is a single shared password, not user accounts. It suits one operator
    with one dashboard, which is what this is. It is not suitable for handing
    out to several people with different permissions.
    """
    expected = get_config_value("APP_PASSWORD")

    if not expected:
        # No password configured. Fine locally; dangerous anywhere public, so
        # say so rather than failing open in silence.
        if _looks_hosted():
            st.error(
                "**This deployment has no APP_PASSWORD set.**\n\n"
                "The dashboard holds a service-role database key, so anyone "
                "with this URL could create or delete strategies. Set an "
                "`APP_PASSWORD` environment variable on the host and redeploy."
            )
            return False
        return True

    if st.session_state.get("_authenticated"):
        return True

    st.markdown("### 📈 Trading Workbench")
    st.caption("Enter the dashboard password to continue.")
    with st.form("login"):
        supplied = st.text_input("Password", type="password")
        if st.form_submit_button("Sign in", type="primary"):
            # compare_digest avoids leaking the answer through response timing.
            if secrets_compare(supplied, expected):
                st.session_state["_authenticated"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    return False


def secrets_compare(a: str, b: str) -> bool:
    """Constant-time string comparison."""
    import hmac

    return hmac.compare_digest(str(a), str(b))


def _looks_hosted() -> bool:
    """Best-effort: are we running on a host rather than someone's laptop?

    Deliberately errs toward "hosted" only on clear signals, so a local run is
    never blocked by a false positive.
    """
    return any(
        os.environ.get(name)
        for name in ("RAILWAY_ENVIRONMENT", "RENDER", "FLY_APP_NAME", "DYNO")
    )


@st.cache_resource(show_spinner=False)
def _make_client(url: str, key: str):
    from supabase import create_client

    return create_client(url, key)


class AppContext:
    """Everything a page needs: a client, the mode, and the store (if writable)."""

    def __init__(self, url: str, key: str, role: str) -> None:
        self.url = url
        self.key = key
        self.role = role
        self.can_edit = role == "service_role"
        self.client = _make_client(url, key)

    @property
    def mode_label(self) -> str:
        return "Edit mode" if self.can_edit else "View only"

    def store(self):
        """A SupabaseStore for write operations (edit mode only)."""
        if not self.can_edit:
            raise PermissionError(
                "This action needs edit mode (a service-role key). Run the app "
                "locally with your .env configured."
            )
        from db import SupabaseStore

        return SupabaseStore(self.client)


def get_context() -> AppContext | None:
    """Build the app context, or render a setup message and return None."""
    url = get_config_value("SUPABASE_URL")
    key = get_config_value("SUPABASE_SERVICE_ROLE_KEY") or get_config_value(
        "SUPABASE_ANON_KEY"
    )
    if not url or not key:
        st.error(
            "**Not configured yet.**\n\n"
            "Set `SUPABASE_URL` plus one key:\n"
            "- `SUPABASE_ANON_KEY` → read-only view (use this on Streamlit Cloud)\n"
            "- `SUPABASE_SERVICE_ROLE_KEY` → full edit mode (local use only)\n\n"
            "Locally these come from your `.env`; on Streamlit Cloud from "
            "**Settings → Secrets**."
        )
        return None
    try:
        return AppContext(url, key, key_role(key))
    except Exception as exc:
        # A malformed URL or key raises here. Show the fix, not a traceback.
        st.error(
            f"**Could not connect to Supabase.**\n\n`{str(exc)[:300]}`\n\n"
            "Check in Supabase → **Project Settings → API**:\n"
            "- `SUPABASE_URL` looks like `https://<ref>.supabase.co`\n"
            "- the key is copied whole (they are long — make sure nothing was "
            "truncated)\n\n"
            "Locally these live in `.env`; on Streamlit Cloud in "
            "**Settings → Secrets**."
        )
        return None


# ---------------------------------------------------------------------------
# Data access (cached)
# ---------------------------------------------------------------------------


@st.cache_data(ttl=45, show_spinner=False)
def fetch_table(
    _client, table: str, order_by: str | None = None, desc: bool = True, limit: int = 5000
) -> pd.DataFrame:
    """Paged read of one table. `_client` is underscore-prefixed so Streamlit
    doesn't try to hash it (it caches on the remaining arguments)."""
    rows, page, start = [], 1000, 0
    while start < limit:
        q = _client.table(table).select("*")
        if order_by:
            q = q.order(order_by, desc=desc)
        resp = q.range(start, min(start + page, limit) - 1).execute()
        rows.extend(resp.data)
        if len(resp.data) < page:
            break
        start += page
    return pd.DataFrame(rows)


@st.cache_data(ttl=45, show_spinner=False)
def fetch_optional_table(
    _client, table: str, order_by: str | None = None, limit: int = 5000
) -> pd.DataFrame:
    """Like fetch_table, but an ABSENT table returns empty instead of raising.

    Used for tables added by a later migration. Without this, a dashboard whose
    database has not had the newest SQL applied would fail to load EVERY page,
    which turns "one feature is unavailable" into "nothing works at all" - a
    wildly disproportionate failure for a missing optional table.

    Only a missing-relation error is swallowed. A bad key, a network failure or
    anything else still raises, because those are real problems the existing
    error handling explains properly.
    """
    try:
        return fetch_table(_client, table, order_by=order_by, limit=limit)
    except Exception as exc:
        message = str(exc).lower()
        if "does not exist" in message or "not find the table" in message:
            return pd.DataFrame()
        raise


EMPTY_TABLES = (
    "trades", "positions", "run_audit", "strategies", "backtest_results",
    "backtest_runs",
)


def load_all(ctx: AppContext) -> dict[str, pd.DataFrame]:
    """Every table the pages need, in one place.

    A failure here (bad key, missing tables, Supabase down) renders one clear
    message and returns EMPTY frames, so each page falls back to its normal
    empty state instead of crashing with a traceback.
    """
    c = ctx.client
    try:
        return {
            "trades": fetch_table(c, "trades", order_by="exit_fill_ts"),
            "positions": fetch_table(c, "positions", order_by="entry_fill_ts"),
            "run_audit": fetch_table(c, "run_audit", order_by="run_started_at", limit=200),
            "strategies": fetch_table(c, "strategies", order_by="name", desc=False),
            "backtest_results": fetch_table(c, "backtest_results", order_by="created_at"),
            # Optional: added by sql/004. A database without it should lose
            # the verdict panel, not the whole dashboard.
            "backtest_runs": fetch_optional_table(c, "backtest_runs", order_by="created_at"),
        }
    except Exception as exc:
        message = str(exc)
        st.error(
            f"**Could not read from Supabase.**\n\n`{message[:400]}`\n\n"
            + (
                "If this mentions a missing relation/table, run "
                "`sql/001_init.sql` in the Supabase SQL editor.\n\n"
                "If it mentions JWT or authorization, re-copy your key.\n\n"
                "If it mentions name resolution, check `SUPABASE_URL`."
            )
        )
        return {name: pd.DataFrame() for name in EMPTY_TABLES}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def to_ist(series: pd.Series) -> pd.Series:
    """UTC timestamp column -> tz-naive IST (Streamlit renders aware ts in UTC)."""
    ts = pd.to_datetime(series, utc=True, format="ISO8601")
    return ts.dt.tz_convert(IST).dt.tz_localize(None)


def rupees(value: float) -> str:
    """₹ with a sign, for metrics."""
    return f"₹{value:,.2f}"


def leaderboard(trades: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """One row per strategy: trades, win %, net P&L, today's P&L. Best first."""
    if trades.empty:
        return pd.DataFrame(
            columns=["strategy", "trades", "win_rate_pct", "net_pnl", "today_pnl"]
        )
    df = trades.copy()
    df["net_pnl"] = df["net_pnl"].astype(float)
    df["exit_ist_date"] = to_ist(df["exit_fill_ts"]).dt.date
    today_ist = now_utc.astimezone(IST).date()

    rows = []
    for name, group in df.groupby("strategy_name"):
        wins = int((group["net_pnl"] > 0).sum())
        rows.append({
            "strategy": name,
            "trades": len(group),
            "win_rate_pct": round(100.0 * wins / len(group), 1),
            "net_pnl": round(float(group["net_pnl"].sum()), 2),
            "today_pnl": round(
                float(group.loc[group["exit_ist_date"] == today_ist, "net_pnl"].sum()), 2
            ),
        })
    return pd.DataFrame(rows).sort_values("net_pnl", ascending=False).reset_index(drop=True)


def equity_and_drawdown(trades: pd.DataFrame) -> pd.DataFrame:
    """Cumulative net P&L and running drawdown, indexed by IST exit time."""
    if trades.empty:
        return pd.DataFrame(columns=["equity", "drawdown"])
    df = trades.copy()
    df["exit_ts"] = pd.to_datetime(df["exit_fill_ts"], utc=True, format="ISO8601")
    df = df.sort_values("exit_ts")
    equity = df["net_pnl"].astype(float).cumsum()
    out = pd.DataFrame(
        {"equity": equity.round(2).values,
         "drawdown": (equity - equity.cummax()).round(2).values},
        index=df["exit_ts"].dt.tz_convert(IST).dt.tz_localize(None),
    )
    out.index.name = "exit time (IST)"
    return out


def todays_trades(trades: pd.DataFrame, now_utc: datetime) -> pd.DataFrame:
    """Trades whose EXIT fill falls on today's IST calendar date."""
    if trades.empty:
        return trades
    today_ist = now_utc.astimezone(IST).date()
    return trades[to_ist(trades["exit_fill_ts"]).dt.date == today_ist]


def now_ist() -> datetime:
    return datetime.now(tz=UTC).astimezone(IST)


def pnl_color(value: float) -> str:
    return "normal" if value == 0 else ("normal" if value > 0 else "inverse")


def empty_state(title: str, body: str, icon: str = "🌱") -> None:
    """Consistent, friendly 'nothing here yet' block with what to do next."""
    st.info(f"{icon} **{title}**\n\n{body}")


def page_header(title: str, subtitle: str, ctx: AppContext) -> None:
    """Title row with the mode badge and refresh time."""
    left, right = st.columns([4, 1])
    with left:
        st.title(title)
        st.caption(subtitle)
    with right:
        if ctx.can_edit:
            st.success("✏️ Edit mode")
        else:
            st.info("👁 View only")
        st.caption(now_ist().strftime("%d %b %H:%M:%S IST"))
