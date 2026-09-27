"""Portfolio: AI brief, positions with look-through, alerts, benchmark / weights slider (spec §8.3)."""
import pandas as pd
import streamlit as st

from core import config as C
from core import portfolio as pf
from ui import charts, data
from ui.components import ai_box, alert_row, fmt_pct, html_block, isnum, page_header, pager

page_header("Portfolio")
ai_box("portfolio", "Write with AI")

h = data.holdings()
lt = data.lookthrough()

# 2. Positions -------------------------------------------------------------------
st.markdown("#### Your positions, including ETFs")
m1, m2, m3 = st.columns(3)
with m1:
    with st.container(border=True):
        st.metric("Portfolio risk", f"{lt['portfolio_risk']:.0f} · {lt['portfolio_band']}"
                  if isnum(lt["portfolio_risk"]) else "n/a", help="Book-weighted average of country risk, after look-through.")
with m2:
    with st.container(border=True):
        st.metric("Estimated 1-month return", fmt_pct(lt["est_1m_return"], signed=True),
                  help="Carry (yield ÷ 12) plus the 1-month currency move on local-currency positions.")
with m3:
    with st.container(border=True):
        st.metric("% on alert", fmt_pct(lt["pct_on_alert"], 1), help="Book weight in countries with risk ≥ 55.")

flt = st.pills("Filter", ["All", "ETFs", "Bonds", "USD", "Local currency", "On alert"], default="All",
               key="pf_filter", label_visibility="collapsed") or "All"
view = h.copy()
if flt == "ETFs":
    view = view[view["type"] == "ETF"]
elif flt == "Bonds":
    view = view[view["type"] == "Bond"]
elif flt == "USD":
    view = view[view["ccy"] == "USD"]
elif flt == "Local currency":
    view = view[view["ccy"] == "Local"]
elif flt == "On alert":
    view = view[view["risk"] >= C.ALERT_THRESHOLD]

sel_country = st.session_state.get("selected_country")
if sel_country:
    touching = view[view["shares"].map(lambda s: sel_country in s)]
    if len(touching):
        name = next(c["name"] for c in C.COUNTRIES if c["iso2"] == sel_country)
        w = lt["weights"].get(sel_country, 0.0)
        st.caption(f"{name}: {w:.1f}% of the book after look-through, via {len(touching)} position(s): "
                   + ", ".join(touching["name"]))

PAID_LABEL = {"Yes": "Yes", "Check": "Check · risk on alert", "No": "No · currency eats the yield"}
tbl = pd.DataFrame({
    "Holding": view["name"],
    "Details": view["subtitle"],
    "Type": view["type"],
    "Ccy": view["ccy"],
    "Yield": view["yield_pct"],
    "Weight": view["weight"],
    "Risk": [f"{r:.0f} · {b}" if isnum(r) else "n/a" for r, b in zip(view["risk"], view["band"])],
    "1m carry": view["carry_1m"],
    "1m FX": view["fx_1m"],
    "1m net": view["net_1m"],
    "Paid for risk?": view["paid"].map(PAID_LABEL),
})
styler = tbl.style.format({"Yield": "{:.2f}%", "Weight": "{:.1f}%", "1m carry": "{:+.2f}%", "1m FX": "{:+.2f}%",
                           "1m net": "{:+.2f}%"})
styler = styler.map(lambda v: "color: #B2382B; font-weight: 700" if str(v).startswith("No")
                    else ("color: #D9772B; font-weight: 600" if str(v).startswith("Check") else ""),
                    subset=["Paid for risk?"])
styler = styler.map(lambda v: "color: #B2382B" if isinstance(v, float) and v <= 0 else "", subset=["1m net"])
sel = st.dataframe(styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="multi-row",
                   key=f"pf_table_{flt}",
                   column_config={"Holding": st.column_config.TextColumn(pinned=True),
                                  "Ccy": st.column_config.TextColumn(help="USD or local currency"),
                                  "1m FX": st.column_config.NumberColumn(help="Currency move over 1 month; negative = local currency weaker. 0 for USD.")})
chosen = [int(view.iloc[i]["id"]) for i in (sel.selection.rows if sel else [])]
c_rm, c_note = st.columns([1, 3], vertical_alignment="center")
with c_rm:
    if st.button(f"Remove ({len(chosen)})", icon=":material/delete:", disabled=not chosen, width="stretch"):
        for hid in chosen:
            pf.remove_holding(hid)
        data.clear_caches()
        st.rerun()
