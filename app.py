"""Streamlit entry point: page config, CSS/fonts, navigation, data/admin sidebar, footer."""
from __future__ import annotations

import logging

import streamlit as st

from core import config as C

st.set_page_config(page_title="EM Sovereign Early Warning", page_icon=":material/public:", layout="wide",
                   initial_sidebar_state="collapsed")
logging.basicConfig(level=C.get_settings().log_level)

from ui import data  # noqa: E402  (after set_page_config)
from ui.components import footer, inject_css  # noqa: E402

data.ensure_db()
inject_css()

pages = [
    st.Page("pages/home.py", title="Home", icon=":material/home:", default=True),
    st.Page("pages/markets.py", title="Markets", icon=":material/show_chart:"),
    st.Page("pages/portfolio.py", title="Portfolio", icon=":material/account_balance_wallet:"),
    st.Page("pages/risk_map.py", title="Risk map", icon=":material/public:"),
    st.Page("pages/ask.py", title="Ask the analyst", icon=":material/forum:"),
]
page = st.navigation(pages, position="top")


def _sidebar() -> None:
    settings = C.get_settings()
    with st.sidebar:
        st.subheader("Data")
        st.caption("Gemini: on." if settings.ai_enabled else "Gemini: off (no key), built-in text is used.")
        if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
            from core.fetchers import refresh_all

            with st.spinner("Fetching World Bank, IMF, FX and FRED data…"):
                st.session_state["refresh_report"] = refresh_all()
            data.clear_caches()
        report = st.session_state.get("refresh_report")
        if report:
            for source, r in report.items():
                if source == "Scores":
                    st.caption(f"**Scores:** {r['status']}")
                    continue
                detail = r.get("detail") or f"{r.get('rows', 0)} values"
                st.caption(f"**{source}:** {r.get('status')} · {detail}")
                for err in r.get("errors", [])[:5]:
                    st.caption(f"⚠ {err}")

        with st.expander("Admin: statement text signal"):
            st.caption("Score a central bank, finance ministry or IMF statement (0 calm → 100 distress). "
                       "The latest score feeds the Text signal layer.")
            names = {c["name"]: c["iso2"] for c in C.COUNTRIES}
            country = st.selectbox("Country", list(names), key="ts_country")
            text = st.text_area("Statement", height=150, key="ts_text")
            if st.button("Score statement", disabled=not text.strip()):
                from core.ai import score_statement

                with st.spinner("Scoring…"):
                    sig, used = score_statement(names[country], text)
                data.clear_caches()
                st.metric("Text signal", f"{sig.score}/100", help="AI" if used else "Keyword scan fallback")
                st.caption(("Gemini: " if used else "Keyword scan: ") + sig.summary)
                for p in sig.phrases:
                    st.caption(f"“{p}”")


_sidebar()
page.run()
footer()
