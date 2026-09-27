"""Portfolio look-through (spec §6.4)."""
from __future__ import annotations

import csv
import io
import math
from typing import Any

import pandas as pd
from sqlalchemy import select

from core import config as C
from core.scoring import round_half_up

# ---------------------------------------------------------------------------
# Pure formulas
# ---------------------------------------------------------------------------


def _num(x) -> float | None:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return float(x)


def normalize_weights(weights: dict[str, float], covered: set[str] | None = None) -> dict[str, float]:
    """ETF country share = fund weight ÷ sum of covered weights (uncovered countries dropped)."""
    covered = covered if covered is not None else set(C.ISO2_TO_ISO3)
    kept = {k: float(w) for k, w in weights.items() if k in covered and w and w > 0}
    total = sum(kept.values())
    return {k: w / total for k, w in kept.items()} if total > 0 else {}


def book_weights(holdings: list[dict]) -> dict[str, float]:
    """Book country weight = Σ (amount × country share) ÷ total amount × 100."""
    total = sum(h["amount_k"] for h in holdings)
    if total <= 0:
        return {}
    out: dict[str, float] = {}
    for h in holdings:
        for iso2, share in h["shares"].items():
            out[iso2] = out.get(iso2, 0.0) + h["amount_k"] * share
    return {k: v / total * 100.0 for k, v in out.items()}


def holding_risk(shares: dict[str, float], risk: dict[str, float | None]) -> float | None:
    """Σ share × country overall risk (renormalized over countries that have a score)."""
    pairs = [(s, risk.get(k)) for k, s in shares.items() if _num(risk.get(k)) is not None]
    tot = sum(s for s, _ in pairs)
    return sum(s * r for s, r in pairs) / tot if tot else None


def portfolio_risk(weights: dict[str, float], risk: dict[str, float | None]) -> float | None:
    """Book-weighted average of country overall risk."""
    return holding_risk(weights, risk)


def pct_on_alert(weights: dict[str, float], risk: dict[str, float | None]) -> float:
    """Book weight in countries with risk ≥ 55."""
    return sum(w for k, w in weights.items() if (_num(risk.get(k)) or 0) >= C.ALERT_THRESHOLD)


def portfolio_yield(holdings: list[dict]) -> float | None:
    total = sum(h["amount_k"] for h in holdings)
    return sum(h["amount_k"] * h["yield_pct"] for h in holdings) / total if total > 0 else None


def one_month_return(ccy: str, yield_pct: float, shares: dict[str, float], fx_1m: dict[str, float | None]) -> dict[str, float]:
    """carry = yield/12; FX = 0 for USD, else Σ share × country 1M FX return (negative = weaker)."""
    carry = yield_pct / 12.0
    if ccy == "USD":
        fx = 0.0
    else:
        fx = sum(s * (_num(fx_1m.get(k)) or 0.0) for k, s in shares.items())
    return {"carry": carry, "fx": fx, "net": carry + fx}


def paid_for_risk(net: float, h_risk: float | None) -> str:
    if net <= 0:
        return "No"
    if h_risk is not None and h_risk >= C.ALERT_THRESHOLD:
        return "Check"
    return "Yes"


PAID_EXPLAIN = {
    "No": "The currency is eating the yield.",
    "Check": "Yield is positive, but the risk is on alert.",
    "Yes": "The yield more than covers the currency move.",
}


def benchmark_weights(fund_weights: list[dict[str, float]]) -> dict[str, float]:
    """Sample blended EM index = average of the sample funds' (normalized) weights, in %."""
    if not fund_weights:
        return {}
    norm = [normalize_weights(w) for w in fund_weights]
    keys = set().union(*norm)
    return {k: sum(n.get(k, 0.0) for n in norm) / len(norm) * 100.0 for k in keys}


def over_under(book: dict[str, float], bench: dict[str, float], min_diff: float = C.BENCHMARK_MIN_DIFF) -> dict[str, float]:
    diffs = {k: book.get(k, 0.0) - bench.get(k, 0.0) for k in set(book) | set(bench)}
    return {k: d for k, d in diffs.items() if abs(d) >= min_diff}


