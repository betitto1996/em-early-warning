"""Shared UI pieces: CSS/fonts, pills, alert rows, pager, AI box, formatting."""
from __future__ import annotations

import html
import math
from typing import Iterable

import streamlit as st

from core import ai
from core import config as C

TAG_COLORS = {"Risk": "#B2382B", "Watch": "#C9A43A", "Positive": "#2A8C7F"}
DARK_TEXT_ON = {"#C9A43A", "#B8C0CC", "#8A94A6"}  # light fills → dark text for contrast

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700;800&family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;1,6..72,400&display=swap');
html, body, [class*="css"], .stApp { font-family: 'Archivo', 'Helvetica Neue', Arial, sans-serif; }
[data-testid="stMetricValue"] { font-family: 'Archivo', 'Helvetica Neue', Arial, sans-serif; font-weight: 700; }
[class*="st-key-ai-"] [data-testid="stMarkdownContainer"] p,
.prose, [data-testid="stChatMessage"] [data-testid="stMarkdownContainer"] p {
  font-family: 'Newsreader', Georgia, 'Times New Roman', serif; font-size: 1.13rem; line-height: 1.6;
}
.em-sub { opacity: .75; margin: -.4rem 0 .2rem 0; font-size: .95rem; }
.pill { display: inline-block; padding: .12rem .55rem; border-radius: 999px; font-size: .78rem;
        font-weight: 600; letter-spacing: .01em; margin-right: .3rem; white-space: nowrap; }
.pill-outline { border: 1px solid currentColor; opacity: .8; }
.alert-row { display: flex; gap: .75rem; align-items: flex-start; padding: .65rem 0;
             border-bottom: 1px solid rgba(128,128,128,.2); }
.alert-row:last-child { border-bottom: none; }
.alert-body { flex: 1; min-width: 0; }
.alert-title { font-weight: 600; }
.alert-driver { opacity: .9; }
.alert-moves { font-size: .82rem; opacity: .7; margin-top: .15rem; }
.alert-pct { text-align: right; font-weight: 700; font-size: 1.15rem; white-space: nowrap; }
.alert-pct small { display: block; font-weight: 400; font-size: .72rem; opacity: .7; }
.big-score { font-size: 3.2rem; font-weight: 800; line-height: 1; }
.dots { text-align: center; font-size: 1rem; opacity: .85; }
.dot-row { letter-spacing: .3rem; margin-right: .3rem; }
[data-testid="stMetricValue"] > div { white-space: normal; overflow-wrap: anywhere; }
.legend { font-size: .8rem; margin-top: -.4rem; }
.card-title { font-weight: 700; font-size: 1.05rem; margin-bottom: .2rem; }
.muted { opacity: .7; font-size: .85rem; }
@media (max-width: 640px) {
  .big-score { font-size: 2.4rem; }
  [data-testid="stMetricValue"] { font-size: 1.9rem; }
  .alert-pct { font-size: 1rem; }
}
</style>
"""


def inject_css() -> None:
    st.html(CSS)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def isnum(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def minus(s: str) -> str:
    return s.replace("-", "−")


def fmt_pct(x, d: int = 2, signed: bool = False) -> str:
    if not isnum(x):
        return "n/a"
    return minus(f"{x:+.{d}f}%") if signed else minus(f"{x:.{d}f}%")


def fmt_bp(x, signed: bool = True) -> str:
    if not isnum(x):
        return "n/a"
    return minus(f"{x:+.0f} bp") if signed else f"{x:.0f} bp"


def fmt_pts(x) -> str:
    if not isnum(x):
        return "n/a"
    return minus(f"{x:+.0f} pts") if x else "±0 pts"


def fmt_score(x) -> str:
    return f"{x:.0f}" if isnum(x) else "n/a"


def md_safe(text: str) -> str:
    """Escape '$' so Streamlit markdown doesn't treat amounts as LaTeX."""
    return (text or "").replace("$", "\\$")


# ---------------------------------------------------------------------------
# Pills and rows
# ---------------------------------------------------------------------------

def pill(label: str, color: str) -> str:
    fg = "#14213D" if color in DARK_TEXT_ON else "#FFFFFF"
    return f'<span class="pill" style="background:{color};color:{fg}">{html.escape(label)}</span>'


def band_pill(score, band: str | None = None) -> str:
    band = band or C.band_for(score)
    if band is None:
        return pill("n/a", "#B8C0CC")
    text = f"{score:.0f} · {band}" if isnum(score) else band
    return pill(text, C.BAND_COLORS[band])


def signal_pill(signal: str) -> str:
    return pill(signal, C.SIGNAL_COLORS.get(signal, "#B8C0CC"))


