"""Universes page: the named groups of stocks a strategy can be tested against."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app_common import AppContext, empty_state, page_header


def _render_list(ctx: AppContext, universes: list[dict]) -> None:
    st.subheader("Your universes")

    if not universes:
        empty_state(
            "No universes yet",
            "Run `scripts/refresh_universes.py` to pull NIFTY50/100/500 from "
            "NSE, or build your own below.",
            "🌐",
        )
        return

    frame = pd.DataFrame([
        {
            "Universe": u["name"],
            "Type": "NSE index" if u["source"] == "nse" else "yours",
            "Stocks": u["members"],
            "List dated": u["as_of"] or "—",
        }
        for u in universes
    ])
    st.dataframe(
        frame, use_container_width=True, hide_index=True,
        column_config={
            "List dated": st.column_config.TextColumn(
                help=(
                    "When this membership was captured. Index membership is "
                    "TODAY'S applied to past data, so a backtest over an NSE "
                    "index is flattered by survivorship — stocks join after "
                    "they have already risen."
                ),
            ),
        },
    )
    st.caption(
        "Use any of these in a strategy with `universe: NAME`. NSE indices are "
        "refreshed by `scripts/refresh_universes.py`; yours are edited here."
    )


def _render_builder(ctx: AppContext, universes: list[dict]) -> None:
    st.subheader("Build your own")
    st.caption(
        "A named list of stocks you can point any strategy at — for example "
        "the banks, or the five names you actually follow."
    )

    name = st.text_input(
        "Name", placeholder="MY_BANKS",
        help="Capitals, digits and underscores. This is what you write as "
             "`universe: MY_BANKS` in a strategy.",
    ).strip().upper()

    symbols_raw = st.text_area(
        "Stocks — one per line",
        height=180,
        placeholder="NSE:HDFCBANK\nNSE:ICICIBANK\nNSE:SBIN\nNSE:AXISBANK\nNSE:KOTAKBANK",
        help="EXCHANGE:SYMBOL, exactly as NSE lists it.",
    )
    symbols = [s.strip() for s in symbols_raw.splitlines() if s.strip()]

    existing = {u["name"] for u in universes}
    if name and name in existing:
        source = next(u["source"] for u in universes if u["name"] == name)
        if source == "nse":
            st.error(
                f"**{name}** is an NSE index maintained by the refresh script. "
                "Pick a different name — saving over it would be undone on the "
                "next refresh."
            )
        else:
            st.warning(
                f"**{name}** already exists and will be REPLACED. Editing is "
                "how you remove a stock, so the new list wins entirely."
            )

    if st.button("💾 Save universe", type="primary",
                 disabled=not (ctx.can_edit and name and symbols)):
        try:
            stored, unknown = ctx.store().save_custom_universe(name, symbols)
        except Exception as exc:
            st.error(str(exc))
        else:
            if unknown:
                # Never silently narrowed: a group quietly holding four of the
                # five names you typed would make every result over it subtly
                # wrong with nothing saying so.
                st.warning(
                    f"Saved **{name}** with {stored} stock(s), but these were "
                    f"**not found** and are NOT in the universe: "
                    f"{', '.join(unknown)}"
                )
                st.caption(
                    "Check the spelling, or refresh the instrument list with "
                    "`backfill.py --refresh-instruments`."
                )
            else:
                st.success(f"Saved **{name}** with {stored} stock(s).")
            st.cache_data.clear()

    custom = [u["name"] for u in universes if u["source"] != "nse"]
    if custom and ctx.can_edit:
        with st.expander("🗑 Delete one of yours"):
            victim = st.selectbox("Universe", custom, key="delete_universe")
            st.caption(
                "Any strategy using it will fail to run until pointed "
                "somewhere else — deliberately, rather than silently testing "
                "nothing."
            )
            if st.button("Delete", key="do_delete_universe"):
                try:
                    ctx.store().delete_universe(victim)
                except Exception as exc:
                    st.error(str(exc))
                else:
                    st.success(f"Deleted {victim}.")
                    st.cache_data.clear()
                    st.rerun()


def render(ctx: AppContext) -> None:
    page_header(
        "🌐 Universes",
        "Named groups of stocks. Test one strategy across all of them at once.",
        ctx,
    )

    try:
        universes = ctx.store().list_universes() if ctx.can_edit else []
    except Exception as exc:
        # Read-only mode has no store; anything else is worth showing.
        universes = []
        if ctx.can_edit:
            st.error(f"Could not read universes: {exc}")

    if not ctx.can_edit:
        st.info(
            "Universes are read and edited in **edit mode**, which needs the "
            "service-role key."
        )
        return

    _render_list(ctx, universes)
    st.divider()
    _render_builder(ctx, universes)

    if not any(u["source"] == "nse" for u in universes):
        st.info(
            "No NSE indices stored yet. Run "
            "`.venv\\Scripts\\python.exe scripts/refresh_universes.py` to pull "
            "NIFTY50, NIFTY100, NIFTY200 and NIFTY500."
        )