def parse_weights_csv(data: bytes) -> tuple[dict[str, float], list[str]]:
    """Two columns: country (name/ISO2/ISO3) and weight. Returns (covered weights, skipped names)."""
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    weights: dict[str, float] = {}
    skipped: list[str] = []
    for row in csv.reader(io.StringIO(text), dialect):
        if len(row) < 2 or not row[0].strip():
            continue
        try:
            w = float(row[1].strip().replace("%", "").replace(",", "."))
        except ValueError:
            continue  # header or junk row
        iso2 = C.to_iso2(row[0])
        if iso2 is None:
            skipped.append(row[0].strip())
            continue
        weights[iso2] = weights.get(iso2, 0.0) + w
    return weights, skipped


# ---------------------------------------------------------------------------
# DB-backed services
# ---------------------------------------------------------------------------


def _market_inputs() -> tuple[dict[str, float | None], dict[str, float | None], pd.DataFrame]:
    from core.markets import benchmarks

    b = benchmarks()
    risk = dict(zip(b["iso2"], b["risk"]))
    fx = dict(zip(b["iso2"], b["fx_1m"]))
    return risk, fx, b


def fund_weights(session) -> dict[int, dict[str, float]]:
    from core.models import FundWeight

    out: dict[int, dict[str, float]] = {}
    for fw in session.execute(select(FundWeight)).scalars():
        out.setdefault(fw.fund_id, {})[fw.iso2] = fw.weight
    return out


def _raw_holdings() -> list[dict]:
    from core.db import session_scope
    from core.models import Fund, Holding

    with session_scope() as s:
        fw = fund_weights(s)
        funds = {f.id: f for f in s.execute(select(Fund)).scalars()}
        rows = []
        for h in s.execute(select(Holding).order_by(Holding.id)).scalars():
            shares = normalize_weights(fw.get(h.fund_id, {})) if h.type == "ETF" else ({h.iso2: 1.0} if h.iso2 else {})
            rows.append(dict(id=h.id, type=h.type, name=h.name, ccy=h.ccy, yield_pct=h.yield_pct,
                             maturity=h.maturity, amount_k=h.amount_k, iso2=h.iso2, fund_id=h.fund_id,
                             fund_name=funds[h.fund_id].name if h.fund_id in funds else None, shares=shares))
    return rows


def holdings() -> pd.DataFrame:
    """Holdings with calculated fields: weight, risk, 1m carry/FX/net, Paid for risk?"""
    raw = _raw_holdings()
    risk, fx, _ = _market_inputs()
    total = sum(h["amount_k"] for h in raw)
    names = {c["iso2"]: c["name"] for c in C.COUNTRIES}
    rows = []
    for h in raw:
        hr = holding_risk(h["shares"], risk)
        ret = one_month_return(h["ccy"], h["yield_pct"], h["shares"], fx)
        paid = paid_for_risk(ret["net"], hr)
        if h["type"] == "ETF":
            subtitle = f"{len(h['shares'])} countries inside"
        else:
            subtitle = " · ".join(x for x in (names.get(h["iso2"], h["iso2"] or ""), h["maturity"] or "") if x)
        rows.append({
            **{k: h[k] for k in ("id", "type", "name", "ccy", "yield_pct", "maturity", "amount_k", "iso2", "fund_id", "shares")},
            "subtitle": subtitle,
            "n_countries": len(h["shares"]),
            "weight": h["amount_k"] / total * 100.0 if total else 0.0,
            "risk": hr, "band": C.band_for(hr),
            "carry_1m": ret["carry"], "fx_1m": ret["fx"], "net_1m": ret["net"],
            "paid": paid, "paid_explain": PAID_EXPLAIN[paid],
        })
    return pd.DataFrame(rows, columns=[
        "id", "type", "name", "subtitle", "ccy", "yield_pct", "maturity", "amount_k", "iso2", "fund_id", "shares",
        "n_countries", "weight", "risk", "band", "carry_1m", "fx_1m", "net_1m", "paid", "paid_explain"])


