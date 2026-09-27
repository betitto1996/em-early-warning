"""Settings (env / .env / Streamlit secrets) and methodology constants.

Every weight, threshold and band used by the scoring and portfolio services
lives in this module so the team can recalibrate in one place (spec §6).
Changing any of these values changes numbers the team defends to judges —
flag it to the team before committing.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_SECRET_KEYS = (
    "APP_ENV", "LOG_LEVEL", "DATABASE_URL", "CACHE_DIR", "DEMO_MODE",
    "GEMINI_API_KEY", "GEMINI_MODEL", "GEMINI_FAST_MODEL", "AI_MAX_OUTPUT_TOKENS",
    "AI_TEMPERATURE", "AI_TIMEOUT_SECONDS", "FRED_API_KEY", "UST10_FALLBACK",
    "MANUAL_INPUTS_CSV", "IMF_YEAR",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'app.db'}"
    cache_dir: Path = ROOT / "data" / "cache"
    demo_mode: bool = True

    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    gemini_fast_model: str = "gemini-flash-lite-latest"
    ai_max_output_tokens: int = 800
    ai_temperature: float = 0.3
    ai_timeout_seconds: int = 30

    fred_api_key: str = ""
    ust10_fallback: float = 5.17
    manual_inputs_csv: Path = ROOT / "data" / "manual_inputs.csv"
    imf_year: int = 2025

    @field_validator("gemini_api_key", "fred_api_key", mode="before")
    @classmethod
    def _strip_key(cls, v):
        # Guard against `KEY=   # comment` being read as the key itself.
        v = (v or "").strip()
        return "" if v.startswith("#") else v

    @property
    def ai_enabled(self) -> bool:
        return bool(self.gemini_api_key.strip())

    def resolve(self, p: Path) -> Path:
        """Relative paths in .env are relative to the project root, not the CWD."""
        return p if p.is_absolute() else ROOT / p


def _streamlit_secrets() -> dict[str, str]:
    """Read `.streamlit/secrets.toml` when running under Streamlit; never raises."""
    try:
        import streamlit as st  # optional: services still work without Streamlit

        return {k: str(st.secrets[k]) for k in _SECRET_KEYS if k in st.secrets}
    except Exception:
        return {}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # st.secrets first, then env vars / .env (pydantic-settings handles those).
    overrides = {k.lower(): v for k, v in _streamlit_secrets().items()}
    settings = Settings(**overrides)
    # SQLite relative URLs resolve against the project root.
    if settings.database_url.startswith("sqlite:///./"):
        settings.database_url = f"sqlite:///{ROOT / settings.database_url[len('sqlite:///./'):]}"
    settings.cache_dir = settings.resolve(settings.cache_dir)
    settings.manual_inputs_csv = settings.resolve(settings.manual_inputs_csv)
    return settings


def reset_settings() -> None:
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Coverage (spec §5)
# ---------------------------------------------------------------------------

COUNTRIES: list[dict] = [
    {"name": "Senegal", "iso3": "SEN", "iso2": "SN", "currency": "XOF", "region": "Africa", "oil": "importer"},
    {"name": "Argentina", "iso3": "ARG", "iso2": "AR", "currency": "ARS", "region": "LatAm", "oil": "neutral"},
    {"name": "Pakistan", "iso3": "PAK", "iso2": "PK", "currency": "PKR", "region": "Asia", "oil": "importer"},
    {"name": "Egypt", "iso3": "EGY", "iso2": "EG", "currency": "EGP", "region": "Africa", "oil": "importer"},
    {"name": "Kenya", "iso3": "KEN", "iso2": "KE", "currency": "KES", "region": "Africa", "oil": "importer"},
    {"name": "Sri Lanka", "iso3": "LKA", "iso2": "LK", "currency": "LKR", "region": "Asia", "oil": "importer"},
    {"name": "Ghana", "iso3": "GHA", "iso2": "GH", "currency": "GHS", "region": "Africa", "oil": "neutral"},
    {"name": "Turkey", "iso3": "TUR", "iso2": "TR", "currency": "TRY", "region": "Europe", "oil": "importer"},
    {"name": "Zambia", "iso3": "ZMB", "iso2": "ZM", "currency": "ZMW", "region": "Africa", "oil": "importer"},
    {"name": "Nigeria", "iso3": "NGA", "iso2": "NG", "currency": "NGN", "region": "Africa", "oil": "exporter"},
    {"name": "South Africa", "iso3": "ZAF", "iso2": "ZA", "currency": "ZAR", "region": "Africa", "oil": "importer"},
    {"name": "Brazil", "iso3": "BRA", "iso2": "BR", "currency": "BRL", "region": "LatAm", "oil": "exporter"},
    {"name": "Hungary", "iso3": "HUN", "iso2": "HU", "currency": "HUF", "region": "Europe", "oil": "importer"},
    {"name": "Indonesia", "iso3": "IDN", "iso2": "ID", "currency": "IDR", "region": "Asia", "oil": "neutral"},
    {"name": "Mexico", "iso3": "MEX", "iso2": "MX", "currency": "MXN", "region": "LatAm", "oil": "neutral"},
    {"name": "Colombia", "iso3": "COL", "iso2": "CO", "currency": "COP", "region": "LatAm", "oil": "exporter"},
    {"name": "Chile", "iso3": "CHL", "iso2": "CL", "currency": "CLP", "region": "LatAm", "oil": "importer"},
    {"name": "Peru", "iso3": "PER", "iso2": "PE", "currency": "PEN", "region": "LatAm", "oil": "neutral"},
]
ISO2_TO_ISO3 = {c["iso2"]: c["iso3"] for c in COUNTRIES}
ISO3_TO_ISO2 = {c["iso3"]: c["iso2"] for c in COUNTRIES}
NAME_TO_ISO2 = {c["name"].lower(): c["iso2"] for c in COUNTRIES}
NAME_TO_ISO2.update({"türkiye": "TR", "turkiye": "TR", "egypt, arab rep.": "EG"})


def to_iso2(value: str) -> str | None:
    """Map a country name, ISO2 or ISO3 code to our ISO2; None if not covered."""
    v = (value or "").strip()
    if not v:
        return None
    if v.upper() in ISO2_TO_ISO3:
        return v.upper()
    if v.upper() in ISO3_TO_ISO2:
        return ISO3_TO_ISO2[v.upper()]
    return NAME_TO_ISO2.get(v.lower())


# ---------------------------------------------------------------------------
# Risk score (spec §6.1)
# ---------------------------------------------------------------------------

# indicator -> (safe value -> 0, danger value -> 100); linear, clipped to [0, 100]
INDICATOR_RANGES: dict[str, tuple[float, float]] = {
    "liquidity_ratio": (2.0, 0.5),
    "reserves_months_imports": (6.0, 2.0),
    "gov_debt_pct_gdp": (40.0, 100.0),
    "interest_pct_revenue": (8.0, 35.0),
    "fx_12m_pct": (0.0, 30.0),            # + = local currency weaker
    "inflation": (4.0, 30.0),             # IMF, else World Bank
    "current_account_pct_gdp": (0.0, -6.0),
    "spread_bp": (200.0, 1000.0),
    "cds_5y_bp": (150.0, 1000.0),
}

LAYER_INDICATORS: dict[str, list[str]] = {
    "L": ["liquidity_ratio", "reserves_months_imports"],
    "S": ["gov_debt_pct_gdp", "interest_pct_revenue"],
    "F": ["fx_12m_pct", "inflation", "current_account_pct_gdp"],
    "M": ["spread_bp", "cds_5y_bp"],
}
LAYER_NAMES = {"L": "Liquidity", "S": "Solvency", "F": "Currency", "M": "Market", "T": "Text signal"}

# T (text signal) is displayed as a layer but is NOT part of the overall score.
OVERALL_WEIGHTS: dict[str, float] = {"L": 0.30, "S": 0.25, "F": 0.20, "M": 0.25}
FUNDAMENTAL_LAYERS = ("L", "S", "F")

GAP_THRESHOLD = 8  # fundamentals − market ≥ +8 / ≤ −8

# Implementation choice (not in spec §6 — flagged in README): when recomputing
# scores from raw data, a layer is recomputed only if at least this many of its
# indicators are available from non-illustrative sources; otherwise the stored
# (seeded) layer value is kept. Avoids one stray value replacing a whole layer.
MIN_INDICATORS_PER_LAYER = {"L": 1, "S": 1, "F": 2, "M": 1}

# Bands: (lower bound inclusive, label, colour)
BANDS = [
    (70, "Distress", "#B2382B"),
    (55, "Alert", "#D9772B"),
    (35, "Watch", "#C9A43A"),
    (0, "Low", "#2A8C7F"),
]
BAND_COLORS = {label: color for _, label, color in BANDS}
ALERT_THRESHOLD = 55
DISTRESS_THRESHOLD = 70


def band_for(score: float | None) -> str | None:
    if score is None:
        return None
    for lower, label, _ in BANDS:
        if score >= lower:
            return label
    return "Low"


# ---------------------------------------------------------------------------
# Relative value (spec §6.3)
# ---------------------------------------------------------------------------

RV_CHEAP_RESIDUAL = 0.25
RV_RICH_RESIDUAL = -0.20
RV_MAX_TOP = 5
RV_FALLBACK_TOP = 3
SIGNAL_COLORS = {"Cheap": "#2A8C7F", "Fair": "#8A94A6", "Rich": "#C9A43A", "Distressed": "#B2382B", "n/a": "#B8C0CC"}

# ---------------------------------------------------------------------------
# Portfolio (spec §6.4) and home alerts (spec §6.5)
# ---------------------------------------------------------------------------

CONCENTRATION_THRESHOLD = 15.0     # single-country book weight, %
OIL_IMPORTER_THRESHOLD = 40.0      # oil-importer share of book, %
BENCHMARK_MIN_DIFF = 0.5           # hide |over/underweight| below this, points
HOME_ALERT_MIN_WEIGHT = 1.0        # book weight, %
HOME_ALERT_ALERT_BONUS = 2.0
HOME_ALERT_SPREAD_DIVISOR = 25.0
HOME_ALERT_BASE = 0.5
HOME_ALERT_SPREAD_WIDEN_BP = 20.0
HOME_ALERT_TOP = 5
OPPORTUNITY_MAX_BOOK_WEIGHT = 2.0  # "AI opportunities" card: top opportunities with book weight < 2%

XOF_PER_EUR = 655.957  # fixed CFA franc peg

# FRED local 10Y may replace an *estimated* local_10y only when it looks consistent with the market
# (a data-quality rule chosen by the assistant, not in spec §6 — flag to the team; recalibrate here).
FRED_YIELD_MAX_AGE_DAYS = 120   # latest FRED observation must be at most this old
FRED_YIELD_MAX_DIFF_BP = 150    # and within this many bp of the value it would replace

DISCLAIMER = "Information and comparison only, not investment advice."
SUBTITLE = (
    "See the real risk inside emerging-market debt, whether you own a fund or pick countries yourself. "
    "AI spots the warning signs early and explains them in plain words."
)
