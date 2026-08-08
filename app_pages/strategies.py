"""Strategies page: view, build, edit, and go live with one click.

The database is the source of truth, so toggling "Live" here changes what
the scheduled engine trades on its next run — no code push, no redeploy.
Every save is validated by the same schema validator the YAML file uses, so
nothing malformed can ever reach the engine.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st
import yaml

from app_common import AppContext, empty_state, load_all, page_header
from strategy_schema import (
    COMPARISON_OPERATORS,
    CROSS_OPERATORS,
    INDICATOR_OUTPUTS,
    INDICATOR_PARAMS,
    PRICE_SOURCES,
    StrategyConfigError,
    parse_strategy_dict,
    strategy_to_raw,
)

ALL_OPERATORS = sorted(COMPARISON_OPERATORS | CROSS_OPERATORS)
INDICATORS = sorted(INDICATOR_PARAMS)
NEEDS_NO_PARAMS = {i for i, p in INDICATOR_PARAMS.items() if not p}


# ---------------------------------------------------------------------------
# Condition builder widgets
# ---------------------------------------------------------------------------


def _operand_widget(prefix: str, label: str, default_indicator: str = "close") -> dict:
    """Render controls for one operand and return its raw dict."""
    st.caption(label)
    cols = st.columns([2, 3, 2])
    indicator = cols[0].selectbox(
        "Indicator", INDICATORS,
        index=INDICATORS.index(default_indicator),
        key=f"{prefix}_ind", label_visibility="collapsed",
    )

    node: dict = {"indicator": indicator}

    # Parameters (period / fast / slow / ...) shown only when relevant.
    expected = INDICATOR_PARAMS[indicator]
    if expected:
        params = {}
        pcols = cols[1].columns(len(expected))
        for i, (pname, ptypes) in enumerate(expected.items()):
            default = {"period": 14, "fast": 12, "slow": 26, "signal": 9,
                       "std": 2.0, "multiplier": 3.0}.get(pname, 14)
            if float in ptypes:
                params[pname] = pcols[i].number_input(
                    pname, value=float(default), min_value=0.1, step=0.5,
                    key=f"{prefix}_{pname}",
                )
            else:
                params[pname] = int(pcols[i].number_input(
                    pname, value=int(default), min_value=1, step=1,
                    key=f"{prefix}_{pname}",
                ))
        node["params"] = params
    else:
        cols[1].caption("_no settings_")

    # `source` only applies to moving averages (this is how "volume SMA" works).
    if indicator in ("ema", "sma"):
        source = cols[2].selectbox(
            "On", sorted(PRICE_SOURCES), index=sorted(PRICE_SOURCES).index("close"),
            key=f"{prefix}_src", help="Use 'volume' to build a volume average.",
        )
        if source != "close":
            node["source"] = source
    elif indicator in INDICATOR_OUTPUTS:
        node["output"] = cols[2].selectbox(
            "Output", list(INDICATOR_OUTPUTS[indicator]), key=f"{prefix}_out",
        )
    return node


def _condition_builder(prefix: str, n_key: str, title: str, logic_help: str) -> dict:
    """Render a group of conditions and return the raw group dict."""
    st.markdown(f"**{title}**")
    logic = st.radio(
        "Combine with", ["all", "any"], horizontal=True, key=f"{prefix}_logic",
        format_func=lambda v: "ALL must be true (AND)" if v == "all" else "ANY can be true (OR)",
        help=logic_help,
    )
    count = st.number_input(
        "Number of conditions", 1, 5, value=st.session_state.get(n_key, 1),
        key=n_key, step=1,
    )

    items = []
    for i in range(int(count)):
        with st.container(border=True):
            st.markdown(f"Condition {i + 1}")
            left = _operand_widget(f"{prefix}_{i}_L", "Left side")
            op_col, mode_col = st.columns([1, 2])
            operator = op_col.selectbox(
                "Operator", ALL_OPERATORS, key=f"{prefix}_{i}_op",
                help="crosses_above / crosses_below fire ONLY on the candle "
                     "where the lines cross — not on every candle after.",
            )
            compare_mode = mode_col.radio(
                "Compare against", ["a fixed number", "another indicator"],
                horizontal=True, key=f"{prefix}_{i}_mode",
            )
            node = dict(left)
            node["operator"] = operator
            if compare_mode == "a fixed number":
                node["value"] = st.number_input(
                    "Value", value=50.0, step=1.0, key=f"{prefix}_{i}_val",
                )
            else:
                node["compare_to"] = _operand_widget(
                    f"{prefix}_{i}_R", "Right side", default_indicator="sma"
                )
            items.append(node)
    return {logic: items}


# ---------------------------------------------------------------------------
# Builder form
# ---------------------------------------------------------------------------


def _builder(ctx: AppContext) -> None:
    st.markdown(
        "Build a strategy with the controls below. It is validated the moment "
        "you save, and the scheduled engine picks it up on its next run."
    )

    c1, c2, c3, c4 = st.columns(4)
    name = c1.text_input("Name", value="MY-STRATEGY-v1",
                         help="Unique. Used as the key for its trades.")
    timeframe = c2.selectbox("Timeframe", ["15m", "30m", "60m", "day"],
                             help="Nothing faster than 15m is supported.")
    position_type = c3.selectbox("Direction", ["long", "short"])
    max_cycles = c4.number_input("Max round-trips/day", 1, 20, value=2)

    instruments_raw = st.text_area(
        "Instruments (one per line)", value="NSE:RELIANCE\nNSE:TCS",
        help="EXCHANGE:SYMBOL exactly as on the exchange, e.g. NSE:RELIANCE.",
        height=90,
    )

    st.divider()
    entry = _condition_builder(
        "entry", "entry_n", "📥 Entry rules — when to OPEN a position",
        "ALL = every condition must hold. ANY = one is enough.",
    )
    st.divider()
    exit_ = _condition_builder(
        "exit", "exit_n", "📤 Exit rules — when to CLOSE (besides stop/target)",
        "Usually ANY, so any one exit signal closes the trade.",
    )
    st.divider()

    st.markdown("**🛡 Risk & sizing**")
    r1, r2, r3 = st.columns(3)
    stop = r1.number_input("Stop loss %", 0.1, 50.0, value=0.7, step=0.1)
    target = r2.number_input("Target %", 0.1, 50.0, value=1.5, step=0.1)
    qty = r3.number_input("Quantity", 1, 100000, value=10, step=1)

    # The single most common beginner mistake: a flat cost that swallows the
    # whole target. Warn with real numbers before they ever run it.
    approx_price = st.number_input(
        "Approx. share price (for the cost check below)", 1.0, 1_000_000.0,
        value=1500.0, step=50.0,
        help="Only used for the sanity check — not stored in the strategy.",
    )
    gross = approx_price * qty * target / 100
    if gross < 30 * 2:
        st.error(
            f"⚠️ **Costs will eat this strategy.** A winning trade earns about "
            f"₹{gross:,.0f}, but every round-trip costs ₹30. "
            f"Raise **Quantity** or **Target %** until the win is comfortably "
            f"above ₹150 (5× cost). At the current settings it "
            f"{'barely breaks even' if gross > 30 else 'LOSES money even when it wins'}."
        )
    else:
        st.success(
            f"✅ Cost check: a winning trade earns ≈ ₹{gross:,.0f} versus the "
            f"₹30 round-trip cost ({gross/30:.0f}× cost)."
        )

    doc = {
        "name": name.strip(),
        "enabled": False,  # always created paused; you enable it deliberately
        "position_type": position_type,
        "timeframe": timeframe,
        "instruments": [i.strip() for i in instruments_raw.splitlines() if i.strip()],
        "entry": entry,
        "exit": exit_,
        "risk": {"stop_loss_pct": float(stop), "target_pct": float(target)},
        "sizing": {"type": "fixed_quantity", "quantity": int(qty)},
        "max_cycles_per_day": int(max_cycles),
    }

    with st.expander("👀 Preview the generated configuration"):
        st.code(yaml.safe_dump(doc, sort_keys=False), language="yaml")

    col_a, col_b = st.columns([1, 3])
    if col_a.button("✅ Validate", use_container_width=True):
        try:
            parse_strategy_dict(doc)
            st.success("Valid! This strategy is safe to save.")
        except StrategyConfigError as exc:
            st.error(f"Not valid yet: {exc}")

    if col_b.button("💾 Save strategy (created paused)", type="primary",
                    use_container_width=True, disabled=not ctx.can_edit):
        try:
            saved = ctx.store().save_strategy_document(doc)
        except StrategyConfigError as exc:
            st.error(f"Cannot save — {exc}")
        except Exception as exc:
            st.error(f"Save failed: {exc}")
        else:
            st.success(
                f"Saved **{saved.name}** (paused). Backtest it first, then "
                "switch it Live in the list above."
            )
            st.cache_data.clear()


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------


def render(ctx: AppContext) -> None:
    page_header(
        "🧠 Strategies",
        "The database is the source of truth — toggling Live here changes what "
        "the engine trades on its next run.",
        ctx,
    )

    strategies = load_all(ctx)["strategies"]

    tab_list, tab_new = st.tabs(["📋 Your strategies", "➕ Build a new one"])

    with tab_list:
        if strategies.empty:
            empty_state(
                "No strategies stored yet",
                "Use **Build a new one** to create your first, or run the paper "
                "engine once to seed the defaults from `strategies.yaml`.",
                "🧠",
            )
        else:
            st.caption(
                f"{int(strategies['enabled'].sum())} live of {len(strategies)}. "
                "A live strategy is evaluated every 15 minutes during market hours."
            )
            for _, row in strategies.iterrows():
                live = bool(row["enabled"])
                icon = "🟢" if live else "⏸"
                with st.expander(
                    f"{icon} **{row['name']}** · {row['timeframe']} · {row['position_type']}"
                    + ("  —  LIVE" if live else "  —  paused")
                ):
                    definition = row.get("definition") or {}
                    if isinstance(definition, str):
                        definition = json.loads(definition)

                    meta = st.columns(4)
                    meta[0].metric("Timeframe", row["timeframe"])
                    meta[1].metric("Direction", row["position_type"])
                    risk = definition.get("risk", {})
                    meta[2].metric("Stop / Target",
                                   f"{risk.get('stop_loss_pct','?')}% / {risk.get('target_pct','?')}%")
                    meta[3].metric("Quantity",
                                   definition.get("sizing", {}).get("quantity", "?"))

                    st.write("**Instruments:** " + ", ".join(definition.get("instruments", [])))
                    st.code(yaml.safe_dump(definition, sort_keys=False), language="yaml")

                    a, b = st.columns([1, 1])
                    if live:
                        if a.button("⏸ Pause", key=f"pause_{row['name']}",
                                    disabled=not ctx.can_edit, use_container_width=True):
                            ctx.store().set_strategy_enabled(row["name"], False)
                            st.cache_data.clear()
                            st.rerun()
                    else:
                        if a.button("🚀 Go live", key=f"live_{row['name']}",
                                    type="primary", disabled=not ctx.can_edit,
                                    use_container_width=True):
                            ctx.store().set_strategy_enabled(row["name"], True)
                            st.cache_data.clear()
                            st.rerun()
                    if b.button("🗑 Delete", key=f"del_{row['name']}",
                                disabled=not ctx.can_edit, use_container_width=True,
                                help="Past trades are kept."):
                        ctx.store().delete_strategy(row["name"])
                        st.cache_data.clear()
                        st.rerun()

            if not ctx.can_edit:
                st.caption(
                    "👁 View-only mode: run the app locally to change strategies."
                )

    with tab_new:
        if not ctx.can_edit:
            st.warning(
                "Building strategies needs **edit mode**. Run the app on your "
                "own machine:\n\n"
                "```\ncd \"C:\\Users\\Aniket\\Personal projects\\Trading tool\"\n"
                ".venv\\Scripts\\streamlit.exe run dashboard.py\n```"
            )
        _builder(ctx)