def lookthrough() -> dict[str, Any]:
    """Book country weights, % on alert, oil-importer share and KPIs."""
    raw = _raw_holdings()
    risk, fx, bm = _market_inputs()
    weights = book_weights(raw)
    via: dict[str, set[str]] = {}
    for h in raw:
        for iso2 in h["shares"]:
            via.setdefault(iso2, set()).add("ETF" if h["type"] == "ETF" else "bond")
    meta = {c["iso2"]: c for c in C.COUNTRIES}
    table = pd.DataFrame([
        {"iso2": k, "iso3": meta[k]["iso3"], "name": meta[k]["name"], "region": meta[k]["region"],
         "oil": meta[k]["oil"], "weight": w, "risk": risk.get(k), "band": C.band_for(risk.get(k)),
         "via": via_text(via.get(k, set()))}
        for k, w in weights.items()
    ], columns=["iso2", "iso3", "name", "region", "oil", "weight", "risk", "band", "via"])
    table = table.sort_values("weight", ascending=False).reset_index(drop=True)
    hdf = holdings()
    total = float(hdf["amount_k"].sum()) if len(hdf) else 0.0
    prisk = portfolio_risk(weights, risk)
    est_1m = float((hdf["net_1m"] * hdf["amount_k"]).sum() / total) if total else None
    return {
        "table": table,
        "weights": weights,
        "total_k": total,
        "n_etfs": int((hdf["type"] == "ETF").sum()) if len(hdf) else 0,
        "n_bonds": int((hdf["type"] == "Bond").sum()) if len(hdf) else 0,
        "portfolio_yield": portfolio_yield(raw),
        "portfolio_risk": prisk,
        "portfolio_band": C.band_for(round_half_up(prisk)),
        "pct_on_alert": pct_on_alert(weights, risk),
        "oil_importer_share": sum(w for k, w in weights.items() if meta[k]["oil"] == "importer"),
        "est_1m_return": est_1m,
        "region_weights": table.groupby("region")["weight"].sum().to_dict() if len(table) else {},
    }


def via_text(via: set[str]) -> str:
    if via == {"ETF", "bond"}:
        return "held via ETF and bond"
    if via == {"ETF"}:
        return "held via ETF"
    if via == {"bond"}:
        return "held via bond"
    return ""


def vs_benchmark() -> pd.DataFrame:
    """Over/underweights (points) vs the sample blended EM index; |diff| < 0.5 hidden."""
    from core.db import session_scope
    from core.models import Fund

    with session_scope() as s:
        sample_ids = [f.id for f in s.execute(select(Fund).where(Fund.is_sample.is_(True))).scalars()]
        fw = fund_weights(s)
    bench = benchmark_weights([fw[i] for i in sample_ids if i in fw])
    lt = lookthrough()
    diffs = over_under(lt["weights"], bench)
    risk, _, _ = _market_inputs()
    names = {c["iso2"]: c["name"] for c in C.COUNTRIES}
    df = pd.DataFrame([
        {"iso2": k, "name": names[k], "book": lt["weights"].get(k, 0.0), "benchmark": bench.get(k, 0.0),
         "diff": d, "risk": risk.get(k), "band": C.band_for(risk.get(k))}
        for k, d in diffs.items()
    ], columns=["iso2", "name", "book", "benchmark", "diff", "risk", "band"])
    return df.sort_values("diff").reset_index(drop=True)