with c_note:
    st.caption("Select rows to remove them. 1-month figures: carry = yield ÷ 12; FX from the country's 1-month "
               "currency move (look-through for local-currency ETFs).")

with st.expander("Add a bond or import ETF holdings"):
    t_bond, t_etf = st.tabs(["Add a bond", "Import ETF holdings"])
    with t_bond:
        with st.form("add_bond", clear_on_submit=True):
            names = {c["name"]: c["iso2"] for c in C.COUNTRIES}
            a1, a2 = st.columns(2)
            country = a1.selectbox("Country", list(names))
            bname = a2.text_input("Name", placeholder="e.g. PE 2034 bond")
            a3, a4, a5, a6 = st.columns(4)
            ccy = a3.selectbox("Currency", ["USD", "Local"])
            yld = a4.number_input("Yield (%)", min_value=0.0, max_value=100.0, value=7.0, step=0.1)
            mat = a5.text_input("Maturity", placeholder="2034")
            amt = a6.number_input("Amount ($k)", min_value=1.0, value=10.0, step=1.0)
            if st.form_submit_button("Add bond", type="primary"):
                pf.add_bond(names[country], bname or f"{names[country]} {mat or ''} bond".strip(), ccy, yld, mat, amt)
                data.clear_caches()
                st.rerun()
    with t_etf:
        st.caption("CSV with two columns: country (name, ISO2 or ISO3) and weight. Countries outside the 18 covered "
                   "are dropped and the rest re-scaled. Yield = weighted USD 10Y of its countries.")
        up = st.file_uploader("ETF holdings file", type=["csv", "txt"])
        e1, e2, e3 = st.columns([2, 1, 1])
        ename = e1.text_input("Fund name", placeholder="e.g. EM bond ETF")
        eamt = e2.number_input("Amount ($k)", min_value=1.0, value=100.0, step=10.0)
        eccy = e3.selectbox("Currency", ["USD", "Local"], key="etf_ccy")
        if st.button("Import ETF", disabled=up is None or not ename.strip(), type="primary"):
            try:
                res = pf.import_etf(up.getvalue(), ename.strip(), amount_k=eamt, ccy=eccy)
                data.clear_caches()
                st.success(f"Imported {res['countries']} countries · yield {res['yield_pct']:.2f}%"
                           + (f" · dropped: {', '.join(res['skipped'])}" if res["skipped"] else ""))
            except ValueError as e:
                st.error(str(e))

# 3. Alerts | slider --------------------------------------------------------------
left, right = st.columns(2, gap="medium")
with left:
    with st.container(border=True, height="stretch"):
        st.markdown("**Alerts and concentrations**")
        items = data.alerts_and_concentrations()
        if not items:
            st.caption("Nothing to flag.")
        for it in items:
            label = "Risk" if it["severity"] == "risk" else "Watch"
            html_block([alert_row(label, it["title"], "", it["text"], None)])

with right:
    with st.container(border=True, height="stretch"):
        slide = pager("pf_slide", 2)
        if slide == 0:
            st.markdown("**You vs. the benchmark** · points over/under a sample blended EM index")
            vb = data.vs_benchmark()
            if vb.empty:
                st.caption("In line with the benchmark everywhere (all differences under 0.5 points).")
            else:
                vb = vb.sort_values("diff", ascending=False)
                fig = charts.hbar(
                    [f"{n} ({r:.0f})" for n, r in zip(vb["name"], vb["risk"])], vb["diff"].tolist(),
                    [C.BAND_COLORS.get(b, charts.NA_COLOR) for b in vb["band"]],
                    [f"{d:+.1f} pts".replace("-", "−") for d in vb["diff"]], diverging=True)
                st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="vb_chart")
                st.caption("Benchmark = average of the two sample funds. Bar colour = country risk band; "
                           "risk score in brackets.")
        else:
            st.markdown("**Country weights** · after looking through the ETFs")
            t = lt["table"]
            fig = charts.hbar(
                [f"{n} ({r:.0f})" for n, r in zip(t["name"], t["risk"])], t["weight"].tolist(),
                [C.BAND_COLORS.get(b, charts.NA_COLOR) for b in t["band"]],
                [f"{w:.1f}%" for w in t["weight"]])
            st.plotly_chart(fig, width="stretch", config={"displayModeBar": False}, key="lt_chart")
            st.markdown(charts.legend_html({b: c for b, c in charts.band_colors().items() if b != "n/a"}),
                        unsafe_allow_html=True)
