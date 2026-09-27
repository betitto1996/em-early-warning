"""Markets: benchmarks, AI brief, 'Paid for the risk?', top opportunities, map, 10Y table (spec §8.2)."""
import pandas as pd
import streamlit as st

from core import config as C
from ui import charts, data
from ui.components import (
    ai_box, band_pill, fmt_bp, fmt_pct, html_block, isnum, page_header, pager, plotly_selected_iso2, set_country,
    signal_pill,
)

page_header("Markets")

# 1. Benchmarks ---------------------------------------------------------------
macro = data.macro()
cols = st.columns(len(macro))
for col, m in zip(cols, macro):
    with col:
        with st.container(border=True, height="stretch"):
            st.metric(m["label"], m["display"], delta=m["change_text"], delta_color="off", delta_arrow="off")
            st.caption(m["explanation"] or "")
if macro and macro[0].get("as_of"):
    st.caption(f"As of {macro[0]['as_of']} · source: {macro[0]['source']}")

# 2. AI summary ----------------------------------------------------------------
ai_box("markets", "Write this week's brief with AI")

# 3. Scatter + top opportunities -------------------------------------------------
rv = data.relative_value()
table: pd.DataFrame = rv["table"]
by_iso = table.set_index("iso2")
tops = rv["top_opportunities"]
lt = data.lookthrough()
book_w = lt["weights"]
via = dict(zip(lt["table"]["iso2"], lt["table"]["via"]))

focus = st.session_state.pop("focus_opportunity", None)
if focus in tops:
    st.session_state["opp_idx"] = tops.index(focus)

left, right = st.columns([3, 2], gap="medium")
with left:
    with st.container(border=True, height="stretch"):
        st.markdown("**Paid for the risk?** · spread vs risk score across the 18 countries")
        fig = charts.rv_scatter(table, rv["a"], rv["b"], selected=st.session_state.get("selected_country"))
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="rv_scatter",
                             config={"displayModeBar": False})
        picked = plotly_selected_iso2(ev, "rv_scatter")
        if picked:
            set_country(picked)
            if picked in tops:
                st.session_state["opp_idx"] = tops.index(picked)
            st.rerun()
        st.caption("Dashed line: spread that countries with similar risk usually pay (fit on countries below 70). "
                   "Cheap = more than 25% above the line; Rich = more than 20% below.")

with right:
    with st.container(border=True, height="stretch"):
        st.markdown("**Top opportunities** · where the yield pays most for the risk")
        if not tops:
            st.caption("No relative-value data yet.")
        else:
            idx = pager("opp_idx", len(tops))
            iso2 = tops[idx]
            r = by_iso.loc[iso2]
            st.subheader(r["name"])
            html_block([signal_pill(r["signal"]), band_pill(r["risk"], r["band"])])
            st.markdown(r["rv_sentence"])
            m1, m2 = st.columns(2)
            m1.metric("USD 10Y", fmt_pct(r["usd_10y"]), help="US 10Y + USD spread")
            m2.metric("USD spread", fmt_bp(r["spread_bp"], signed=False),
                      delta=f"{r['spread_1m_bp']:+.0f} bp 1M" if isnum(r["spread_1m_bp"]) else None,
                      delta_color="inverse")
            m3, m4 = st.columns(2)
            m3.metric("Local 10Y", fmt_pct(r["local_10y"]))
            m4.metric("Real 10Y", fmt_pct(r["real_10y"]),
                      help=f"Local 10Y minus inflation ({fmt_pct(r['inflation'], 1)})")
            st.markdown(f"**What's moving it:** {r['driver']}".replace("$", "\\$"))
            w = book_w.get(iso2, 0.0)
            st.markdown(f"**Your clients:** {w:.1f}% of the book" + (f", {via[iso2]}" if iso2 in via else " (not held)"))
            b1, b2 = st.columns(2)
            if b1.button("Full risk profile", key="opp_profile", width="stretch"):
                set_country(iso2)
                st.switch_page("pages/risk_map.py")
            if b2.button("See in portfolio", key="opp_pf", width="stretch"):
                set_country(iso2)
                st.switch_page("pages/portfolio.py")

