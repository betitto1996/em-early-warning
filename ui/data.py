"""Cached reads of the services layer. Call `clear_caches()` after a data refresh or a book edit."""
from __future__ import annotations

import streamlit as st

from core import home, markets, portfolio, scoring
from core.db import init_db

TTL = 600


@st.cache_resource
def ensure_db() -> bool:
    return init_db()


@st.cache_data(ttl=TTL, show_spinner=False)
def country_scores():
    return scoring.country_scores()


@st.cache_data(ttl=TTL, show_spinner=False)
def country_detail(iso2: str):
    return scoring.country_detail(iso2)


@st.cache_data(ttl=TTL, show_spinner=False)
def macro():
    return markets.macro()


@st.cache_data(ttl=TTL, show_spinner=False)
def relative_value():
    return markets.relative_value()


@st.cache_data(ttl=TTL, show_spinner=False)
def holdings():
    return portfolio.holdings()


@st.cache_data(ttl=TTL, show_spinner=False)
def lookthrough():
    return portfolio.lookthrough()


@st.cache_data(ttl=TTL, show_spinner=False)
def vs_benchmark():
    return portfolio.vs_benchmark()


@st.cache_data(ttl=TTL, show_spinner=False)
def alerts_and_concentrations():
    return portfolio.alerts_and_concentrations()


@st.cache_data(ttl=TTL, show_spinner=False)
def home_view():
    return home.home_view()


def clear_caches() -> None:
    st.cache_data.clear()
    # Briefs describe the old data; drop them so they are rewritten.
    for k in [k for k in st.session_state if str(k).startswith("brief_")]:
        del st.session_state[k]
