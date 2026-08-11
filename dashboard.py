"""Trading workbench — the single app for strategy, backtest, and paper trading.

Run locally (full edit mode, from the project folder):

    .venv\\Scripts\\streamlit.exe run dashboard.py        (Windows)
    .venv/bin/streamlit run dashboard.py                  (Mac/Linux)

Deploy read-only: point Streamlit Community Cloud at this same file and set
SUPABASE_URL + SUPABASE_ANON_KEY in the app's Secrets.

PAGES
  Overview       health, headline P&L, what to do next
  Strategies     view / build / edit; one click to go live or pause
  Backtest       run tests on history; read kill rules
  Paper trading  open positions, equity curve, today's trades
  Trade log      full filterable history + CSV export
  System         engine runs, configuration, upgrade path

MODES (detected from the Supabase key you configure)
  anon key         -> view only. Safe for a public URL.
  service-role key -> edit mode (build strategies, run backtests).
                      Intended for LOCAL use; never put this key in a
                      publicly reachable deployment.

Everything here is simulation. No screen in this app can place a real order.
"""

from __future__ import annotations


def _running_under_streamlit() -> bool:
    try:
        from streamlit import runtime

        return runtime.exists()
    except Exception:
        return False


def _app() -> None:  # pragma: no cover — exercised by `streamlit run`
    import streamlit as st

    st.set_page_config(
        page_title="Trading Workbench",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    from app_common import get_context, require_login

    # Before ANY data is fetched or rendered: a deployed dashboard carries
    # a service-role key, so the gate has to come first.
    if not require_login():
        st.stop()

    ctx = get_context()
    if ctx is None:
        st.stop()

    from app_pages import (
        backtest_page,
        overview,
        paper_trading,
        strategies,
        system,
        trade_log,
    )

    # st.navigation renders the sidebar. Each entry wraps a page module so
    # the shared context is built exactly once per run. `url_path` MUST be
    # given explicitly: Streamlit infers the path from the callable's name,
    # and every lambda is called "<lambda>", which collides.
    def page(module, title: str, icon: str, path: str, default: bool = False):
        return st.Page(
            lambda: module.render(ctx),
            title=title, icon=icon, url_path=path, default=default,
        )

    pages = [
        page(overview, "Overview", "📊", "overview", default=True),
        page(strategies, "Strategies", "🧠", "strategies"),
        page(backtest_page, "Backtest", "🔬", "backtest"),
        page(paper_trading, "Paper trading", "📈", "paper-trading"),
        page(trade_log, "Trade log", "📜", "trade-log"),
        page(system, "System", "⚙️", "system"),
    ]

    with st.sidebar:
        st.markdown("### 📈 Trading Workbench")
        st.caption(
            "**Paper trading / research only.** No real orders are placed "
            "anywhere in this system."
        )
        st.divider()

    nav = st.navigation(pages)

    with st.sidebar:
        st.divider()
        st.caption(f"Mode: **{ctx.mode_label}**")
        if not ctx.can_edit:
            st.caption(
                "Run locally to build strategies and launch backtests."
            )
        if st.button("🔄 Refresh data", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    nav.run()


if _running_under_streamlit():  # pragma: no cover
    _app()
elif __name__ == "__main__":
    print(
        "This is a Streamlit app. Run it with:\n"
        "  .venv\\Scripts\\streamlit.exe run dashboard.py"
    )
