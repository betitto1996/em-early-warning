"""Market metrics (spec §6.2) and relative value — "Paid for the risk?" (spec §6.3)."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select

from core import config as C
from core.scoring import country_scores, raw_indicators

# ---------------------------------------------------------------------------
# Pure formulas
# ---------------------------------------------------------------------------


def _isnum(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def usd_10y(ust10: float, spread_bp: float | None) -> float | None:
    """USD 10Y = UST10 + spread / 100."""
    return ust10 + spread_bp / 100.0 if _isnum(spread_bp) else None


def real_10y(local_10y: float | None, inflation: float | None) -> float | None:
    """Real 10Y = local 10Y − inflation; null if either is missing."""
    return local_10y - inflation if _isnum(local_10y) and _isnum(inflation) else None


def fit_relative_value(risk: pd.Series, spread: pd.Series) -> tuple[float, float] | None:
    """OLS of ln(spread_bp) = a + b·risk on countries with a spread and risk < 70."""
    mask = risk.notna() & spread.notna() & (spread > 0) & (risk < C.DISTRESS_THRESHOLD)
    if mask.sum() < 2:
        return None
    b, a = np.polyfit(risk[mask].astype(float), np.log(spread[mask].astype(float)), 1)
    return float(a), float(b)


def fair_spread(a: float, b: float, risk: float) -> float:
    return math.exp(a + b * risk)


def residual(spread_bp: float, a: float, b: float, risk: float) -> float:
    """spread / exp(a + b·risk) − 1."""
    return spread_bp / fair_spread(a, b, risk) - 1.0


def rv_signal(risk: float | None, spread_bp: float | None, resid: float | None) -> str:
    if not _isnum(spread_bp):
        return "n/a"
    if _isnum(risk) and risk >= C.DISTRESS_THRESHOLD:
        return "Distressed"
    if not _isnum(resid):
        return "n/a"
    if resid > C.RV_CHEAP_RESIDUAL:
        return "Cheap"
    if resid < C.RV_RICH_RESIDUAL:
        return "Rich"
    return "Fair"


def pick_top_opportunities(df: pd.DataFrame) -> list[str]:
    """Cheap countries sorted by residual (max 5); if none, the top 3 residuals (non-distressed)."""
    cheap = df[df["signal"] == "Cheap"].sort_values("residual", ascending=False)
    if len(cheap):
        return cheap["iso2"].head(C.RV_MAX_TOP).tolist()
    ranked = df[df["residual"].notna() & (df["signal"] != "Distressed")].sort_values("residual", ascending=False)
    return ranked["iso2"].head(C.RV_FALLBACK_TOP).tolist()


def rv_sentence(signal: str, resid: float | None) -> str:
    if signal == "Distressed":
        return "Priced on recovery value, so there's no fair peer comparison."
    if signal == "n/a" or not _isnum(resid):
        return "No spread data yet, so no peer comparison."
    pct = abs(resid) * 100
    if resid >= 0:
        return f"Pays about {pct:.0f}% more spread than countries with similar risk."
    return f"Pays about {pct:.0f}% less spread than countries with similar risk."


# ---------------------------------------------------------------------------
# DB-backed services
# ---------------------------------------------------------------------------


def macro() -> list[dict[str, Any]]:
    """US 10Y, DXY, Brent, Fed hike odds with change, explanation and as-of."""
    from core.db import session_scope
    from core.models import Macro

    order = ["UST10", "DXY", "BRENT", "FED"]
    with session_scope() as s:
        rows = {m.key: m for m in s.execute(select(Macro)).scalars()}
    out = []
    for key in order:
        m = rows.get(key)
        if m is None:
            if key == "UST10":
                v = C.get_settings().ust10_fallback
                out.append(dict(key=key, label="US 10Y", value=v, display=f"{v:.2f}%", change_text=None,
                                explanation="Higher US yields raise the bar EM bonds must clear.",
                                source="fallback", as_of=None))
            continue
        out.append(dict(key=m.key, label=m.label, value=m.value, display=m.display, change_text=m.change_text,
                        explanation=m.explanation, source=m.source, as_of=m.as_of))
    return out


def ust10() -> float:
    for m in macro():
        if m["key"] == "UST10" and m["value"] is not None:
            return float(m["value"])
    return C.get_settings().ust10_fallback


def benchmarks() -> pd.DataFrame:
    """Per country: spread, 1M change, USD 10Y, local 10Y, inflation, real 10Y, risk, risk 1M, FX, sources."""
    from core.db import session_scope

    with session_scope() as s:
        raw = raw_indicators(s)
    scores = country_scores().set_index("iso2")
    base = ust10()
    rows = []
    for c in C.COUNTRIES:
        iso2 = c["iso2"]
        r = raw.get(iso2, {})

        def v(k):
            return r.get(k, {}).get("value")

        def src(k):
            return r.get(k, {}).get("source")

        fx_1m_pct = v("fx_1m_pct")  # + = weaker (CSV convention)
        sc = scores.loc[iso2]
        rows.append({
            "iso2": iso2, "iso3": c["iso3"], "name": c["name"], "region": c["region"], "oil": c["oil"],
            "risk": sc["overall"], "band": sc["band"], "risk_1m": sc["risk_1m_change"],
            "spread_bp": v("spread_bp"), "spread_1m_bp": v("spread_1m_bp"), "cds_5y_bp": v("cds_5y_bp"),
            "usd_10y": usd_10y(base, v("spread_bp")),
            "local_10y": v("local_10y"), "inflation": v("inflation"),
            "real_10y": real_10y(v("local_10y"), v("inflation")),
            # UI convention: 1M FX return, negative = local currency weakened.
            "fx_1m": 0.0 - fx_1m_pct if _isnum(fx_1m_pct) else None,
            "fx_12m_pct": v("fx_12m_pct"),
            "driver": sc["driver"],
            "sources": {k: src(k) for k in ("spread_bp", "spread_1m_bp", "local_10y", "inflation", "fx_1m_pct") if src(k)},
        })
    df = pd.DataFrame(rows)
    for col in ("risk", "risk_1m", "spread_bp", "spread_1m_bp", "cds_5y_bp", "usd_10y", "local_10y",
                "inflation", "real_10y", "fx_1m", "fx_12m_pct"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def relative_value() -> dict[str, Any]:
    """Fit (a, b), residual + signal per country, fair spread and the top opportunities."""
    df = benchmarks()
    fit = fit_relative_value(df["risk"], df["spread_bp"])
    if fit:
        a, b = fit
        df["fair_spread"] = [fair_spread(a, b, r) if _isnum(r) else None for r in df["risk"]]
        df["residual"] = [residual(sp, a, b, r) if _isnum(sp) and _isnum(r) else None
                          for sp, r in zip(df["spread_bp"], df["risk"])]
    else:
        a = b = None
        df["fair_spread"] = None
        df["residual"] = None
    df["residual"] = pd.to_numeric(df["residual"], errors="coerce")
    df["signal"] = [rv_signal(r, sp, res) for r, sp, res in zip(df["risk"], df["spread_bp"], df["residual"])]
    # A distressed country has no peer comparison; hide its residual.
    df.loc[df["signal"] == "Distressed", "residual"] = np.nan
    df["rv_sentence"] = [rv_sentence(sig, res) for sig, res in zip(df["signal"], df["residual"])]
    df["driver"] = [d if isinstance(d, str) and d else moves_sentence(row) for d, (_, row) in zip(df["driver"], df.iterrows())]
    return {"a": a, "b": b, "table": df, "top_opportunities": pick_top_opportunities(df)}


def moves_sentence(row) -> str:
    """Fallback "what's moving it" built only from the stored moves (never invented)."""
    parts = []
    if _isnum(row.get("spread_1m_bp")) and row["spread_1m_bp"] != 0:
        parts.append(f"spread {'widened' if row['spread_1m_bp'] > 0 else 'tightened'} {abs(row['spread_1m_bp']):.0f} bp over the month")
    if _isnum(row.get("risk_1m")) and row["risk_1m"] != 0:
        parts.append(f"risk score {row['risk_1m']:+.0f} pts")
    if _isnum(row.get("fx_1m")) and row["fx_1m"] != 0:
        parts.append(f"currency {row['fx_1m']:+.1f}% vs USD")
    if not parts:
        return "No major move this month."
    s = "; ".join(parts)
    return s[0].upper() + s[1:] + "."
