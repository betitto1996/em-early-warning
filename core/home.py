"""Home view: KPIs, map data and ranked alerts (spec §6.5, §8.1)."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

from core import config as C


def _n(x) -> float:
    return 0.0 if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def alert_impact(weight: float, risk: float | None, risk_1m: float | None, fx_1m: float | None,
                 spread_1m_bp: float | None) -> float:
    """impact = weight × (|risk 1M| + |FX 1M| + (2 if risk ≥ 55) + |spread 1M|/25 + 0.5)."""
    bonus = C.HOME_ALERT_ALERT_BONUS if _n(risk) >= C.ALERT_THRESHOLD else 0.0
    return weight * (abs(_n(risk_1m)) + abs(_n(fx_1m)) + bonus
                     + abs(_n(spread_1m_bp)) / C.HOME_ALERT_SPREAD_DIVISOR + C.HOME_ALERT_BASE)


def alert_tag(risk_1m: float | None, fx_1m: float | None, spread_1m_bp: float | None) -> str:
    """Risk if risk rose, FX fell or spread widened ≥ 20bp; Positive if risk fell and none of those; else Watch."""
    worse = _n(risk_1m) > 0 or _n(fx_1m) < 0 or _n(spread_1m_bp) >= C.HOME_ALERT_SPREAD_WIDEN_BP
    if worse:
        return "Risk"
    if _n(risk_1m) < 0:
        return "Positive"
    return "Watch"


def moves_text(risk_1m, fx_1m, spread_1m_bp, via: str) -> str:
    parts = []
    if risk_1m is not None and not (isinstance(risk_1m, float) and math.isnan(risk_1m)):
        parts.append(f"risk {risk_1m:+.0f} pts".replace("-", "−") if risk_1m else "risk unchanged")
    if fx_1m is not None and not (isinstance(fx_1m, float) and math.isnan(fx_1m)) and fx_1m != 0:
        parts.append(f"currency {fx_1m:+.1f}%".replace("-", "−"))
    if spread_1m_bp is not None and not (isinstance(spread_1m_bp, float) and math.isnan(spread_1m_bp)):
        parts.append(f"spread {spread_1m_bp:+.0f} bp".replace("-", "−"))
    if via:
        parts.append(via)
    return " · ".join(parts)


def rank_alerts(lookthrough_table: pd.DataFrame, market: pd.DataFrame, top: int = C.HOME_ALERT_TOP) -> list[dict[str, Any]]:
    m = market.set_index("iso2")
    items = []
    for _, r in lookthrough_table.iterrows():
        if r["weight"] < C.HOME_ALERT_MIN_WEIGHT or r["iso2"] not in m.index:
            continue
        mk = m.loc[r["iso2"]]
        items.append({
            "iso2": r["iso2"], "name": r["name"], "weight": r["weight"], "risk": mk["risk"], "band": mk["band"],
            "impact": alert_impact(r["weight"], mk["risk"], mk["risk_1m"], mk["fx_1m"], mk["spread_1m_bp"]),
            "tag": alert_tag(mk["risk_1m"], mk["fx_1m"], mk["spread_1m_bp"]),
            "driver": mk["driver"],
            "moves": moves_text(mk["risk_1m"], mk["fx_1m"], mk["spread_1m_bp"], r["via"]),
            "via": r["via"],
        })
    items.sort(key=lambda x: x["impact"], reverse=True)
    return items[:top]


def home_view() -> dict[str, Any]:
    """Summary inputs, KPIs, map data and ranked alerts."""
    from core.markets import relative_value, ust10
    from core.portfolio import holdings, lookthrough

    lt = lookthrough()
    rv = relative_value()
    market = rv["table"]
    hdf = holdings()
    weights = lt["weights"]
    opp = [k for k in rv["top_opportunities"] if weights.get(k, 0.0) < C.OPPORTUNITY_MAX_BOOK_WEIGHT]
    names = dict(zip(market["iso2"], market["name"]))
    alerts = rank_alerts(lt["table"], market)
    base = ust10()

    # Map data: all covered countries; held ones carry a weight.
    map_df = market[["iso2", "iso3", "name", "risk", "band", "signal"]].copy()
    map_df["weight"] = map_df["iso2"].map(weights).fillna(0.0)
    map_df["held"] = map_df["weight"] > 0

    positives = market[(market["risk_1m"] < 0)].sort_values("risk_1m")
    strongest_positive = None
    if len(positives):
        p = positives.iloc[0]
        strongest_positive = {"iso2": p["iso2"], "name": p["name"], "risk_1m": p["risk_1m"], "driver": p["driver"]}

    return {
        "kpis": {
            "value_k": lt["total_k"], "n_etfs": lt["n_etfs"], "n_bonds": lt["n_bonds"],
            "yield": lt["portfolio_yield"], "ust10": base,
            "yield_pickup_bp": (lt["portfolio_yield"] - base) * 100 if lt["portfolio_yield"] is not None else None,
            "risk": lt["portfolio_risk"], "band": lt["portfolio_band"], "pct_on_alert": lt["pct_on_alert"],
            "opportunities": opp, "opportunity_names": [names[k] for k in opp],
        },
        "map": map_df,
        "alerts": alerts,
        "lookthrough": lt,
        "holdings": hdf,
        "strongest_positive": strongest_positive,
        "top_opportunities": rv["top_opportunities"],
    }
