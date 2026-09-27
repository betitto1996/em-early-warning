"""Plotly maps, scatter and bars with the shared risk / signal colours."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core import config as C

MUTED = "rgba(138,148,166,0.35)"
LAND = "rgba(138,148,166,0.14)"
NA_COLOR = "#B8C0CC"
BLUES = ["#DCE8F7", "#A9C5EA", "#6F9BD6", "#3A6FB5", "#1F4E8C"]  # 5-step quintiles
DIVERGING = {"≤ −20 bp": "#1D7A6E", "−20 to −5 bp": "#7CC3B8", "±5 bp": "#B8C0CC",
             "+5 to +20 bp": "#E7A08F", "≥ +20 bp": "#B2382B"}

# Approximate country centroids (lat, lon) for bubble markers.
CENTROIDS = {
    "SN": (14.5, -14.5), "AR": (-34.0, -64.0), "PK": (30.0, 70.0), "EG": (26.8, 30.8), "KE": (0.2, 37.9),
    "LK": (7.9, 80.7), "GH": (7.9, -1.0), "TR": (39.0, 35.2), "ZM": (-13.1, 27.8), "NG": (9.1, 8.7),
    "ZA": (-30.6, 22.9), "BR": (-10.8, -52.9), "HU": (47.2, 19.5), "ID": (-2.5, 118.0), "MX": (23.6, -102.5),
    "CO": (4.6, -74.3), "CL": (-30.5, -71.0), "PE": (-9.2, -75.0),
}

BAND_ORDER = ["Low", "Watch", "Alert", "Distress"]
SIGNAL_ORDER = ["Cheap", "Fair", "Rich", "Distressed", "n/a"]


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=0, r=0, t=10, b=0), paper_bgcolor="rgba(0,0,0,0)",
                      plot_bgcolor="rgba(0,0,0,0)", showlegend=False, clickmode="event+select",
                      font=dict(family="Archivo, Helvetica Neue, Arial, sans-serif"))
    return fig


def _geo(fig: go.Figure) -> None:
    fig.update_geos(projection_type="natural earth", showframe=False, showcoastlines=False, showcountries=True,
                    countrycolor="rgba(138,148,166,0.35)", showland=True, landcolor=LAND,
                    bgcolor="rgba(0,0,0,0)", lataxis_range=[-50, 62], lonaxis_range=[-118, 145], showocean=False)


def _category_traces(fig, df: pd.DataFrame, cat_col: str, colors: dict[str, str], hover_col: str,
                     selected: str | None) -> None:
    for cat, color in colors.items():
        sub = df[df[cat_col] == cat]
        if sub.empty:
            continue
        fig.add_trace(go.Choropleth(
            locations=sub["iso3"], z=[1] * len(sub), locationmode="ISO-3",
            colorscale=[[0, color], [1, color]], showscale=False, customdata=sub[["iso2"]].values,
            text=sub[hover_col], hovertemplate="%{text}<extra></extra>",
            marker_line_color=["#14213D" if i == selected else "rgba(255,255,255,0.6)" for i in sub["iso2"]],
            marker_line_width=[2.2 if i == selected else 0.6 for i in sub["iso2"]],
            name=cat,
        ))


def category_map(df: pd.DataFrame, cat_col: str, colors: dict[str, str], hover_col: str,
                 selected: str | None = None, height: int = 420) -> go.Figure:
    """Choropleth with discrete colours (one trace per category). Hover shows value + label."""
    fig = go.Figure()
    _category_traces(fig, df, cat_col, colors, hover_col, selected)
    _geo(fig)
    return _layout(fig, height)


def band_colors() -> dict[str, str]:
    return {b: C.BAND_COLORS[b] for b in BAND_ORDER} | {"n/a": NA_COLOR}


def signal_colors() -> dict[str, str]:
    return {s: C.SIGNAL_COLORS[s] for s in SIGNAL_ORDER}


def quintile_bins(values: pd.Series, fmt) -> tuple[pd.Series, dict[str, str]]:
    """5-step blue quintiles; returns (category per row, ordered {label: colour})."""
    v = pd.to_numeric(values, errors="coerce")
    avail = v.dropna()
    cats = pd.Series(["n/a"] * len(v), index=v.index, dtype=object)
    colors: dict[str, str] = {}
    if len(avail) >= 1:
        n_bins = min(5, avail.nunique())
        if n_bins >= 2:
            q = pd.qcut(avail.rank(method="first"), n_bins, labels=False)
        else:
            q = pd.Series(0, index=avail.index)
        palette = BLUES if n_bins == 5 else BLUES[5 - n_bins:] if n_bins > 1 else [BLUES[2]]
        for b in range(n_bins):
            members = avail[q == b]
            label = f"{fmt(members.min())} – {fmt(members.max())}"
            colors[label] = palette[b]
            cats[members.index] = label
    colors["n/a"] = NA_COLOR
    return cats, colors


def diverging_bins(values: pd.Series) -> tuple[pd.Series, dict[str, str]]:
    """1M spread change: tighter teal, wider red, ±5bp neutral, ±20bp strong."""
    def cat(x):
        if x is None or (isinstance(x, float) and math.isnan(x)):
            return "n/a"
        if x <= -20:
            return "≤ −20 bp"
        if x < -5:
            return "−20 to −5 bp"
        if x <= 5:
            return "±5 bp"
        if x < 20:
            return "+5 to +20 bp"
        return "≥ +20 bp"
    return values.map(cat), DIVERGING | {"n/a": NA_COLOR}


def legend_html(colors: dict[str, str], present: set[str] | None = None) -> str:
    from ui.components import pill

    return '<div class="legend">' + "".join(
        pill(label, color) for label, color in colors.items() if present is None or label in present) + "</div>"


def exposure_map(df: pd.DataFrame, selected: str | None = None, height: int = 380) -> go.Figure:
    """Home 'My map': held countries tinted by risk colour, bubbles sized by book weight (radius ∝ √weight)."""
    fig = go.Figure()
    df = df.copy()
    df["band"] = df["band"].fillna("n/a")
    held = df[df["held"]].copy()
    not_held = df[~df["held"]].copy()
    if not not_held.empty:
        not_held["hover"] = not_held["name"] + " · not held · risk " + not_held["risk"].map(
            lambda r: f"{r:.0f}" if pd.notna(r) else "n/a")
        fig.add_trace(go.Choropleth(
            locations=not_held["iso3"], z=[1] * len(not_held), colorscale=[[0, MUTED], [1, MUTED]], showscale=False,
            customdata=not_held[["iso2"]].values, text=not_held["hover"], hovertemplate="%{text}<extra></extra>",
            marker_line_color="rgba(255,255,255,0.5)", marker_line_width=0.5))
    held["hover"] = (held["name"] + " · " + held["weight"].map(lambda w: f"{w:.1f}% of the book") + " · risk "
                     + held["risk"].map(lambda r: f"{r:.0f}") + " " + held["band"])
    for band in BAND_ORDER:
        sub = held[held["band"] == band]
        if sub.empty:
            continue
        color = C.BAND_COLORS[band]
        fig.add_trace(go.Choropleth(
            locations=sub["iso3"], z=[1] * len(sub), colorscale=[[0, color], [1, color]], showscale=False,
            customdata=sub[["iso2"]].values, text=sub["hover"], hovertemplate="%{text}<extra></extra>",
            marker_opacity=0.45,
            marker_line_color=["#14213D" if i == selected else "rgba(255,255,255,0.6)" for i in sub["iso2"]],
            marker_line_width=[2 if i == selected else 0.6 for i in sub["iso2"]]))
    if not held.empty:
        lat = [CENTROIDS[i][0] for i in held["iso2"]]
        lon = [CENTROIDS[i][1] for i in held["iso2"]]
        size = (np.sqrt(held["weight"]) * 9).clip(lower=6)
        labels = [f"{n} {w:.0f}%" if w >= 3 else "" for n, w in zip(held["name"], held["weight"])]
        fig.add_trace(go.Scattergeo(
            lat=lat, lon=lon, mode="markers+text", text=labels, textposition="top center",
            textfont=dict(size=11), customdata=held[["iso2"]].values, hovertext=held["hover"],
            hovertemplate="%{hovertext}<extra></extra>",
            marker=dict(size=size, sizemode="diameter", color=[C.BAND_COLORS.get(b, NA_COLOR) for b in held["band"]],
                        opacity=0.9, line=dict(color="white", width=1))))
    _geo(fig)
    return _layout(fig, height)


def rv_scatter(df: pd.DataFrame, a: float | None, b: float | None, selected: str | None = None,
               height: int = 440) -> go.Figure:
    """'Paid for the risk?': x = risk 0–100, y = spread (log 50–2000bp), fitted curve, distressed zone."""
    fig = go.Figure()
    fig.add_vrect(x0=C.DISTRESS_THRESHOLD, x1=100, fillcolor=C.BAND_COLORS["Distress"], opacity=0.08, line_width=0,
                  annotation_text="Distressed", annotation_position="top right",
                  annotation_font_size=11)
    if a is not None and b is not None:
        xs = np.linspace(0, 100, 101)
        fig.add_trace(go.Scatter(x=xs, y=np.exp(a + b * xs), mode="lines", hoverinfo="skip",
                                 line=dict(color="#8A94A6", dash="dash", width=1.5), name="Fitted curve"))
    d = df[df["spread_bp"].notna() & df["risk"].notna()]
    for sig in SIGNAL_ORDER:
        sub = d[d["signal"] == sig]
        if sub.empty:
            continue
        hover = [f"{n}: risk {r:.0f}, spread {s:.0f} bp, {sig}" + (f" ({res * 100:+.0f}% vs peers)".replace("-", "−")
                                                                  if pd.notna(res) else "")
                 for n, r, s, res in zip(sub["name"], sub["risk"], sub["spread_bp"], sub["residual"])]
        fig.add_trace(go.Scatter(
            x=sub["risk"], y=sub["spread_bp"], mode="markers+text", text=sub["iso2"], textposition="top center",
            textfont=dict(size=11), customdata=sub[["iso2"]].values, hovertext=hover,
            hovertemplate="%{hovertext}<extra></extra>", name=sig,
            marker=dict(size=[17 if i == selected else 11 for i in sub["iso2"]], color=C.SIGNAL_COLORS[sig],
                        line=dict(color=["#14213D" if i == selected else "white" for i in sub["iso2"]],
                                  width=[2.5 if i == selected else 1 for i in sub["iso2"]]))))
    fig.update_xaxes(title="Risk score (0 safe → 100 distress)", range=[0, 100], zeroline=False)
    fig.update_yaxes(title="USD spread (bp, log scale)", type="log", range=[math.log10(50), math.log10(2000)],
                     tickvals=[50, 100, 200, 500, 1000, 2000])
    _layout(fig, height)
    fig.update_layout(showlegend=True, legend=dict(orientation="h", y=-0.22, x=0), margin=dict(l=0, r=0, t=10, b=0))
    return fig


def hbar(labels: list[str], values: list[float], colors: list[str], text: list[str], height: int | None = None,
         diverging: bool = False, customdata: list[str] | None = None) -> go.Figure:
    """Horizontal bars (look-through weights or over/underweights)."""
    fig = go.Figure(go.Bar(
        y=labels, x=values, orientation="h", marker_color=colors, text=text, textposition="outside",
        cliponaxis=False, customdata=customdata, hovertemplate="%{y}: %{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", automargin=True)
    fig.update_xaxes(showgrid=True, zeroline=diverging, zerolinewidth=1.5, showticklabels=False)
    span = max([abs(v) for v in values] + [1])
    fig.update_xaxes(range=[-span * 1.6, span * 1.6] if diverging else [0, span * 1.4])
    return _layout(fig, height or max(220, 26 * len(labels) + 40))


def layer_bars(layers: dict[str, float | None]) -> go.Figure:
    names = [C.LAYER_NAMES[k] for k in layers]
    vals = [v if v is not None else 0 for v in layers.values()]
    colors = [C.BAND_COLORS[C.band_for(v)] if v is not None else NA_COLOR for v in layers.values()]
    text = [f"{v:.0f} · {C.band_for(v)}" if v is not None else "n/a" for v in layers.values()]
    fig = go.Figure(go.Bar(y=names, x=vals, orientation="h", marker_color=colors, text=text, textposition="outside",
                           cliponaxis=False, hovertemplate="%{y}: %{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed", automargin=True)
    fig.update_xaxes(range=[0, 118], showticklabels=False, showgrid=False)
    return _layout(fig, 210)