# 4. Markets map ---------------------------------------------------------------
with st.container(border=True):
    st.markdown("**Markets map**")
    mode = st.segmented_control(
        "Colour by", ["Risk", "Opportunities", "Local 10Y", "Real 10Y", "USD spread", "1M spread change"],
        default="Risk", key="mk_map_mode", label_visibility="collapsed") or "Risk"
    mdf = table.copy()
    if mode == "Risk":
        mdf["cat"] = mdf["band"].fillna("n/a")
        colors = charts.band_colors()
        mdf["hover"] = [f"{n}: risk {r:.0f} · {b} · {s}" for n, r, b, s in
                        zip(mdf["name"], mdf["risk"], mdf["band"], mdf["signal"])]
    elif mode == "Opportunities":
        mdf["cat"] = mdf["signal"]
        colors = charts.signal_colors()
        mdf["hover"] = [f"{n}: {s}" + (f" ({res * 100:+.0f}% vs peers)".replace("-", "−") if pd.notna(res) else "")
                        for n, s, res in zip(mdf["name"], mdf["signal"], mdf["residual"])]
    elif mode == "1M spread change":
        mdf["cat"], colors = charts.diverging_bins(mdf["spread_1m_bp"])
        mdf["hover"] = [f"{n}: {fmt_bp(v)} over 1M · {s}" for n, v, s in
                        zip(mdf["name"], mdf["spread_1m_bp"], mdf["signal"])]
    else:
        col, fmt = {"Local 10Y": ("local_10y", lambda v: f"{v:.1f}%"), "Real 10Y": ("real_10y", lambda v: f"{v:.1f}%"),
                    "USD spread": ("spread_bp", lambda v: f"{v:.0f} bp")}[mode]
        mdf["cat"], colors = charts.quintile_bins(mdf[col], fmt)
        mdf["hover"] = [f"{n}: {mode} {fmt(v) if pd.notna(v) else 'n/a'} · {s}" for n, v, s in
                        zip(mdf["name"], mdf[col], mdf["signal"])]
    fig = charts.category_map(mdf, "cat", colors, "hover", selected=st.session_state.get("selected_country"))
    ev = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="mk_map",
                         config={"displayModeBar": False, "scrollZoom": False})
    st.markdown(charts.legend_html(colors, set(mdf["cat"])), unsafe_allow_html=True)
    picked = plotly_selected_iso2(ev, "mk_map")
    if picked:
        set_country(picked)
        if picked in tops:
            st.session_state["opp_idx"] = tops.index(picked)
        st.rerun()

# 5. 10-year benchmarks table --------------------------------------------------
st.markdown("#### 10-year benchmarks")
tbl = table[["name", "signal", "risk", "risk_1m", "usd_10y", "spread_bp", "spread_1m_bp", "local_10y", "inflation",
             "real_10y"]].copy()
tbl.index = table["iso2"]
tbl = tbl.sort_values("risk", ascending=False)

fmt_map = {"risk": "{:.0f}", "risk_1m": "{:+.0f}", "usd_10y": "{:.2f}%", "spread_bp": "{:.0f}",
           "spread_1m_bp": "{:+.0f}", "local_10y": "{:.2f}%", "inflation": "{:.1f}%", "real_10y": "{:.2f}%"}
styler = tbl.style.format(fmt_map, na_rep="n/a")
# "•" marks values sourced from the team's briefs.
for iso2 in tbl.index:
    src = by_iso.loc[iso2, "sources"] or {}
    for field in ("local_10y", "inflation"):
        if src.get(field) == "brief" and isnum(tbl.loc[iso2, field]):
            base = fmt_map[field]
            styler = styler.format(lambda v, b=base: b.format(v) + " •", subset=pd.IndexSlice[[iso2], [field]])
styler = styler.map(lambda s: f"color: {C.SIGNAL_COLORS.get(s, '#8A94A6')}; font-weight: 600", subset=["signal"])
styler = styler.map(lambda v: f"color: {C.BAND_COLORS[C.band_for(v)]}; font-weight: 700" if isnum(v) else "",
                    subset=["risk"])
sel = st.dataframe(
    styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="bm_table",
    column_config={
        "name": st.column_config.TextColumn("Country", pinned=True),
        "signal": st.column_config.TextColumn("Relative value"),
        "risk": st.column_config.NumberColumn("Risk"),
        "risk_1m": st.column_config.NumberColumn("Risk 1M", help="Change in points over one month"),
        "usd_10y": st.column_config.NumberColumn("USD 10Y", help="US 10Y + USD spread"),
        "spread_bp": st.column_config.NumberColumn("Spread (bp)"),
        "spread_1m_bp": st.column_config.NumberColumn("Spread 1M (bp)"),
        "local_10y": st.column_config.NumberColumn("Local 10Y"),
        "inflation": st.column_config.NumberColumn("Inflation"),
        "real_10y": st.column_config.NumberColumn("Real 10Y", help="Local 10Y − inflation"),
    })
rows = sel.selection.rows if sel else []
if rows:
    iso2 = tbl.index[rows[0]]
    if st.session_state.get("_last_bm_row") != iso2:
        st.session_state["_last_bm_row"] = iso2
        set_country(iso2)
        st.rerun()
st.caption("• Value from the team's briefs (Fri 25 Sep 2026 close). Spreads and changes in basis points. "
           "Local 10Y is the local-currency government bond; USD 10Y = US 10Y + USD spread. "
           "n/a = no liquid local 10Y or no data yet.")
