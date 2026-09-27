"""Home: AI summary, KPIs, look-through exposure map, ranked alerts (spec §8.1)."""
import streamlit as st

from ui import charts, data
from ui.components import (
    ai_box, alert_row, fmt_bp, fmt_pct, html_block, page_header, plotly_selected_iso2, set_country,
)

page_header("Hello John")
hv = data.home_view()
k = hv["kpis"]

ai_box("home", "Refresh with AI", auto=True, title="Your week in brief")

c1, c2, c3, c4 = st.columns(4)
with c1:
    with st.container(border=True, height="stretch"):
        st.metric("Portfolio value", f"${k['value_k']:,.0f}k")
        st.caption(f"{k['n_etfs']} ETFs · {k['n_bonds']} bonds")
with c2:
    with st.container(border=True, height="stretch"):
        st.metric("Portfolio yield", fmt_pct(k["yield"]),
                  delta=f"{fmt_bp(k['yield_pickup_bp'])} vs US 10Y ({fmt_pct(k['ust10'])})", delta_color="off",
                  delta_arrow="off")
        st.caption("Amount-weighted yield of every position.")
with c3:
    with st.container(border=True, height="stretch"):
        st.metric("Risk profile", f"{k['risk']:.0f} · {k['band']}" if k["risk"] is not None else "n/a",
                  help="Book-weighted average of country risk (0 safe → 100 distress).")
        st.caption(f"{k['pct_on_alert']:.0f}% of the book in countries on alert (risk ≥ 55)")
        if st.button("See portfolio", icon=":material/arrow_forward:", key="go_pf", type="tertiary"):
            st.switch_page("pages/portfolio.py")
with c4:
    with st.container(border=True, height="stretch"):
        st.metric("AI opportunities", len(k["opportunities"]),
                  help="Top relative-value opportunities where your book has less than 2%.")
        st.caption(", ".join(k["opportunity_names"]) or "None outside your current book")
        if k["opportunities"] and st.button("See in Markets", icon=":material/arrow_forward:", key="go_mk",
                                            type="tertiary"):
            set_country(k["opportunities"][0])
            st.session_state["focus_opportunity"] = k["opportunities"][0]
            st.switch_page("pages/markets.py")

left, right = st.columns([3, 2], gap="medium")
with left:
    with st.container(border=True):
        st.markdown("**My map** · real exposure after looking through the ETFs")
        fig = charts.exposure_map(hv["map"], selected=st.session_state.get("selected_country"))
        event = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="home_map",
                                config={"displayModeBar": False, "scrollZoom": False})
        st.markdown(charts.legend_html({b: c for b, c in charts.band_colors().items() if b != "n/a"}
                                       | {"Not held": charts.MUTED}), unsafe_allow_html=True)
        st.caption("Bubble size = share of the book. Select a country to open its risk profile.")
        picked = plotly_selected_iso2(event, "home_map")
        if picked:
            set_country(picked)
            st.switch_page("pages/risk_map.py")

with right:
    with st.container(border=True):
        st.markdown("**Alerts** · what moved in the countries your clients own")
        if not hv["alerts"]:
            st.caption("No country above 1% of the book.")
        rows = [alert_row(a["tag"], f"{a['name']}", a["driver"], a["moves"], a["weight"]) for a in hv["alerts"]]
        html_block(rows)
        st.caption("Ranked by weight × size of the move. Changes over one month.")
