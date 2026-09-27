"""Data fetchers: World Bank, IMF WEO (DataMapper), Frankfurter (ECB), FRED.

Raw responses are cached on disk (CACHE_DIR) so the demo can run offline:
on a network failure the last cached response is used. API keys are never
written to cache file names or logs.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy import delete

from core import config as C

log = logging.getLogger(__name__)

WB_INDICATORS = {
    "FI.RES.TOTL.CD": "reserves_usd",
    "FI.RES.TOTL.MO": "reserves_months_imports",
    "DT.DOD.DSTC.CD": "short_term_debt_usd",
    "DT.TDS.DECT.CD": "ext_debt_service_usd",
    "GC.XPN.INTP.RV.ZS": "interest_pct_revenue",
    "FP.CPI.TOTL.ZG": "inflation_wb",
}
IMF_INDICATORS = {
    "GGXWDG_NGDP": "gov_debt_pct_gdp",
    "PCPIPCH": "inflation_imf",
    "BCA_NGDPD": "current_account_pct_gdp",
}
# Frankfurter (ECB reference rates) publishes only ~30 currencies. Of the 18 covered countries it has
# BRL HUF IDR MXN TRY ZAR; XOF is derived from EUR (fixed CFA peg). Symbols it does not publish are silently
# dropped from its response, so they are not requested. The other 11 (ARS CLP COP PEN PKR KES LKR ZMW NGN EGP
# GHS) must come from Bloomberg via data/manual_inputs.csv (spec §4).
FRANKFURTER_SYMBOLS = ["BRL", "HUF", "IDR", "MXN", "TRY", "ZAR", "EUR"]


# ---------------------------------------------------------------------------
# HTTP with retries + disk cache
# ---------------------------------------------------------------------------


def _cache_path(url: str, params: dict | None):
    safe = {k: v for k, v in (params or {}).items() if "key" not in k.lower()}
    digest = hashlib.sha256((url + "?" + urlencode(sorted(safe.items()))).encode()).hexdigest()[:24]
    d = C.get_settings().cache_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{digest}.json"


def get_json(url: str, params: dict | None = None, retries: int = 2, timeout: float = 20.0) -> tuple[Any, str]:
    """GET JSON; returns (data, "live" | "cache"). Raises if neither network nor cache works."""
    path = _cache_path(url, params)
    last_err: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with httpx.Client(timeout=timeout, follow_redirects=True) as client:
                # Default httpx User-Agent: the IMF API rejects some custom ones with 403.
                r = client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            path.write_text(json.dumps(data))
            return data, "live"
        except Exception as e:  # network, HTTP or JSON error
            last_err = e
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
    if path.exists():
        return json.loads(path.read_text()), "cache"
    raise RuntimeError(f"{type(last_err).__name__} and no cached response") from None


# ---------------------------------------------------------------------------
# Parsers (pure; unit-tested)
# ---------------------------------------------------------------------------


def parse_world_bank(payload: Any, key: str) -> list[dict]:
    """Latest non-null value per country from a World Bank v2 response."""
    if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
        return []
    best: dict[str, dict] = {}
    for row in payload[1]:
        iso3 = row.get("countryiso3code") or ""
        iso2 = C.ISO3_TO_ISO2.get(iso3)
        if iso2 is None or row.get("value") is None:
            continue
        year = int(row["date"])
        if iso2 not in best or year > best[iso2]["year"]:
            best[iso2] = {"iso2": iso2, "key": key, "value": float(row["value"]), "year": year, "source": "worldbank"}
    return list(best.values())


def parse_imf(payload: Any, code: str, key: str, max_year: int) -> list[dict]:
    """Latest year ≤ max_year per country from an IMF DataMapper response: values[CODE][ISO3][year]."""
    series = (payload or {}).get("values", {}).get(code, {})
    out = []
    for iso3, by_year in series.items():
        iso2 = C.ISO3_TO_ISO2.get(iso3)
        if iso2 is None:
            continue
        years = sorted((int(y) for y, v in by_year.items() if v is not None and int(y) <= max_year), reverse=True)
        if years:
            y = years[0]
            out.append({"iso2": iso2, "key": key, "value": float(by_year[str(y)]), "year": y, "source": "imf"})
    return out


def parse_frankfurter(payload: Any, today: date | None = None) -> list[dict]:
    """fx_1m_pct and fx_12m_pct (+ = local currency weakened vs USD) from USD-based rates."""
    rates: dict[str, dict[str, float]] = (payload or {}).get("rates", {})
    if not rates:
        return []
    dates = sorted(rates)
    last = dates[-1]
    last_d = date.fromisoformat(last)

    def on_or_before(target: date) -> str | None:
        cands = [d for d in dates if date.fromisoformat(d) <= target]
        return cands[-1] if cands else None

    d1m = on_or_before(last_d - timedelta(days=30))
    d12m = on_or_before(last_d - timedelta(days=365))
    ccy_to_iso2 = {c["currency"]: c["iso2"] for c in C.COUNTRIES}

    def rate(d: str, ccy: str) -> float | None:
        r = rates.get(d, {})
        if ccy == "XOF":
            return r["EUR"] * C.XOF_PER_EUR if "EUR" in r else None
        return r.get(ccy)

    out = []
    for ccy in [s for s in FRANKFURTER_SYMBOLS if s != "EUR"] + ["XOF"]:
        iso2 = ccy_to_iso2.get(ccy)
        now = rate(last, ccy)
        if iso2 is None or not now:
            continue
        for key, past_d in (("fx_1m_pct", d1m), ("fx_12m_pct", d12m)):
            past = rate(past_d, ccy) if past_d else None
            if past:
                # Rates are local currency per USD: a higher number = weaker local currency.
                out.append({"iso2": iso2, "key": key, "value": (now / past - 1.0) * 100.0,
                            "year": last_d.year, "source": "frankfurter"})
    return out


def parse_fred(payload: Any) -> dict | None:
    obs = [o for o in (payload or {}).get("observations", []) if o.get("value") not in (None, ".", "")]
    if not obs:
        return None
    last = obs[-1]
    prev = obs[-2] if len(obs) > 1 else None
    value = float(last["value"])
    change_bp = (value - float(prev["value"])) * 100 if prev else None
    return {"value": value, "date": last["date"], "change_bp": change_bp}


# FRED (OECD) 10-year government bond yields, monthly, verified to exist. The annual "…A156N" IDs and the
# 3-letter "IRLTLT01MEX156N"-style IDs do not exist in FRED; annual series are also years stale. FRED has no 10Y
# series for Brazil, Peru or Indonesia — those stay None (no data; never invented) and must come from the
# manual CSV.
FRED_LOCAL_10Y_SERIES: dict[str, str | None] = {
    "MX": "IRLTLT01MXM156N",
    "CL": "IRLTLT01CLM156N",
    "HU": "IRLTLT01HUM156N",
    "CO": "COLIRLTLT01STM",
    "BR": None,
    "PE": None,
    "ID": None,
}


def parse_fred_latest(payload: Any) -> dict | None:
    """Most recent non-missing observation from a FRED observations response (any sort order)."""
    obs = [o for o in (payload or {}).get("observations", []) if o.get("value") not in (None, ".", "")]
    if not obs:
        return None
    last = max(obs, key=lambda o: o["date"])
    return {"value": float(last["value"]), "date": last["date"]}


# ---------------------------------------------------------------------------
# Fetch jobs
# ---------------------------------------------------------------------------

_ISO3_LIST = [c["iso3"] for c in C.COUNTRIES]


def fetch_world_bank() -> tuple[list[dict], str]:
    rows, modes = [], set()
    for code, key in WB_INDICATORS.items():
        url = f"https://api.worldbank.org/v2/country/{';'.join(_ISO3_LIST)}/indicator/{code}"
        data, mode = get_json(url, {"format": "json", "date": "2015:2026", "per_page": 2000})
        modes.add(mode)
        rows += parse_world_bank(data, key)
    return rows, "cache" if "cache" in modes else "live"


def fetch_imf() -> tuple[list[dict], str]:
    s = C.get_settings()
    periods = ",".join(str(y) for y in range(s.imf_year - 4, s.imf_year + 1))
    rows, modes = [], set()
    for code, key in IMF_INDICATORS.items():
        url = f"https://www.imf.org/external/datamapper/api/v1/{code}/{'/'.join(_ISO3_LIST)}"
        data, mode = get_json(url, {"periods": periods})
        modes.add(mode)
        rows += parse_imf(data, code, key, s.imf_year)
    return rows, "cache" if "cache" in modes else "live"


def fetch_frankfurter() -> tuple[list[dict], str]:
    start = (date.today() - timedelta(days=400)).isoformat()
    data, mode = get_json(f"https://api.frankfurter.dev/v1/{start}..",
                          {"base": "USD", "symbols": ",".join(FRANKFURTER_SYMBOLS)})
    return parse_frankfurter(data), mode


def fx_report() -> list[dict]:
    """One row per covered country currency: fx_1m_pct / fx_12m_pct as stored (+ = local currency weaker vs USD)
    and a status: ok (Frankfurter), derived (XOF from EUR), failed (not published by Frankfurter, or no data)."""
    from core.db import session_scope
    from core.models import IndicatorValue
    from sqlalchemy import select

    with session_scope() as s:
        stored = {(r.iso2, r.key): r.value for r in s.execute(
            select(IndicatorValue).where(IndicatorValue.source == "frankfurter")).scalars()}
    out = []
    for c in C.COUNTRIES:
        v1, v12 = stored.get((c["iso2"], "fx_1m_pct")), stored.get((c["iso2"], "fx_12m_pct"))
        if v1 is None and v12 is None:
            status = "failed"
        else:
            status = "derived" if c["currency"] == "XOF" else "ok"
        out.append({"currency": c["currency"], "iso2": c["iso2"], "country": c["name"],
                    "fx_1m_pct": v1, "fx_12m_pct": v12, "status": status})
    return out


def fetch_fred() -> tuple[dict | None, str]:
    s = C.get_settings()
    if not s.fred_api_key:
        return None, "skipped"
    start = (date.today() - timedelta(days=30)).isoformat()
    data, mode = get_json("https://api.stlouisfed.org/fred/series/observations",
                          {"series_id": "DGS10", "api_key": s.fred_api_key, "file_type": "json",
                           "observation_start": start})
    return parse_fred(data), mode


def fetch_fred_local_yields() -> tuple[dict[str, dict], str]:
    """Latest FRED 10Y local yield per country: {iso2: {value, date, series}}. Empty dict (and a warning) on a
    missing key or failed request; each series fails independently."""
    s = C.get_settings()
    if not s.fred_api_key:
        log.warning("FRED_API_KEY is empty: skipping FRED local 10Y yields")
        return {}, "skipped"
    out: dict[str, dict] = {}
    modes: set[str] = set()
    for iso2, sid in FRED_LOCAL_10Y_SERIES.items():
        if sid is None:
            continue
        try:
            data, mode = get_json("https://api.stlouisfed.org/fred/series/observations",
                                  {"series_id": sid, "api_key": s.fred_api_key, "file_type": "json",
                                   "sort_order": "desc", "limit": 12})
        except Exception as e:
            log.warning("FRED %s (%s) failed: %s", sid, iso2, type(e).__name__)
            continue
        modes.add(mode)
        latest = parse_fred_latest(data)
        if latest:
            out[iso2] = {**latest, "series": sid}
    status = "cache" if "cache" in modes else ("live" if modes else "failed")
    return out, status


def _fred_decision(existing, existing_src, note, fred_value, obs_date) -> tuple[bool, str, float | None]:
    """Should a FRED yield replace `existing`? Returns (write, action, diff_bp).

    FRED replaces an *estimate* only when it looks consistent with the market (recent observation, within
    FRED_YIELD_MAX_DIFF_BP of the estimate). Confirmed values (source 'brief', or a note saying 'confirmed')
    are never replaced. FRED's own earlier value is simply refreshed.
    """
    if existing is None:
        return True, "written", None
    diff_bp = abs(fred_value - existing) * 100
    if existing_src == "fred":
        return True, "refreshed (same source)", diff_bp
    if existing_src == "brief" or "confirmed" in (note or "").lower():
        return False, "kept (confirmed team value)", diff_bp
    age = (date.today() - date.fromisoformat(obs_date)).days
    if age > C.FRED_YIELD_MAX_AGE_DAYS:
        return False, f"kept (FRED data {age}d old)", diff_bp
    if diff_bp > C.FRED_YIELD_MAX_DIFF_BP:
        return False, f"kept estimate (FRED {diff_bp:.0f}bp away, limit {C.FRED_YIELD_MAX_DIFF_BP}: review)", diff_bp
    return True, f"replaced estimate ({diff_bp:.0f}bp diff)", diff_bp


def apply_fred_local_yields(results: dict[str, dict]) -> list[dict]:
    """Apply FRED yields to ManualInput.local_10y (and data/manual_inputs.csv) under `_fred_decision`.
    Returns one row per configured country with what happened."""
    from core.db import session_scope
    from core.manual_inputs import fill_csv_empty_cells
    from core.models import ManualInput

    rows, to_csv = [], {}
    with session_scope() as s:
        for iso2, sid in FRED_LOCAL_10Y_SERIES.items():
            m = s.get(ManualInput, iso2)
            existing = m.local_10y if m is not None else None
            existing_src = (m.sources or {}).get("local_10y") if m is not None else None
            r = results.get(iso2)
            row = {"iso2": iso2, "fred_value": r["value"] if r else None, "period": r["date"][:7] if r else None,
                   "series": sid, "existing": existing, "existing_source": existing_src, "diff_bp": None}
            if r is None:
                row["action"] = "no FRED series" if sid is None else "no data"
            else:
                write, row["action"], row["diff_bp"] = _fred_decision(existing, existing_src, m.note if m else None,
                                                                      r["value"], r["date"])
                if write:
                    m = m or ManualInput(iso2=iso2, sources={})
                    m.local_10y = r["value"]
                    m.sources = {**(m.sources or {}), "local_10y": "fred"}
                    s.merge(m)
                    to_csv[iso2] = r["value"]
            rows.append(row)
    if to_csv:
        fill_csv_empty_cells("local_10y", to_csv, overwrite=True)
    return rows


GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
# ECON/WB GKG themes for debt-stress news, ORed together (spec-provided).
GDELT_QUERY_THEMES = ("ECON_DEBT", "ECON_BANKRUPTCY", "ECON_CURRENCY_EXCHANGE_RATE", "ECON_INFLATION",
                     "ECON_RESERVES", "WB_1282_FINANCIAL_CRISIS", "ECON_IMF", "ECON_CREDITRATING")
# GDELT filters by FIPS 10-4 country codes, not ISO — these differ from our ISO2 for 7 of the 18 countries
# (e.g. Turkey TR->TU, South Africa ZA->SF, Chile CL->CI, Senegal SN->SG, Sri Lanka LK->CE).
GDELT_FIPS = {"NG": "NI", "EG": "EG", "ZA": "SF", "TR": "TU", "HU": "HU", "BR": "BR", "MX": "MX",
             "ID": "ID", "CO": "CO", "CL": "CI", "PE": "PE", "AR": "AR", "SN": "SG", "GH": "GH",
             "KE": "KE", "PK": "PK", "LK": "CE", "ZM": "ZA"}


def parse_gdelt_articles(payload: Any) -> list[dict]:
    """GDELT DOC 2.0 'artlist' JSON -> [{title, url, date, tone, source}].

    GDELT returns `{}` (no "articles" key) when nothing matches — not an error. Per-article `tone` is not
    documented as part of ArtList's JSON fields (sort=Tone* only orders the results); if GDELT ever omits it,
    `tone` is None here rather than a guessed number, and callers must treat it as "no signal" (spec §15: never
    invent market/signal data).
    """
    out = []
    for a in (payload or {}).get("articles") or []:
        tone = a.get("tone")
        try:
            tone = float(tone) if tone is not None else None
        except (TypeError, ValueError):
            tone = None
        out.append({"title": a.get("title") or "(no title)", "url": a.get("url") or "",
                    "date": a.get("seendate") or "", "tone": tone,
                    "source": a.get("domain") or a.get("sourcecountry") or ""})
    return out


def fetch_gdelt_news(iso2: str) -> list[dict]:
    """Up to 5 recent debt/crisis-related articles (last 72h) for a country from GDELT. No API key.

    Best-effort and optional: GDELT enforces a shared, undocumented rate limit (its own error asks for at
    least 5s between requests) and can 429 under contention with no key to identify/prioritize a caller, so
    this always falls back to an empty list on any error rather than ever blocking the rest of a refresh.
    A single attempt only (no local retry storm) — repeated calls would just make the shared limit worse;
    the on-disk cache (see get_json) is the resilience mechanism across separate runs.
    """
    fips = GDELT_FIPS.get((iso2 or "").upper())
    if fips is None:
        return []
    query = f"({' OR '.join(GDELT_QUERY_THEMES)}) sourcecountry:{fips}"
    try:
        data, _mode = get_json(GDELT_URL, {"query": query, "mode": "artlist", "maxrecords": 5,
                                           "timespan": "72h", "format": "json", "sort": "ToneDesc"}, retries=0)
    except Exception as e:
        log.warning("GDELT fetch failed for %s (FIPS %s): %s", iso2, fips, type(e).__name__)
        return []
    return parse_gdelt_articles(data)


def gdelt_tone_to_score(tone: float) -> int:
    """0 (calm) .. 100 (acute distress) from a single GDELT tone value (-100..+100, more negative = more negative
    coverage). Same 0-100 convention as the statement text signal (spec §7.3)."""
    return min(100, max(0, int((-tone + 10) * 4)))


def gdelt_risk_signal(articles: list[dict]) -> tuple[int | None, float | None]:
    """(T, avg_tone) from a batch of articles' tone values (spec §5b: average tone first, convert once).
    (None, None) if none of the articles carry a tone value."""
    tones = [a["tone"] for a in articles if a.get("tone") is not None]
    if not tones:
        return None, None
    avg_tone = sum(tones) / len(tones)
    return gdelt_tone_to_score(avg_tone), avg_tone


def save_gdelt_news(iso2: str, articles: list[dict]) -> dict[str, Any]:
    """Persist each article as a TextSignal row (source='gdelt'); if any article carries a tone, update
    CountryScore.T for this country from the batch average tone. Returns a status dict for the CLI report."""
    from core.db import session_scope
    from core.models import CountryScore, TextSignal

    t, avg_tone = gdelt_risk_signal(articles)
    with session_scope() as s:
        for a in articles:
            s.add(TextSignal(
                iso2=iso2, source="gdelt", title=a["title"], url=a["url"], tone=a["tone"],
                published_at=a["date"], ai_used=False, phrases=[], summary=a["title"],
                score=float(gdelt_tone_to_score(a["tone"])) if a["tone"] is not None else 0.0,
                text_hash=hashlib.sha256((a["url"] or a["title"]).encode()).hexdigest(),
            ))
        if t is not None:
            row = s.get(CountryScore, iso2)
            if row is not None:
                row.T = float(t)
                row.layer_sources = {**(row.layer_sources or {}), "T": "gdelt"}
    return {"iso2": iso2, "n_articles": len(articles), "T": t, "avg_tone": avg_tone}


def _store(rows: list[dict], source: str) -> None:
    from core.db import session_scope
    from core.models import IndicatorValue

    keys = {r["key"] for r in rows}
    with session_scope() as s:
        # Replace this source's previous values for the fetched keys.
        if keys:
            s.execute(delete(IndicatorValue).where(IndicatorValue.source == source, IndicatorValue.key.in_(keys)))
        for r in rows:
            s.add(IndicatorValue(**r))


def _store_ust10(obs: dict, source: str) -> None:
    from core.db import session_scope
    from core.models import Macro

    with session_scope() as s:
        m = s.get(Macro, "UST10") or Macro(key="UST10", label="US 10Y",
                                          explanation="Higher US yields raise the bar EM bonds must clear.")
        m.value = obs["value"]
        m.display = f"{obs['value']:.2f}%"
        if obs.get("change_bp") is not None:
            m.change_text = f"{obs['change_bp']:+.0f} bp on the day".replace("-", "−")
        m.source = source
        m.as_of = obs["date"]
        s.merge(m)


def refresh_all(recompute: bool = True) -> dict[str, Any]:
    """Run every fetcher + manual CSV; recompute scores. Returns a status report per source."""
    from core.db import init_db
    from core.manual_inputs import load_manual_csv
    from core.scoring import recompute_scores

    init_db()
    report: dict[str, Any] = {}
    loaded_any = False
    for name, fn, source in (("World Bank", fetch_world_bank, "worldbank"), ("IMF WEO", fetch_imf, "imf"),
                             ("Frankfurter FX", fetch_frankfurter, "frankfurter")):
        try:
            rows, mode = fn()
            _store(rows, source)
            report[name] = {"status": mode, "rows": len(rows)}
            loaded_any = loaded_any or bool(rows)
        except Exception as e:
            report[name] = {"status": "failed", "rows": 0, "detail": str(e)[:200]}
    try:
        obs, mode = fetch_fred()
        if obs:
            _store_ust10(obs, "fred")
            report["FRED US 10Y"] = {"status": mode, "rows": 1, "detail": f"{obs['value']:.2f}% on {obs['date']}"}
        else:
            report["FRED US 10Y"] = {"status": mode, "rows": 0,
                                     "detail": f"No FRED key: using the stored US 10Y (fallback {C.get_settings().ust10_fallback}%)."}
    except Exception as e:
        report["FRED US 10Y"] = {"status": "failed", "rows": 0, "detail": str(e)[:200]}
    try:
        manual = load_manual_csv()
        report["Manual CSV"] = manual
        loaded_any = loaded_any or manual.get("rows", 0) > 0
    except Exception as e:
        report["Manual CSV"] = {"status": "failed", "rows": 0, "detail": str(e)[:200]}
    try:
        yields, mode = fetch_fred_local_yields()
        applied = apply_fred_local_yields(yields) if yields else []
        n_written = sum(1 for a in applied if a["action"].startswith(("written", "replaced", "refreshed")))
        report["FRED local 10Y"] = {"status": mode, "rows": n_written, "table": applied,
                                    "detail": f"{len(yields)} series fetched, {n_written} applied"}
    except Exception as e:
        report["FRED local 10Y"] = {"status": "failed", "rows": 0, "detail": str(e)[:200]}
    if recompute and loaded_any:
        report["Scores"] = {"status": "recomputed", "layers": recompute_scores()}
    else:
        report["Scores"] = {"status": "unchanged", "detail": "No new data; stored scores kept."}
    return report