def alerts_and_concentrations() -> list[dict[str, Any]]:
    """Negative net carry, distressed exposures, single-country concentration, oil-importer share."""
    hdf = holdings()
    lt = lookthrough()
    items: list[dict[str, Any]] = []
    for _, h in hdf[hdf["net_1m"] <= 0].iterrows():
        items.append({"kind": "carry", "severity": "risk", "iso2": h["iso2"],
                      "title": f"{h['name']}: the currency is eating the yield",
                      "text": f"1-month carry {h['carry_1m']:+.2f}% vs FX {h['fx_1m']:+.2f}% → net {h['net_1m']:+.2f}%."})
    for _, r in lt["table"].iterrows():
        if r["risk"] is not None and r["risk"] >= C.DISTRESS_THRESHOLD:
            items.append({"kind": "distress", "severity": "risk", "iso2": r["iso2"],
                          "title": f"{r['name']} is in distress (risk {r['risk']:.0f})",
                          "text": f"{r['weight']:.1f}% of the book, {r['via']}."})
    for _, r in lt["table"].iterrows():
        if r["weight"] >= C.CONCENTRATION_THRESHOLD:
            items.append({"kind": "concentration", "severity": "watch", "iso2": r["iso2"],
                          "title": f"Concentration: {r['name']} is {r['weight']:.1f}% of the book",
                          "text": f"Above the {C.CONCENTRATION_THRESHOLD:.0f}% single-country threshold, {r['via']}."})
    if lt["oil_importer_share"] >= C.OIL_IMPORTER_THRESHOLD:
        items.append({"kind": "oil", "severity": "watch", "iso2": None,
                      "title": f"Oil importers are {lt['oil_importer_share']:.0f}% of the book",
                      "text": f"Above {C.OIL_IMPORTER_THRESHOLD:.0f}%: an oil spike hits these currencies and budgets together."})
    return items


def add_bond(iso2: str, name: str, ccy: str, yield_pct: float, maturity: str | None, amount_k: float) -> int:
    from core.db import session_scope
    from core.models import Holding

    iso2 = C.to_iso2(iso2) or ""
    if iso2 not in C.ISO2_TO_ISO3:
        raise ValueError("Country is not covered.")
    if ccy not in ("USD", "Local"):
        raise ValueError("Currency must be USD or Local.")
    if amount_k <= 0:
        raise ValueError("Amount must be positive.")
    with session_scope() as s:
        h = Holding(type="Bond", iso2=iso2, name=name.strip() or f"{iso2} bond", ccy=ccy,
                    yield_pct=float(yield_pct), maturity=(maturity or None), amount_k=float(amount_k))
        s.add(h)
        s.flush()
        return h.id


def remove_holding(holding_id: int) -> bool:
    from core.db import session_scope
    from core.models import Holding

    with session_scope() as s:
        h = s.get(Holding, int(holding_id))
        if h is None:
            return False
        s.delete(h)
        return True


def import_etf(csv_bytes: bytes, name: str, amount_k: float = 100.0, ccy: str = "USD") -> dict[str, Any]:
    """Create a fund from a (country, weight) CSV and add it to the book.

    Imported ETF yield = weighted USD 10Y of its covered countries.
    """
    from core.db import session_scope
    from core.markets import benchmarks
    from core.models import Fund, FundWeight, Holding

    weights, skipped = parse_weights_csv(csv_bytes)
    shares = normalize_weights(weights)
    if not shares:
        raise ValueError("No covered countries found in the file (expected two columns: country or ISO, weight).")
    b = benchmarks().set_index("iso2")
    pairs = [(s, b.loc[k, "usd_10y"]) for k, s in shares.items() if pd.notna(b.loc[k, "usd_10y"])]
    tot = sum(s for s, _ in pairs)
    yld = sum(s * y for s, y in pairs) / tot if tot else 0.0
    with session_scope() as s:
        f = Fund(name=name, is_sample=False)
        s.add(f)
        s.flush()
        for iso2, w in weights.items():
            s.add(FundWeight(fund_id=f.id, iso2=iso2, weight=w))
        h = Holding(type="ETF", fund_id=f.id, name=name, ccy=ccy, yield_pct=round(yld, 2), amount_k=float(amount_k))
        s.add(h)
        s.flush()
        return {"fund_id": f.id, "holding_id": h.id, "yield_pct": round(yld, 2),
                "countries": len(shares), "skipped": skipped}
