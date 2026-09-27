"""Risk map: choropleth by layer, heatmap table, country profile (spec §8.4)."""
import pandas as pd
import streamlit as st

from core import config as C
from ui import charts, data
from ui.components import band_pill, html_block, isnum, page_header, pill, plotly_selected_iso2, set_country

page_header("Risk map")

cs = data.country_scores()
by_iso = cs.set_index("iso2")
names = dict(zip(cs["iso2"], cs["name"]))

if st.session_state.get("selected_country") not in by_iso.index:
    st.session_state["selected_country"] = cs.iloc[0]["iso2"]  # highest risk first

INDICATOR_LABELS = {
    "liquidity_ratio": "Liquidity ratio (reserves ÷ ST debt + debt service), x",
    "reserves_months_imports": "Reserves, months of imports",
    "gov_debt_pct_gdp": "Government debt, % GDP",
    "interest_pct_revenue": "Interest, % of revenue",
    "fx_12m_pct": "FX 12M, % (+ = weaker)",
    "inflation": "Inflation, %",
    "current_account_pct_gdp": "Current account, % GDP",
    "spread_bp": "USD spread, bp",
    "cds_5y_bp": "5Y CDS, bp",
}
LAYERS = {"Overall": "overall", "Liquidity": "L", "Solvency": "S", "Currency": "F", "Market": "M", "Text signal": "T"}
layer_label = st.segmented_control("Colour by", list(LAYERS), default="Overall", key="rm_layer",
                                   label_visibility="collapsed") or "Overall"
col = LAYERS[layer_label]

left, right = st.columns([3, 2], gap="medium")
with left:
    with st.container(border=True, height="stretch"):
        mdf = cs.copy()
        mdf["cat"] = mdf[col].map(lambda v: C.band_for(v) if isnum(v) else "n/a")
        mdf["hover"] = [f"{n}: {layer_label} {v:.0f} · {C.band_for(v)}" if isnum(v) else f"{n}: {layer_label} n/a"
                        for n, v in zip(mdf["name"], mdf[col])]
        fig = charts.category_map(mdf, "cat", charts.band_colors(), "hover",
                                  selected=st.session_state["selected_country"], height=440)
        ev = st.plotly_chart(fig, width="stretch", on_select="rerun", selection_mode="points", key="rm_map",
                             config={"displayModeBar": False, "scrollZoom": False})
        st.markdown(charts.legend_html(charts.band_colors(), set(mdf["cat"])), unsafe_allow_html=True)
        if layer_label == "Text signal":
            st.caption("Text signal comes from AI scoring of official statements. It is shown but not part of the "
                       "overall score.")
        picked = plotly_selected_iso2(ev, "rm_map")
        if picked:
            set_country(picked)
            st.rerun()


def _on_pick():
    st.session_state["selected_country"] = st.session_state["rm_country"]


with right:
    with st.container(border=True, height="stretch"):
        iso_list = list(cs["iso2"])
        cur = st.session_state["selected_country"]
        st.session_state["rm_country"] = cur
        st.selectbox("Country", iso_list, format_func=lambda i: names[i], key="rm_country", on_change=_on_pick)
        r = by_iso.loc[cur]
        c1, c2 = st.columns([1, 2], vertical_alignment="center")
        with c1:
            color = C.BAND_COLORS.get(r["band"], charts.NA_COLOR)
            st.markdown(f'<div class="big-score" style="color:{color}">{r["overall"]:.0f}</div>'
                        if isnum(r["overall"]) else '<div class="big-score">n/a</div>', unsafe_allow_html=True)
        with c2:
            html_block([band_pill(r["overall"], r["band"])])
            st.caption("Overall risk · 0 safe → 100 distress")
        st.plotly_chart(charts.layer_bars({k: (r[k] if isnum(r[k]) else None) for k in "LSFMT"}), width="stretch",
                        config={"displayModeBar": False}, key="rm_layers")
        st.markdown(r["note"])
        st.markdown(f"**Data vs prices:** {r['gap_sentence']}")
        if isinstance(r["driver"], str) and r["driver"]:
            st.markdown(f"**What's moving it:** {r['driver']}".replace("$", "\\$"))
        flags = r["flags"] or {}
        chips = []
        if flags.get("imf_program"):
            chips.append(pill("IMF program", "#1F4E8C"))
        oil = flags.get("oil")
        if oil in ("importer", "exporter"):
            chips.append(pill(f"Oil {oil}", "#8A94A6"))
        if flags.get("hidden_debt"):
            chips.append(pill("Hidden debt found", "#B2382B"))
        if flags.get("post_restructuring"):
            chips.append(pill("Post-restructuring", "#C9A43A"))
        if chips:
            html_block(chips)
        src = r["layer_sources"] or {}
        with st.expander("Indicators and sources"):
            ind = data.country_detail(cur)["indicators"]
            rows = [{"Indicator": INDICATOR_LABELS[k], "Value": v["value"], "Year": v.get("year"),
                     "Source": v.get("source"), "Score": v.get("score")}
                    for k, v in ind.items() if k in INDICATOR_LABELS]
            if rows:
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                             column_config={"Value": st.column_config.NumberColumn(format="%.2f"),
                                            "Year": st.column_config.NumberColumn(format="%d"),
                                            "Score": st.column_config.NumberColumn(format="%.0f")})
                st.caption("Score = 0 safe → 100 danger for that indicator. Layers average the available scores; "
                           "layers without raw data keep their stored value (" + ", ".join(
                               f"{k}: {v}" for k, v in src.items()) + ").")
            else:
                st.caption("No raw indicators loaded yet.")

# Heatmap table --------------------------------------------------------------------
st.markdown("#### All layers")
tbl = cs[["name", "overall", "L", "S", "F", "M", "T", "gap"]].copy()
tbl.index = cs["iso2"]


def _cell(v):
    if not isnum(v):
        return ""
    c = C.BAND_COLORS[C.band_for(v)]
    fg = "#14213D" if c == C.BAND_COLORS["Watch"] else "#FFFFFF"
    return f"background-color: {c}; color: {fg}; font-weight: 600"


styler = (tbl.style.format({c: "{:.0f}" for c in ["overall", "L", "S", "F", "M", "T"]} | {"gap": "{:+.0f}"},
                           na_rep="n/a")
          .map(_cell, subset=["overall", "L", "S", "F", "M", "T"]))
sel = st.dataframe(
    styler, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row", key="rm_table",
    column_config={
        "name": st.column_config.TextColumn("Country", pinned=True),
        "overall": st.column_config.NumberColumn("Overall"),
        "L": st.column_config.NumberColumn("Liquidity"),
        "S": st.column_config.NumberColumn("Solvency"),
        "F": st.column_config.NumberColumn("Currency"),
        "M": st.column_config.NumberColumn("Market"),
        "T": st.column_config.NumberColumn("Text signal", help="Shown, not weighted in the overall score"),
        "gap": st.column_config.NumberColumn("Gap", help="Fundamentals − Market. ≥ +8: data worse than prices; "
                                                         "≤ −8: prices more worried than data."),
    })
rows = sel.selection.rows if sel else []
if rows:
    iso2 = tbl.index[rows[0]]
    if st.session_state.get("_last_rm_row") != iso2:
        st.session_state["_last_rm_row"] = iso2
        set_country(iso2)
        st.rerun()
st.caption("Overall = 0.30 Liquidity + 0.25 Solvency + 0.20 Currency + 0.25 Market. "
           "Bands: Low < 35 · Watch 35–54 · Alert 55–69 · Distress ≥ 70.")
