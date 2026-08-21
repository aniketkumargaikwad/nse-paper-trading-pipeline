"""System page: engine runs, configuration, health checks, upgrade path."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app_common import AppContext, load_all, page_header, to_ist


def render(ctx: AppContext) -> None:
    page_header("⚙️ System", "Engine activity, configuration, and health.", ctx)

    data = load_all(ctx)
    audits = data["run_audit"]

    # ---- current configuration ---------------------------------------------
    st.subheader("Configuration")
    c1, c2, c3 = st.columns(3)
    c1.metric("App mode", ctx.mode_label)
    c1.caption(
        "Edit mode is on because a service-role key is configured."
        if ctx.can_edit else
        "Read-only: an anon key is configured. This is the safe setting for a "
        "publicly reachable app."
    )

    # Read the CONFIGURATION rather than inferring from an audit row. The
    # previous version searched run_audit for a "provider" key that only the
    # paper engine ever wrote - and the paper engine had never run - so this
    # reported "unknown" permanently regardless of how the app was set up.
    # What provider the app is configured to use is a fact available right
    # now; it should not depend on something having run.
    from config import get_settings

    try:
        settings = get_settings()
        configured = settings.data_provider
        store_mode = settings.candle_store
    except Exception:                                   # noqa: BLE001
        configured, store_mode = "unknown", "unknown"

    c2.metric("Data provider", configured)

    # What the last run ACTUALLY used, which can differ from configuration if
    # the engine runs elsewhere (a scheduled job with its own environment).
    last_used = None
    if not audits.empty:
        for _, row in audits.iterrows():
            details = row.get("details")
            if isinstance(details, str):
                try:
                    details = json.loads(details)
                except ValueError:
                    details = None
            if isinstance(details, dict) and details.get("provider"):
                last_used = details["provider"]
                break

    if last_used and last_used != configured:
        c2.caption(
            f"⚠️ configured here, but the last run used **{last_used}** — the "
            "engine may have a different environment."
        )
    else:
        c2.caption(f"candles: {store_mode}")

    c3.metric("Supabase", "connected")
    c3.caption(ctx.url.replace("https://", ""))

    if ctx.can_edit:
        st.warning(
            "✏️ **Edit mode uses a service-role key.** Keep this configuration "
            "local. Do not put a service-role key in a publicly reachable "
            "Streamlit Cloud app — use the anon key there."
        )

    st.divider()

    # ---- engine runs --------------------------------------------------------
    st.subheader("Engine runs")
    if audits.empty:
        st.warning(
            "**No runs recorded.** The scheduled engine has never executed.\n\n"
            "Enable it with `gh workflow enable paper-engine`, or trigger one "
            "manually from the repo's **Actions → paper-engine → Run workflow**."
        )
    else:
        a = audits.copy()
        a["Started (IST)"] = to_ist(a["run_started_at"])

        counts = a["status"].value_counts()
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Runs recorded", len(a))
        m2.metric("🟢 ok", int(counts.get("ok", 0)))
        m3.metric("🟡 skipped", int(counts.get("skipped", 0)))
        m4.metric("🔴 error", int(counts.get("error", 0)))

        st.caption(
            "`skipped` is NORMAL outside market hours and on holidays — it "
            "means the engine correctly did nothing."
        )

        only_problems = st.checkbox("Show only errors", value=False)
        view = a[a["status"] == "error"] if only_problems else a

        st.dataframe(
            view[["Started (IST)", "run_type", "status", "reason", "details"]]
            .rename(columns={
                "run_type": "Type", "status": "Status", "reason": "Reason",
                "details": "Details",
            }),
            use_container_width=True, hide_index=True, height=380,
        )

        errors = a[a["status"] == "error"]
        if not errors.empty:
            with st.expander(f"🔴 {len(errors)} failed run(s) — likely causes"):
                st.write(
                    "- `Missing required environment variable(s)` → add the two "
                    "Supabase secrets in the repo's **Settings → Secrets and "
                    "variables → Actions**.\n"
                    "- `No Kite access token` → only affects the paid Kite "
                    "provider; run `login.py` that morning.\n"
                    "- `Yahoo Finance failed` → the free feed throttled; the "
                    "next run retries automatically."
                )
                st.dataframe(
                    errors[["Started (IST)", "reason"]].head(20),
                    use_container_width=True, hide_index=True,
                )

    st.divider()

    # ---- roadmap / upgrade --------------------------------------------------
    st.subheader("Upgrading later")
    up1, up2 = st.columns(2)
    with up1:
        st.markdown(
            "**Paid Kite Connect data**\n\n"
            "Gives years of 15-minute history and F&O symbols "
            "(₹500/30 days, plus a daily login).\n\n"
            "1. Create a *Connect* app at developers.kite.trade\n"
            "2. Add `KITE_API_KEY` / `KITE_API_SECRET` secrets\n"
            "3. Add repo **variable** `DATA_PROVIDER=kite`\n\n"
            "No code changes — see the operating guide."
        )
    with up2:
        st.markdown(
            "**One-click live deployment**\n\n"
            "Not built, and deliberately so. This system is "
            "**paper/simulation only** — nothing here can place a real order.\n\n"
            "Real automated order placement is a separate, regulated problem "
            "(broker authorisation, risk limits, compliance). It belongs in a "
            "separate system, not bolted onto this research tool."
        )
