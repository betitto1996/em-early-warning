"""Gemini features (spec §7): briefs, Ask the analyst, statement text signal.

Every call falls back to deterministic text when the key is missing or the call
fails, and marks `ai_used=False`. The API key is only read server-side here and
is never logged or returned.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from typing import Any, Callable, Iterable, Iterator

from pydantic import BaseModel, Field

from core import config as C

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------

FORBIDDEN_WORDS = ["buy", "buys", "buying", "sell", "sells", "selling", "hold", "consider", "considering",
                   "reduce", "reducing"]
_FORBIDDEN_RE = re.compile(r"\b(" + "|".join(FORBIDDEN_WORDS) + r")\b", re.IGNORECASE)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def find_forbidden(text: str) -> list[str]:
    """Recommendation words present in `text` (lower-cased, unique, in order)."""
    seen: list[str] = []
    for m in _FORBIDDEN_RE.finditer(text or ""):
        w = m.group(1).lower()
        if w not in seen:
            seen.append(w)
    return seen


def filter_recommendations(text: str) -> tuple[str, list[str]]:
    """Drop sentences containing buy/sell/hold/consider/reduce language. Returns (clean, flagged)."""
    flagged = find_forbidden(text)
    if not flagged:
        return text, []
    kept = [s for s in _SENTENCE_END.split(text) if not _FORBIDDEN_RE.search(s)]
    return " ".join(kept).strip(), flagged


def _system_instruction(extra: str, data: dict) -> str:
    return (
        "You are an emerging-market sovereign debt analyst writing for financial advisors.\n"
        "Rules:\n"
        "- Use ONLY the JSON data provided below. If something isn't in the data, say so plainly.\n"
        "- Never recommend actions: do not use the words buy, sell, hold, consider or reduce, "
        "and do not tell anyone what to do with a position. Describe and compare only.\n"
        "- Answer in the user's language. Write 3-5 plain sentences, no bullet points, no headings.\n"
        "- Say 'Local 10Y' or 'USD 10Y' explicitly for yields; express spread and yield changes in basis points.\n"
        f"{extra}\n\nDATA (JSON):\n{json.dumps(data, default=_json_default, ensure_ascii=False)}"
    )


def _json_default(o):
    if isinstance(o, set):
        return sorted(o)
    if hasattr(o, "item"):
        return o.item()
    return str(o)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _client():
    """Return a genai client, or None when AI is disabled."""
    s = C.get_settings()
    if not s.ai_enabled:
        return None
    try:
        from google import genai
        from google.genai import types

        return genai.Client(api_key=s.gemini_api_key,
                            http_options=types.HttpOptions(timeout=s.ai_timeout_seconds * 1000))
    except Exception as e:  # pragma: no cover - depends on environment
        log.warning("Gemini client unavailable: %s", type(e).__name__)
        return None


class AIStream:
    """Iterable of text chunks with `ai_used`, `flagged` and `notice` filled in as it runs."""

    def __init__(self, producer: Callable[[], Iterable[str]] | None, fallback: Callable[[], str]):
        self._producer = producer
        self._fallback = fallback
        self.ai_used = False
        self.flagged: list[str] = []
        self.notice: str | None = None
        self.text = ""

    def __iter__(self) -> Iterator[str]:
        if self._producer is None:
            self.notice = "AI is off (no Gemini key), so this is the built-in summary."
            yield from self._emit_fallback()
            return
        emitted = False
        buffer = ""
        try:
            for piece in self._producer():
                buffer += piece
                # Release only whole sentences, after the recommendation filter.
                parts = _SENTENCE_END.split(buffer)
                buffer = parts.pop()
                for sentence in parts:
                    out = self._screen(sentence)
                    if out:
                        emitted = True
                        self.ai_used = True
                        yield out + " "
            out = self._screen(buffer)
            if out:
                emitted = True
                self.ai_used = True
                yield out
        except Exception as e:
            log.warning("Gemini call failed: %s", type(e).__name__)
            if emitted:
                self.notice = "The AI connection was interrupted."
                return
            self.notice = "AI is unavailable right now, so this is the built-in summary."
            yield from self._emit_fallback()
            return
        if not emitted:
            self.notice = "AI returned no usable text, so this is the built-in summary."
            yield from self._emit_fallback()
        elif self.flagged:
            self.notice = "Sentences with recommendation language were removed."

    def _screen(self, sentence: str) -> str:
        sentence = sentence.strip()
        if not sentence:
            return ""
        bad = find_forbidden(sentence)
        if bad:
            self.flagged.extend(w for w in bad if w not in self.flagged)
            return ""
        self.text += sentence + " "
        return sentence

    def _emit_fallback(self) -> Iterator[str]:
        text, _ = filter_recommendations(self._fallback())
        self.text = text
        yield text


def _stream_from_gemini(system: str, contents: Any) -> Callable[[], Iterable[str]] | None:
    client = _client()
    if client is None:
        return None
    s = C.get_settings()

    def produce() -> Iterable[str]:
        from google.genai import types

        stream = client.models.generate_content_stream(
            model=s.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system, temperature=s.ai_temperature, max_output_tokens=s.ai_max_output_tokens),
        )
        for chunk in stream:
            if chunk.text:
                yield chunk.text

    return produce


# ---------------------------------------------------------------------------
# Grounding data
# ---------------------------------------------------------------------------


def _r(x, n=1):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return round(float(x), n)


def markets_data() -> dict:
    from core.markets import macro, relative_value

    rv = relative_value()
    t = rv["table"]
    return {
        "backdrop": [{k: m[k] for k in ("label", "display", "change_text", "explanation", "as_of")} for m in macro()],
        "countries": [
            {"country": r["name"], "risk": _r(r["risk"], 0), "band": r["band"], "risk_1m_change_pts": _r(r["risk_1m"], 0),
             "spread_bp": _r(r["spread_bp"], 0), "spread_1m_change_bp": _r(r["spread_1m_bp"], 0),
             "usd_10y_pct": _r(r["usd_10y"], 2), "local_10y_pct": _r(r["local_10y"], 2),
             "inflation_pct": _r(r["inflation"], 1), "real_10y_pct": _r(r["real_10y"], 2),
             "fx_1m_return_pct_negative_means_weaker": _r(r["fx_1m"], 1),
             "relative_value_signal": r["signal"], "spread_vs_peers_pct": _r(r["residual"] * 100, 0),
             "whats_moving_it": r["driver"]}
            for _, r in t.iterrows()
        ],
        "top_opportunities": [t.set_index("iso2").loc[k, "name"] for k in rv["top_opportunities"]],
    }


def portfolio_data() -> dict:
    from core.portfolio import alerts_and_concentrations, holdings, lookthrough

    lt = lookthrough()
    h = holdings()
    return {
        "book_value_k_usd": _r(lt["total_k"], 0), "n_etfs": lt["n_etfs"], "n_bonds": lt["n_bonds"],
        "portfolio_yield_pct": _r(lt["portfolio_yield"], 2), "portfolio_risk": _r(lt["portfolio_risk"], 0),
        "portfolio_band": lt["portfolio_band"], "pct_on_alert": _r(lt["pct_on_alert"], 1),
        "oil_importer_share_pct": _r(lt["oil_importer_share"], 1),
        "estimated_1m_return_pct": _r(lt["est_1m_return"], 2),
        "region_weights_pct": {k: _r(v, 1) for k, v in lt["region_weights"].items()},
        "lookthrough_country_weights": [
            {"country": r["name"], "book_weight_pct": _r(r["weight"], 1), "risk": _r(r["risk"], 0),
             "band": r["band"], "held": r["via"]}
            for _, r in lt["table"].iterrows()
        ],
        "holdings": [
            {"name": r["name"], "type": r["type"], "currency": r["ccy"], "yield_pct": _r(r["yield_pct"], 2),
             "amount_k_usd": _r(r["amount_k"], 0), "weight_pct": _r(r["weight"], 1), "risk": _r(r["risk"], 0),
             "carry_1m_pct": _r(r["carry_1m"], 2), "fx_1m_pct": _r(r["fx_1m"], 2), "net_1m_pct": _r(r["net_1m"], 2),
             "paid_for_risk": r["paid"], "paid_for_risk_meaning": r["paid_explain"]}
            for _, r in h.iterrows()
        ],
        "alerts": [{"title": a["title"], "detail": a["text"]} for a in alerts_and_concentrations()],
    }


def home_data() -> dict:
    """Grounding for the Home brief. Includes each discussed country's Local/USD 10Y (from `markets_data()`)
    so this brief never has to say a yield "isn't in the data" when Markets/Portfolio show it right there —
    same field names as `markets_data()`/`portfolio_data()` so the three briefs describe the same numbers."""
    from core.home import home_view

    hv = home_view()
    k = hv["kpis"]
    by_country = {c["country"]: c for c in markets_data()["countries"]}

    def yields(name: str) -> dict:
        m = by_country.get(name, {})
        return {kk: m.get(kk) for kk in ("usd_10y_pct", "local_10y_pct", "real_10y_pct", "spread_bp")}

    return {
        "book_value_k_usd": _r(k["value_k"], 0), "n_etfs": k["n_etfs"], "n_bonds": k["n_bonds"],
        "portfolio_yield_pct": _r(k["yield"], 2), "us_10y_pct": _r(k["ust10"], 2),
        "portfolio_risk": _r(k["risk"], 0), "band": k["band"], "pct_on_alert": _r(k["pct_on_alert"], 1),
        "exposures_to_discuss": [
            {"country": a["name"], "tag": a["tag"], "book_weight_pct": _r(a["weight"], 1), "driver": a["driver"],
             "moves": a["moves"], **yields(a["name"])} for a in hv["alerts"]
        ],
        "strongest_positive_story": hv["strongest_positive"],
        "opportunities_not_in_book": k["opportunity_names"],
    }


def analyst_data() -> dict:
    from core.scoring import country_scores

    cs = country_scores()
    return {
        "risk_scores": [
            {"country": r["name"], "overall": _r(r["overall"], 0), "band": r["band"],
             "layers": {C.LAYER_NAMES[k]: _r(r[k], 0) for k in "LSFMT"},
             "fundamentals_minus_market_gap": _r(r["gap"], 0), "gap_meaning": r["gap_sentence"],
             "flags": r["flags"], "note": r["note"]}
            for _, r in cs.iterrows()
        ],
        "methodology": "Risk 0 = safe, 100 = distress. Overall = 0.30 Liquidity + 0.25 Solvency + 0.20 Currency + "
                       "0.25 Market. Bands: Low <35, Watch 35-54, Alert 55-69, Distress >=70. Text signal shown but not weighted.",
        "markets": markets_data(),
        "portfolio": portfolio_data(),
    }


DATA_BUILDERS = {"markets": markets_data, "portfolio": portfolio_data, "home": home_data}

BRIEF_TASKS = {
    "markets": "Write this week's markets brief in 3-4 plain-English sentences: the global backdrop, the biggest move, "
               "the strongest improving story, and where yields pay most versus peers.",
    "portfolio": "Write a portfolio brief in 3-4 plain-English sentences: size and yield, the real country exposure after "
                 "looking through the ETFs, the main risks, and one positive.",
    "home": "Write a short summary for the advisor's week in 3-4 plain-English sentences: book value, yield, risk profile, "
            "the exposures to discuss with clients this week, and the strongest positive story.",
}


# ---------------------------------------------------------------------------
# Deterministic fallbacks
# ---------------------------------------------------------------------------


def _fmt_signed(x: float, unit: str, digits: int = 0) -> str:
    return f"{x:+.{digits}f}{unit}".replace("-", "−")


def fallback_brief(scope: str, data: dict | None = None) -> str:
    data = data or DATA_BUILDERS[scope]()
    if scope == "markets":
        bd = {b["label"]: b for b in data["backdrop"]}
        ust = bd.get("US 10Y", {})
        brent = bd.get("Brent", {})
        rows = [c for c in data["countries"] if c["spread_1m_change_bp"] is not None]
        big = max(rows, key=lambda c: abs(c["spread_1m_change_bp"]), default=None)
        imp = [c for c in data["countries"] if (c["risk_1m_change_pts"] or 0) < 0]
        best = min(imp, key=lambda c: c["risk_1m_change_pts"], default=None)
        by_name = {c["country"]: c for c in data["countries"]}
        opp = [by_name[n] for n in data["top_opportunities"] if n in by_name]
        s = [f"US 10Y sits at {ust.get('display', 'n/a')} ({ust.get('change_text') or 'no change data'}) and Brent at "
             f"{brent.get('display', 'n/a')}, a backdrop that is tough for oil importers."]
        if big:
            s.append(f"The biggest move was {big['country']}, where the spread moved "
                     f"{_fmt_signed(big['spread_1m_change_bp'], ' bp')} over the month to {big['spread_bp']:.0f} bp.")
        if best:
            s.append(f"The strongest improving story is {best['country']} (risk {_fmt_signed(best['risk_1m_change_pts'], ' pts')}): "
                     f"{best['whats_moving_it']}")
        if opp and opp[0]["spread_vs_peers_pct"] is not None:
            o = opp[0]
            s.append(f"Relative to peers with similar risk, {o['country']} pays the most extra spread "
                     f"(about {o['spread_vs_peers_pct']:.0f}% more).")
        return " ".join(s)

    if scope == "portfolio":
        top = data["lookthrough_country_weights"][:3]
        africa = data["region_weights_pct"].get("Africa")
        no_pay = [h["name"] for h in data["holdings"] if h["paid_for_risk"] == "No"]
        pos = max(data["holdings"], key=lambda h: h["net_1m_pct"] or -99, default=None)
        s = [f"The book is ${data['book_value_k_usd']:,.0f}k across {data['n_etfs']} ETFs and {data['n_bonds']} bonds, "
             f"yielding {data['portfolio_yield_pct']:.2f}% on average."]
        if top:
            s.append("After looking through the ETFs, the largest country exposures are "
                     + ", ".join(f"{t['country']} ({t['book_weight_pct']:.1f}%)" for t in top)
                     + (f", and Africa is {africa:.0f}% of the book." if africa is not None else "."))
        risk_bits = [f"{data['pct_on_alert']:.0f}% of the book sits in countries on alert"]
        if no_pay:
            risk_bits.append(f"the currency is eating the yield on {', '.join(no_pay)}")
        s.append("Main risks: " + " and ".join(risk_bits) + ".")
        if pos:
            s.append(f"On the positive side, {pos['name']} has the best estimated 1-month net return "
                     f"({_fmt_signed(pos['net_1m_pct'], '%', 2)}).")
        return " ".join(s)

    # home
    ex = data["exposures_to_discuss"][:3]
    pos = data["strongest_positive_story"]
    s = [f"The book is worth ${data['book_value_k_usd']:,.0f}k and yields {data['portfolio_yield_pct']:.2f}%, "
         f"{(data['portfolio_yield_pct'] - data['us_10y_pct']) * 100:.0f} bp above the US 10Y.",
         f"Its risk profile is {data['band']} ({data['portfolio_risk']:.0f}/100), with {data['pct_on_alert']:.0f}% "
         f"of the book in countries on alert."]
    if ex:
        s.append("Exposures to discuss this week: " + "; ".join(
            f"{e['country']} ({e['book_weight_pct']:.1f}% of the book, {e['tag'].lower()})" for e in ex) + ".")
    if pos:
        s.append(f"The strongest positive story is {pos['name']}: {pos['driver']}")
    return " ".join(s)


def fallback_answer(question: str, data: dict | None = None) -> str:
    """Rule-based answer from dashboard data, used when AI is off."""
    data = data or analyst_data()
    q = question.lower()
    countries = {c["country"].lower(): c for c in data["risk_scores"]}
    market = {c["country"].lower(): c for c in data["markets"]["countries"]}
    mentioned = sorted((n for n in countries if n in q), key=q.index)
    pf = data["portfolio"]
    intro = ""

    if "paying" in q or "paid" in q or "not paying" in q:
        bad = [h for h in pf["holdings"] if h["paid_for_risk"] != "Yes"]
        if not bad:
            return intro + "Every position currently shows a positive net 1-month return with risk below alert."
        return intro + " ".join(
            f"{h['name']}: net 1-month {h['net_1m_pct']:+.2f}% (carry {h['carry_1m_pct']:+.2f}%, FX {h['fx_1m_pct']:+.2f}%), "
            f"risk {h['risk']:.0f} → '{h['paid_for_risk']}' ({h['paid_for_risk_meaning']})" for h in bad)
    for region in ("africa", "latam", "asia", "europe"):
        if region in q:
            key = {"africa": "Africa", "latam": "LatAm", "asia": "Asia", "europe": "Europe"}[region]
            w = pf["region_weights_pct"].get(key, 0.0)
            parts = [f"{c['country']} {c['book_weight_pct']:.1f}%" for c in pf["lookthrough_country_weights"]
                     if countries.get(c["country"].lower()) and _region(c["country"]) == key]
            return intro + (f"After looking through the ETFs, {w:.1f}% of the book is in {key}"
                            + (f": {', '.join(parts)}." if parts else "."))
    if mentioned:
        out = []
        for n in mentioned[:2]:
            c, m = countries[n], market.get(n, {})
            local = f"Local 10Y {m['local_10y_pct']:.2f}%" if m.get("local_10y_pct") is not None else "Local 10Y n/a"
            real = f"real 10Y {m['real_10y_pct']:.2f}%" if m.get("real_10y_pct") is not None else "real 10Y n/a"
            fx = f"currency {m['fx_1m_return_pct_negative_means_weaker']:+.1f}% over 1 month" \
                if m.get("fx_1m_return_pct_negative_means_weaker") is not None else "currency move n/a"
            out.append(f"{c['note']} {c['gap_meaning']} "
                       f"{local}, {real}, {fx}. What's moving it: {m.get('whats_moving_it') or 'n/a'}")
        return intro + " ".join(out)
    return intro + ("I can answer questions about country risk, market metrics, your holdings and the look-through weights. "
                    "Try naming a country or asking which positions are not paying for their risk.")


def _region(name: str) -> str | None:
    for c in C.COUNTRIES:
        if c["name"] == name:
            return c["region"]
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def stream_brief(scope: str) -> AIStream:
    """Markets / portfolio / home brief as a streaming AIStream."""
    if scope not in BRIEF_TASKS:
        raise ValueError(f"Unknown brief scope: {scope}")
    data = DATA_BUILDERS[scope]()
    producer = _stream_from_gemini(_system_instruction(BRIEF_TASKS[scope], data), BRIEF_TASKS[scope])
    return AIStream(producer, lambda: fallback_brief(scope, data))


def build_contents(history: list[dict], question: str) -> list:
    """Gemini `contents` with roles user/model; keeps the last 8 turns."""
    from google.genai import types

    contents = []
    for turn in history[-8:]:
        role = "model" if turn["role"] in ("model", "assistant") else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    contents.append(types.Content(role="user", parts=[types.Part(text=question)]))
    return contents


def stream_answer(history: list[dict], question: str) -> AIStream:
    """Ask the analyst: grounded, streaming answer."""
    data = analyst_data()
    system = _system_instruction(
        "Answer the advisor's question about their clients' EM exposure using the data. "
        "Compare options by the numbers (risk, spread, Local 10Y, real 10Y, currency move) without recommending.", data)
    producer = None
    if C.get_settings().ai_enabled:
        try:
            producer = _stream_from_gemini(system, build_contents(history, question))
        except Exception as e:
            log.warning("Could not build chat request: %s", type(e).__name__)
    return AIStream(producer, lambda: fallback_answer(question, data))


# --- Statement text signal --------------------------------------------------

TEXT_CUES = ["reprofil", "restructur", "liquidity pressure", "arrears", "financing gap",
             "financing conditions have tightened", "creditor", "unreported", "hidden", "suspend",
             "reserves declined", "depreciat", "debt treatment", "common framework", "emergency",
             "smooth the debt service", "dialogue with the imf"]


_NAMES = {c["iso2"]: c["name"] for c in C.COUNTRIES}


class TextSignal(BaseModel):
    score: int = Field(ge=0, le=100, description="0 = calm, 100 = acute distress language")
    phrases: list[str] = Field(default_factory=list, max_length=5, description="Up to 5 exact phrases from the text")
    summary: str = Field(description="One sentence")


def keyword_signal(text: str) -> TextSignal:
    """Fallback: score = min(100, 15 + 13 × hits) over the cue list."""
    low = text.lower()
    hits, phrases = 0, []
    for cue in TEXT_CUES:
        idx = low.find(cue)
        if idx >= 0:
            hits += 1
            # Extend to whole words for the phrase shown to the user.
            start = idx
            while start > 0 and low[start - 1].isalnum():
                start -= 1
            end = idx + len(cue)
            while end < len(text) and text[end].isalnum():
                end += 1
            phrase = text[start:end]
            if phrase not in phrases and len(phrases) < 5:
                phrases.append(phrase)
    score = min(100, 15 + 13 * hits)
    summary = (f"Keyword scan found {hits} warning cue{'s' if hits != 1 else ''}." if hits
               else "Keyword scan found no warning cues.")
    return TextSignal(score=score, phrases=phrases, summary=summary)


def _clean_signal(raw: Any, text: str) -> TextSignal:
    if isinstance(raw, TextSignal):
        d = raw.model_dump()
    elif isinstance(raw, dict):
        d = raw
    else:
        d = json.loads(str(raw))
    try:
        score = int(round(float(d.get("score", 0))))
    except (TypeError, ValueError):
        score = 0
    score = max(0, min(100, score))
    phrases = [p for p in (d.get("phrases") or []) if isinstance(p, str) and p and p in text][:5]
    summary = str(d.get("summary") or "").strip().split("\n")[0][:300]
    summary, _ = filter_recommendations(summary)
    return TextSignal(score=score, phrases=phrases, summary=summary or "No summary returned.")


def score_statement(country: str, text: str, persist: bool = True) -> tuple[TextSignal, bool]:
    """Score a central bank / finance ministry / IMF statement. Returns (signal, ai_used)."""
    iso2 = C.to_iso2(country)
    if iso2 is None:
        raise ValueError(f"Country not covered: {country}")
    result: TextSignal | None = None
    ai_used = False
    client = _client()
    if client is not None and text.strip():
        try:
            from google.genai import types

            s = C.get_settings()
            resp = client.models.generate_content(
                model=s.gemini_fast_model,
                contents=("Score how much this official statement signals sovereign debt stress for "
                          f"{_NAMES[iso2]}. 0 = calm, 100 = acute "
                          "distress language. Return up to 5 exact phrases copied from the text and a one-sentence "
                          "neutral summary with no recommendations.\n\nSTATEMENT:\n" + text[:20000]),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=TextSignal, temperature=0.0),
            )
            parsed = getattr(resp, "parsed", None)
            result = _clean_signal(parsed if parsed is not None else resp.text, text)
            ai_used = True
        except Exception as e:
            log.warning("Gemini text-signal call failed: %s", type(e).__name__)
    if result is None:
        result = keyword_signal(text)
    if persist:
        save_text_signal(iso2, text, result, ai_used)
    return result, ai_used


def save_text_signal(iso2: str, text: str, signal: TextSignal, ai_used: bool) -> None:
    """Store the signal; the latest score per country feeds layer T."""
    from core.db import session_scope
    from core.models import CountryScore, TextSignal as TextSignalRow

    with session_scope() as s:
        s.add(TextSignalRow(iso2=iso2, text_hash=hashlib.sha256(text.encode()).hexdigest(), score=signal.score,
                            phrases=signal.phrases, summary=signal.summary, ai_used=ai_used))
        row = s.get(CountryScore, iso2)
        if row is not None:
            row.T = float(signal.score)
            row.layer_sources = {**(row.layer_sources or {}), "T": "ai" if ai_used else "keywords"}
