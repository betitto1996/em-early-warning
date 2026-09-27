"""Country risk score (spec §6.1). 0 = safe, 100 = distress.

Pure formula functions at the top (unit-tested), DB-backed services below.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import pandas as pd
from sqlalchemy import select

from core import config as C

# Source precedence when several values exist for the same indicator:
# team/manual values override API values; illustrative seeds are last.
SOURCE_RANK = {"manual": 5, "ai": 4, "brief": 4, "worldbank": 3, "imf": 3, "frankfurter": 3, "fred": 3,
               "computed": 3, "illustrative": 0}


# ---------------------------------------------------------------------------
# Pure formulas
# ---------------------------------------------------------------------------

def round_half_up(x: float | None, ndigits: int = 0) -> float | None:
    """Round half away from zero (54.5 → 55), not Python's banker's rounding."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    q = 10 ** ndigits
    return math.copysign(math.floor(abs(x) * q + 0.5 + 1e-9), x) / q


def indicator_score(key: str, value: float | None) -> float | None:
    """Linear map between the safe value (0) and the danger value (100), clipped."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    safe, danger = C.INDICATOR_RANGES[key]
    return max(0.0, min(100.0, (value - safe) / (danger - safe) * 100.0))


def liquidity_ratio(reserves: float | None, short_term_debt: float | None, debt_service: float | None) -> float | None:
    """reserves ÷ (short-term external debt + annual external debt service)."""
    if reserves is None or short_term_debt is None or debt_service is None:
        return None
    denom = short_term_debt + debt_service
    if denom <= 0:
        return None
    return reserves / denom


def layer_scores(indicators: dict[str, float | None]) -> dict[str, float | None]:
    """Each layer = average of its available indicator scores (None if none available)."""
    out: dict[str, float | None] = {}
    for layer, keys in C.LAYER_INDICATORS.items():
        scores = [s for k in keys if (s := indicator_score(k, indicators.get(k))) is not None]
        out[layer] = sum(scores) / len(scores) if scores else None
    return out


def _weighted(layers: dict[str, float | None], keys) -> float | None:
    pairs = [(C.OVERALL_WEIGHTS[k], layers.get(k)) for k in keys if layers.get(k) is not None]
    total_w = sum(w for w, _ in pairs)
    return sum(w * v for w, v in pairs) / total_w if total_w else None


def overall_score(layers: dict[str, float | None]) -> float | None:
    """0.30·L + 0.25·S + 0.20·F + 0.25·M, re-weighted over available layers; rounded. T excluded."""
    return round_half_up(_weighted(layers, C.OVERALL_WEIGHTS.keys()))


def fundamentals_score(layers: dict[str, float | None]) -> float | None:
    return _weighted(layers, C.FUNDAMENTAL_LAYERS)


def compute_derived(layers: dict[str, float | None]) -> dict[str, float | None]:
    fundamentals = fundamentals_score(layers)
    m = layers.get("M")
    gap = fundamentals - m if fundamentals is not None and m is not None else None
    return {
        "overall": overall_score(layers),
        "fundamentals": round_half_up(fundamentals),
        "gap": round_half_up(gap),
    }


def gap_sentence(gap: float | None) -> str:
    if gap is None:
        return "Not enough data to compare fundamentals with market pricing."
    if gap >= C.GAP_THRESHOLD:
        return f"The data look worse than prices suggest (fundamentals {gap:+.0f} pts vs market)."
    if gap <= -C.GAP_THRESHOLD:
        return f"Prices are more worried than the data (fundamentals {gap:+.0f} pts vs market)."
    return "Market pricing is broadly in line with the fundamentals."


def plain_note(name: str, layers: dict[str, float | None], overall: float | None) -> str:
    avail = {k: v for k, v in layers.items() if k in C.OVERALL_WEIGHTS and v is not None}
    if overall is None or not avail:
        return f"Not enough data yet to score {name}."
    worst = max(avail, key=avail.get)
    best = min(avail, key=avail.get)
    return (f"{name} scores {overall:.0f} ({C.band_for(overall)}). "
            f"The biggest concern is {C.LAYER_NAMES[worst].lower()} ({avail[worst]:.0f}); "
            f"the least worrying is {C.LAYER_NAMES[best].lower()} ({avail[best]:.0f}).")


# ---------------------------------------------------------------------------
# Raw indicator assembly
# ---------------------------------------------------------------------------

def _best(rows: list[tuple[float | None, int | None, str]]) -> tuple[float | None, int | None, str | None]:
    rows = [r for r in rows if r[0] is not None]
    if not rows:
        return None, None, None
    return max(rows, key=lambda r: (SOURCE_RANK.get(r[2], 1), r[1] or 0))


def raw_indicators(session, include_illustrative: bool = True) -> dict[str, dict[str, dict[str, Any]]]:
    """{iso2: {indicator: {"value", "year", "source"}}} using source precedence."""
    from core.models import IndicatorValue, ManualInput

    grouped: dict[str, dict[str, list]] = {}
    for iv in session.execute(select(IndicatorValue)).scalars():
        grouped.setdefault(iv.iso2, {}).setdefault(iv.key, []).append((iv.value, iv.year, iv.source))
    manual = {m.iso2: m for m in session.execute(select(ManualInput)).scalars()}

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for c in C.COUNTRIES:
        iso2 = c["iso2"]
        g = grouped.get(iso2, {})
        m = manual.get(iso2)
        # Manual CSV values override API values.
        if m is not None:
            for field in ("spread_bp", "spread_1m_bp", "cds_5y_bp", "local_10y", "fx_1m_pct", "fx_12m_pct", "text_signal"):
                v = getattr(m, field)
                if v is not None:
                    g.setdefault(field, []).append((v, m.updated_at.year if m.updated_at else None,
                                                    (m.sources or {}).get(field, "manual")))
        vals: dict[str, dict[str, Any]] = {}
        for key, rows in g.items():
            if not include_illustrative:
                rows = [r for r in rows if r[2] != "illustrative"]
            v, y, s = _best(rows)
            if v is not None:
                vals[key] = {"value": v, "year": y, "source": s}

        def val(k):
            return vals.get(k, {}).get("value")

        lr = liquidity_ratio(val("reserves_usd"), val("short_term_debt_usd"), val("ext_debt_service_usd"))
        if lr is not None:
            years = [vals[k]["year"] for k in ("reserves_usd", "short_term_debt_usd", "ext_debt_service_usd") if vals[k].get("year")]
            vals["liquidity_ratio"] = {"value": lr, "year": min(years) if years else None, "source": "computed"}
        infl = vals.get("inflation_imf") or vals.get("inflation_wb")
        if infl:
            vals["inflation"] = infl
        out[iso2] = vals
    return out


def indicator_values(raw: dict[str, dict[str, Any]]) -> dict[str, float | None]:
    return {k: v.get("value") for k, v in raw.items()}


# ---------------------------------------------------------------------------
# DB-backed services
# ---------------------------------------------------------------------------

def recompute_scores() -> dict[str, list[str]]:
    """Recompute layers from non-illustrative raw data; keep stored layers where data is missing.

    Returns {iso2: [layers recomputed]} for the status report.
    """
    from core.db import session_scope
    from core.models import CountryScore, TextSignal

    report: dict[str, list[str]] = {}
    with session_scope() as s:
        raw = raw_indicators(s, include_illustrative=False)
        latest_signal: dict[str, TextSignal] = {}
        for ts in s.execute(select(TextSignal).order_by(TextSignal.created_at)).scalars():
            latest_signal[ts.iso2] = ts
        for c in C.COUNTRIES:
            iso2 = c["iso2"]
            row = s.get(CountryScore, iso2) or CountryScore(iso2=iso2, layer_sources={})
            vals = indicator_values(raw.get(iso2, {}))
            computed = layer_scores(vals)
            sources = dict(row.layer_sources or {})
            changed = []
            for layer, keys in C.LAYER_INDICATORS.items():
                n_avail = sum(1 for k in keys if vals.get(k) is not None)
                if computed[layer] is not None and n_avail >= C.MIN_INDICATORS_PER_LAYER[layer]:
                    setattr(row, layer, round_half_up(computed[layer], 1))
                    sources[layer] = "computed"
                    changed.append(layer)
            if iso2 in latest_signal:
                row.T = latest_signal[iso2].score
                sources["T"] = "ai" if latest_signal[iso2].ai_used else "keywords"
                changed.append("T")
            elif vals.get("text_signal") is not None:
                row.T = max(0.0, min(100.0, vals["text_signal"]))
                sources["T"] = "manual"
                changed.append("T")
            before = row.overall
            layers = {k: getattr(row, k) for k in "LSFM"}
            for k, v in compute_derived(layers).items():
                setattr(row, k, v)
            # risk_1m_change needs a month-old score; keep the stored value unless it can be derived.
            if row.risk_1m_change is None and before is not None and row.overall is not None:
                row.risk_1m_change = row.overall - before
            row.layer_sources = sources
            row.computed_at = datetime.now(timezone.utc)
            s.merge(row)
            if changed:
                report[iso2] = changed
    return report


def country_scores() -> pd.DataFrame:
    """One row per covered country: layers, overall, fundamentals, gap, band, flags, note, sources."""
    from core.db import session_scope
    from core.models import Country, CountryNote, CountryScore

    with session_scope() as s:
        countries = {c.iso2: c for c in s.execute(select(Country)).scalars()}
        scores = {r.iso2: r for r in s.execute(select(CountryScore)).scalars()}
        notes = {n.iso2: n for n in s.execute(select(CountryNote)).scalars()}
        raw = raw_indicators(s)

    rows = []
    for c in C.COUNTRIES:
        iso2 = c["iso2"]
        sc = scores.get(iso2)
        nt = notes.get(iso2)
        layers = {k: getattr(sc, k) if sc else None for k in "LSFMT"}
        overall = sc.overall if sc else None
        flags = dict(nt.flags or {}) if nt else {}
        flags["oil"] = countries[iso2].oil if iso2 in countries else c["oil"]
        rows.append({
            "iso2": iso2, "iso3": c["iso3"], "name": c["name"], "region": c["region"],
            "currency": c["currency"], "oil": c["oil"],
            **layers,
            "overall": overall,
            "fundamentals": sc.fundamentals if sc else None,
            "gap": sc.gap if sc else None,
            "band": C.band_for(overall),
            "risk_1m_change": sc.risk_1m_change if sc else None,
            "flags": flags,
            "note": (nt.note if nt and nt.note else plain_note(c["name"], layers, overall)),
            "gap_sentence": gap_sentence(sc.gap if sc else None),
            "driver": nt.driver if nt else None,
            "layer_sources": dict(sc.layer_sources or {}) if sc else {},
            "source_years": {k: v.get("year") for k, v in raw.get(iso2, {}).items()},
        })
    df = pd.DataFrame(rows)
    return df.sort_values("overall", ascending=False, na_position="last").reset_index(drop=True)


def country_detail(iso2: str) -> dict[str, Any]:
    """Raw indicators (value, year, source, indicator score) plus the country's score row."""
    from core.db import session_scope

    iso2 = iso2.upper()
    with session_scope() as s:
        raw = raw_indicators(s).get(iso2, {})
    scores = country_scores()
    row = scores[scores["iso2"] == iso2]
    indicators = {
        k: {**v, "score": indicator_score(k, v["value"]) if k in C.INDICATOR_RANGES else None}
        for k, v in raw.items()
    }
    return {"iso2": iso2, "scores": row.iloc[0].to_dict() if len(row) else {}, "indicators": indicators}
