"""Strategies page: view, build, edit, and go live with one click.

The database is the source of truth, so toggling "Live" here changes what
the scheduled engine trades on its next run — no code push, no redeploy.
Every save is validated by the same schema validator the YAML file uses, so
nothing malformed can ever reach the engine.
"""

from __future__ import annotations

import json

import pandas as pd
from pathlib import Path

import streamlit as st
import yaml

from app_common import AppContext, empty_state, load_all, page_header, to_ist
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
    notional = r3.number_input(
        "Rupees per trade", 1000, 100_000_000, value=100_000, step=10_000,
        help=(
            "Trade size in rupees, not shares. Quantity is worked out per "
            "symbol as this amount divided by the share price, so trading "
            "costs weigh the same on a cheap stock as an expensive one."
        ),
    )

    trail_on = st.checkbox(
        "Add a trailing stop",
        help="Follows the price in your favour and never loosens.",
    )
    trail = None
    if trail_on:
        trail = st.number_input("Trailing stop %", 0.1, 50.0, value=0.5, step=0.1)

    st.markdown("**🕘 Session (optional)**")
    s1, s2 = st.columns(2)
    square_off_on = s1.checkbox(
        "Square off intraday",
        help=(
            "Close any open position before the session ends. Without this a "
            "position can be held overnight, exposed to gaps your stop cannot "
            "protect against."
        ),
    )
    square_off = s2.text_input("Square off at (IST)", value="15:15",
                               disabled=not square_off_on)

    # The single most common beginner mistake: a flat cost that swallows the
    # whole target. Warn with real numbers before they ever run it.
    approx_price = st.number_input(
        "Approx. share price (for the cost check below)", 1.0, 1_000_000.0,
        value=1500.0, step=50.0,
        help="Only used for the sanity check — not stored in the strategy.",
    )
    # Quantity is derived exactly as the engines derive it, so the number
    # shown here is the number that will actually be traded.
    est_qty = int(notional // approx_price)
    gross = approx_price * est_qty * target / 100
    st.caption(
        f"At ₹{approx_price:,.0f} a share, ₹{notional:,.0f} buys "
        f"**{est_qty:,} shares**."
    )
    if est_qty < 1:
        st.error(
            "⚠️ **Rupees per trade is below the share price**, so this "
            "strategy would buy zero shares and every signal would be "
            "skipped. Raise it above the share price."
        )
    if gross < 30 * 2:
        st.error(
            f"⚠️ **Costs will eat this strategy.** A winning trade earns about "
            f"₹{gross:,.0f}, but every round-trip costs ₹30. "
            f"Raise **Rupees per trade** or **Target %** until the win is comfortably "
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
        "risk": {
            "stop_loss": {"type": "percent", "value": float(stop)},
            "target": {"type": "percent", "value": float(target)},
            **(
                {"trailing_stop": {"type": "percent", "value": float(trail)}}
                if trail is not None
                else {}
            ),
        },
        "sizing": {"type": "notional", "notional_per_trade": float(notional)},
        **(
            {"session": {"square_off": square_off.strip()}}
            if square_off_on and square_off.strip()
            else {}
        ),
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
        except Exception as exc:
            st.error(f"Save failed: {exc}")
        else:
            _report_save(saved, paused_hint=True)
            st.cache_data.clear()


# ---------------------------------------------------------------------------
# Page
def _report_save(saved, *, paused_hint: bool) -> None:
    """Show the outcome of a save, including the draft case.

    A strategy that cannot run must never look like one that can, so a draft
    is reported as a warning with its errors listed — not as a success.
    """
    if saved.is_valid:
        message = f"Saved **{saved.name}**"
        if paused_hint:
            message += (
                " (paused). Backtest it first, then switch it Live in the "
                "list above."
            )
        st.success(message)
        return

    st.warning(
        f"Saved **{saved.name}** as a **draft**. It is stored so you can fix "
        "it, but it will not run until these are resolved:"
    )
    for message in saved.errors:
        st.markdown(f"- `{message}`")
    st.caption(
        "Paste those messages back into ChatGPT and it will usually correct "
        "itself."
    )


def render_paste_box(ctx) -> None:
    """Paste a strategy written elsewhere — the Pine-Script-style workflow.

    The format reference is carried inline so the loop is self-contained:
    copy it into ChatGPT, paste what comes back here.
    """
    st.subheader("📋 Paste a strategy")
    st.caption(
        "Wrote one in ChatGPT? Paste the YAML here. Copy the format reference "
        "below into the chat first, so it knows what this system accepts."
    )

    format_doc_path = Path(__file__).resolve().parent.parent / "docs" / "STRATEGY_FORMAT.md"
    with st.expander("📖 Format reference — copy this into ChatGPT first"):
        if format_doc_path.exists():
            st.code(format_doc_path.read_text(encoding="utf-8"), language="markdown")
        else:
            st.warning(
                "docs/STRATEGY_FORMAT.md is missing. Generate it with:  "
                "python scripts/gen_strategy_format_doc.py"
            )

    text = st.text_area(
        "Strategy YAML", height=320, key="paste_yaml",
        placeholder="version: 2\nstrategies:\n  - name: my-strategy\n    ...",
    )
    if not st.button("✅ Validate and save", key="paste_save",
                     type="primary", disabled=not ctx.can_edit):
        return
    if not text.strip():
        st.warning("Nothing to save yet — paste a strategy above.")
        return

    try:
        saved = ctx.store().save_strategy_text(text)
    except Exception as exc:
        # Unparseable YAML has no name to key a draft on, so it lands here
        # rather than becoming a draft. The message says what to fix.
        st.error(str(exc))
        return

    _report_save(saved, paused_hint=False)
    st.cache_data.clear()


# ---------------------------------------------------------------------------


def _describe_stop(spec) -> str:
    """Render either stop form. Mirrors StopSpec.describe for raw dicts."""
    if not isinstance(spec, dict):
        return "?"
    if spec.get("type") == "atr":
        return f"{spec.get('multiplier', '?')}x ATR({spec.get('period', '?')})"
    return f"{spec.get('value', '?')}%"


def _describe_risk(risk) -> str:
    if not isinstance(risk, dict):
        return "?"
    text = f"{_describe_stop(risk.get('stop_loss'))} / {_describe_stop(risk.get('target'))}"
    if risk.get("trailing_stop"):
        text += f" (trail {_describe_stop(risk['trailing_stop'])})"
    return text


def _describe_sizing(sizing) -> str:
    if not isinstance(sizing, dict):
        return "?"
    if sizing.get("type") == "notional":
        return f"₹{sizing.get('notional_per_trade', 0):,.0f}/trade"
    return f"{sizing.get('quantity', '?')} shares"


def _scope_of(definition: dict) -> str:
    """What a strategy trades, in one cell.

    A universe strategy has no `instruments` key at all, so asking for one
    yields an empty string and the reader cannot tell an empty list from a
    universe of 500.
    """
    if definition.get("universe"):
        return f"universe {definition['universe']}"
    instruments = definition.get("instruments") or []
    if not instruments:
        return "—"
    if len(instruments) <= 2:
        return ", ".join(instruments)
    return f"{len(instruments)} instruments"


def _latest_verdicts(runs: pd.DataFrame) -> dict[str, dict]:
    """Each strategy's most recent backtest verdict, keyed by name."""
    if runs.empty:
        return {}
    ordered = runs.sort_values("created_at", ascending=False)
    return {
        name: group.iloc[0].to_dict()
        for name, group in ordered.groupby("strategy_name")
    }


def _render_grid(strategies: pd.DataFrame, runs: pd.DataFrame) -> None:
    """One row per strategy: what it is, and how it last tested.

    The list below shows strategies one expander at a time, which answers
    "what is this strategy" but never "which of these is worth my attention".
    That needs them side by side, with the backtest verdict attached — knowing
    a strategy exists is not the same as knowing it works.
    """
    verdicts = _latest_verdicts(runs)
    rows = []
    for _, r in strategies.iterrows():
        definition = r.get("definition") or {}
        if isinstance(definition, str):
            definition = json.loads(definition)
        is_draft = r.get("status") == "draft"
        v = verdicts.get(r["name"])

        rows.append({
            "State": (
                "📝 draft" if is_draft
                else ("🟢 live" if bool(r["enabled"]) else "⏸ paused")
            ),
            "Strategy": r["name"],
            "TF": r.get("timeframe") or "—",
            "Trades": (
                "—" if is_draft else _scope_of(definition)
            ),
            # Backtest columns are blank rather than zero when a strategy has
            # never been tested: untested and tested-badly are different facts,
            # and a 0 would present the first as the second.
            "Net ₹": float(v["net_pnl"]) if v else None,
            "Symbols +": (
                f"{int(v['symbols_profitable'])}/{int(v['symbols_resolved'])}"
                if v else "—"
            ),
            "Sharpe": (
                float(v["sharpe_daily"])
                if v and v.get("sharpe_daily") is not None else None
            ),
            "Passed": bool(v["passed_kill_rules"]) if v else False,
            "Tested": (
                to_ist(pd.Series([v["created_at"]])).iloc[0].strftime("%d %b %H:%M")
                if v else "never"
            ),
        })

    grid = pd.DataFrame(rows)
    st.dataframe(
        grid, use_container_width=True, hide_index=True,
        column_config={
            "Net ₹": st.column_config.NumberColumn(
                format="%.0f",
                help="From the most recent backtest. Blank means never tested.",
            ),
            "Sharpe": st.column_config.NumberColumn(
                format="%.2f",
                help=(
                    "Return per unit of risk. Blank means never tested, or not "
                    "measurable from the data available."
                ),
            ),
            "Symbols +": st.column_config.TextColumn(
                help=(
                    "How many symbols the strategy actually made money on. A "
                    "large profit earned on very few symbols usually means one "
                    "outlier carried it."
                ),
            ),
            "Passed": st.column_config.CheckboxColumn(
                disabled=True, help="Cleared every robustness rule."
            ),
        },
    )

    untested = sum(1 for r in rows if r["Tested"] == "never")
    if untested:
        st.caption(
            f"{untested} strateg{'y has' if untested == 1 else 'ies have'} never "
            "been backtested. Run one from the **Backtest** page before trusting "
            "anything it does."
        )


def render(ctx: AppContext) -> None:
    page_header(
        "🧠 Strategies",
        "The database is the source of truth — toggling Live here changes what "
        "the engine trades on its next run.",
        ctx,
    )

    data = load_all(ctx)
    strategies = data["strategies"]
    runs = data.get("backtest_runs", pd.DataFrame())

    tab_list, tab_new, tab_paste = st.tabs(
        ["📋 Your strategies", "➕ Build a new one", "📋 Paste one"]
    )

    with tab_list:
        if strategies.empty:
            empty_state(
                "No strategies stored yet",
                "Use **Build a new one** to create your first, or run the paper "
                "engine once to seed the defaults from `strategies.yaml`.",
                "🧠",
            )
        else:
            live_count = int(strategies["enabled"].fillna(False).sum())
            st.caption(
                f"{live_count} live of {len(strategies)}. "
                "A live strategy is evaluated every 15 minutes during market hours."
            )
            _render_grid(strategies, runs)
            st.divider()
            st.caption("Open a strategy for its full definition and controls.")
            for _, row in strategies.iterrows():
                # A draft is stored but cannot run. It must never be presented
                # the same way as a strategy that can.
                is_draft = row.get("status") == "draft"
                live = bool(row["enabled"]) and not is_draft
                icon = "📝" if is_draft else ("🟢" if live else "⏸")
                if is_draft:
                    heading = f"{icon} **{row['name']}** — DRAFT, will not run"
                else:
                    heading = (
                        f"{icon} **{row['name']}** · {row['timeframe']} · "
                        f"{row['position_type']}"
                        + ("  —  LIVE" if live else "  —  paused")
                    )
                with st.expander(heading):
                    if is_draft:
                        st.warning(
                            "This strategy could not be validated, so it is "
                            "stored as a draft and is skipped by every engine. "
                            "Fix these and paste it again:"
                        )
                        for message in (row.get("validation_errors") or []):
                            st.markdown(f"- `{message}`")
                        if row.get("raw_source"):
                            with st.expander("What you pasted"):
                                st.code(row["raw_source"], language="yaml")
                        continue
                    definition = row.get("definition") or {}
                    if isinstance(definition, str):
                        definition = json.loads(definition)

                    meta = st.columns(4)
                    meta[0].metric("Timeframe", row["timeframe"])
                    meta[1].metric("Direction", row["position_type"])
                    risk = definition.get("risk", {})
                    meta[2].metric("Stop / Target",
                                   _describe_risk(risk))
                    meta[3].metric("Quantity",
                                   _describe_sizing(definition.get("sizing", {})))

                    if definition.get("universe"):
                        st.write(f"**Universe:** {definition['universe']}")
                    else:
                        st.write(
                            "**Instruments:** "
                            + ", ".join(definition.get("instruments", []))
                        )
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

    with tab_paste:
        render_paste_box(ctx)