def alert_row(tag: str, title: str, driver: str, moves: str, pct: float | None) -> str:
    pct_html = f'<div class="alert-pct">{pct:.1f}%<small>of the book</small></div>' if isnum(pct) else ""
    return (
        f'<div class="alert-row"><div>{pill(tag, TAG_COLORS.get(tag, "#8A94A6"))}</div>'
        f'<div class="alert-body"><div class="alert-title">{html.escape(title)}</div>'
        f'<div class="alert-driver">{html.escape(driver or "")}</div>'
        f'<div class="alert-moves">{html.escape(moves or "")}</div></div>{pct_html}</div>'
    )


def html_block(fragments: Iterable[str]) -> None:
    st.markdown("".join(fragments), unsafe_allow_html=True)


def page_header(title: str) -> None:
    """Page title and the product subtitle."""
    st.title(title)
    st.markdown(f'<div class="em-sub">{html.escape(C.SUBTITLE)}</div>', unsafe_allow_html=True)


def footer() -> None:
    st.divider()
    st.caption(C.DISCLAIMER)


# ---------------------------------------------------------------------------
# Pager / slider
# ---------------------------------------------------------------------------

def _step(key: str, n: int, delta: int) -> None:
    st.session_state[key] = (st.session_state.get(key, 0) + delta) % n


def pager(key: str, n: int, labels: list[str] | None = None) -> int:
    """‹ dots › control backed by st.session_state[key]; returns the current index."""
    if n <= 0:
        return 0
    idx = st.session_state.get(key, 0) % n
    st.session_state[key] = idx
    with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
        st.button("‹", key=f"{key}_prev", on_click=_step, args=(key, n, -1), help="Previous")
        dots = "".join("●" if i == idx else "○" for i in range(n))
        caption = f'<span class="muted">{html.escape(labels[idx])}</span>' if labels else ""
        st.markdown(f'<div class="dots"><span class="dot-row">{dots}</span> {caption}</div>', unsafe_allow_html=True,
                    width="content")
        st.button("›", key=f"{key}_next", on_click=_step, args=(key, n, 1), help="Next")
    return idx


# ---------------------------------------------------------------------------
# AI summary box
# ---------------------------------------------------------------------------

def _md_stream(stream: ai.AIStream):
    for chunk in stream:
        yield md_safe(chunk)


def ai_box(scope: str, button_label: str, auto: bool = False, title: str = "AI summary") -> None:
    """Serif-styled summary. Deterministic text until the button asks Gemini (or `auto` on first visit)."""
    state_key = f"brief_{scope}"
    settings = C.get_settings()
    with st.container(border=True, key=f"ai-{scope}"):
        with st.container(horizontal=True, vertical_alignment="center", horizontal_alignment="distribute"):
            st.markdown(f"**{title}**")
            clicked = st.button(button_label, key=f"btn_{scope}", icon=":material/auto_awesome:", type="tertiary")
        run_ai = clicked or (auto and settings.ai_enabled and state_key not in st.session_state)
        if run_ai:
            stream = ai.stream_brief(scope)
            st.write_stream(_md_stream(stream))
            st.session_state[state_key] = {"text": stream.text.strip(), "ai_used": stream.ai_used, "notice": stream.notice}
        elif state_key in st.session_state:
            st.markdown(md_safe(st.session_state[state_key]["text"]))
        else:
            text = ai.fallback_brief(scope)
            st.markdown(md_safe(text))
            st.session_state[state_key] = {"text": text, "ai_used": False, "notice": None}
        saved = st.session_state[state_key]
        if saved.get("notice"):
            st.caption(saved["notice"])
        elif saved.get("ai_used"):
            st.caption("Written by Gemini from this dashboard's data.")
        else:
            st.caption("Built-in summary from this dashboard's data."
                       + ("" if settings.ai_enabled else " Add a Gemini key to write it with AI."))


# ---------------------------------------------------------------------------
# Selection helpers
# ---------------------------------------------------------------------------

def plotly_selected_iso2(event, widget_key: str) -> str | None:
    """Newly selected ISO2 from a plotly selection event (None if unchanged since last run)."""
    points = []
    try:
        points = event.selection.points if event else []
    except AttributeError:
        points = (event or {}).get("selection", {}).get("points", [])
    iso2 = None
    for p in points or []:
        cd = p.get("customdata")
        if isinstance(cd, (list, tuple)) and cd:
            cd = cd[0]
        if isinstance(cd, str) and cd in C.ISO2_TO_ISO3:
            iso2 = cd
            break
        loc = p.get("location")
        if isinstance(loc, str) and loc in C.ISO3_TO_ISO2:
            iso2 = C.ISO3_TO_ISO2[loc]
            break
    memo = f"_last_sel_{widget_key}"
    if iso2 == st.session_state.get(memo):
        return None
    st.session_state[memo] = iso2
    return iso2


def selected_country(default: str | None = None) -> str | None:
    return st.session_state.get("selected_country", default)


def set_country(iso2: str | None) -> None:
    if iso2:
        st.session_state["selected_country"] = iso2
