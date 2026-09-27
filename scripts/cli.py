"""CLI: python -m scripts.cli [seed | fetch | score | import-etf | text-signal | models]."""
from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="EM Sovereign Early Warning — data jobs.")


@app.command()
def seed(reset: bool = typer.Option(False, "--reset", help="Wipe existing data and re-seed the demo data.")):
    """Create tables and seed the illustrative DEMO_MODE data."""
    from core.db import init_db
    from core.seed import seed_all

    if reset:
        init_db(seed_if_empty=False)
        seed_all(reset=True)
        typer.echo("Database reset and re-seeded.")
    else:
        typer.echo("Seeded." if init_db(seed_if_empty=True) else "Database already has data (use --reset to wipe).")


@app.command()
def fetch():
    """Run World Bank, IMF, Frankfurter, FRED fetchers + manual CSV, then recompute scores."""
    from core.fetchers import refresh_all

    report = refresh_all()
    for source, r in report.items():
        if source == "Scores":
            typer.echo(f"Scores: {r['status']}")
            continue
        typer.echo(f"{source}: {r.get('status')} · {r.get('detail') or str(r.get('rows', 0)) + ' values'}")
        for e in r.get("errors", []):
            typer.echo(f"  ! {e}")
    _print_fx_table()
    _print_fred_yields(report.get("FRED local 10Y", {}).get("table", []))
    _auto_sync()


def _print_fred_yields(table: list[dict]) -> None:
    if not table:
        return
    typer.echo("\nLocal 10Y from FRED (OECD monthly series). FRED replaces estimates only if consistent with the market")
    typer.echo(f"{'country':<8}{'local_10y':>10}  {'source (series)':<20}{'year-mo':>8}   {'in DB before':<18} action")
    f = lambda v: "n/a" if v is None else f"{v:.2f}"
    for r in table:
        before = "empty" if r["existing"] is None else f"{r['existing']:.2f} ({r['existing_source'] or '?'})"
        typer.echo(f"{r['iso2']:<8}{f(r['fred_value']):>10}  {(r['series'] or '-'):<20}{(r['period'] or '-'):>8}   {before:<18} {r['action']}")


def _print_fx_table() -> None:
    from core.fetchers import fx_report

    typer.echo("\nFX vs USD (+ = local currency weaker) — source: Frankfurter/ECB")
    typer.echo(f"{'currency':<9}{'country':<14}{'fx_1m_pct':>10}{'fx_12m_pct':>12}  status")
    for r in fx_report():
        f = lambda v: "n/a" if v is None else f"{v:+.2f}"
        note = "  (not published by Frankfurter)" if r["status"] == "failed" else (
            "  (from EUR via fixed 655.957 peg)" if r["status"] == "derived" else "")
        typer.echo(f"{r['currency']:<9}{r['country']:<14}{f(r['fx_1m_pct']):>10}{f(r['fx_12m_pct']):>12}  {r['status']}{note}")


@app.command()
def score():
    """Recompute country scores from the stored raw data."""
    from core.db import init_db
    from core.scoring import country_scores, recompute_scores

    init_db()
    _auto_sync("before scoring")   # so CSV edits count in this run
    changed = recompute_scores()
    typer.echo(f"Recomputed layers for {len(changed)} countries." if changed else "No raw data: stored scores kept.")
    df = country_scores().sort_values("overall", ascending=False)
    typer.echo(f"\n{'country':<8}{'L':>6}{'S':>6}{'F':>6}{'M':>6}{'T':>6}{'overall':>9}  band")
    for _, r in df.iterrows():
        fmt = lambda v: "n/a" if v is None or v != v else f"{v:.0f}"  # v != v -> NaN
        typer.echo(f"{r['iso2']:<8}{fmt(r['L']):>6}{fmt(r['S']):>6}{fmt(r['F']):>6}{fmt(r['M']):>6}{fmt(r['T']):>6}"
                   f"{fmt(r['overall']):>9}  {r['band']}")
    _auto_sync("after scoring")    # DB -> CSV


def _auto_sync(when: str = "end") -> None:
    """Keep data/manual_inputs.csv and the DB in sync (one summary line)."""
    from core.manual_inputs import sync_manual

    res = sync_manual()
    c = res["counts"]
    typer.echo(f"\nimport-manual ({when}): CSV<->DB in sync · {c['csv']} from csv, {c['db']} db-only, "
               f"{c['merged']} already merged" + (f" · {len(res['errors'])} CSV error(s)" if res["errors"] else ""))
    for e in res["errors"]:
        typer.echo(f"  ! {e}")


@app.command("import-manual")
def import_manual(quiet: bool = typer.Option(False, "--quiet", help="Print only the summary.")):
    """Sync data/manual_inputs.csv <-> ManualInput: upsert non-empty CSV cells, keep DB values that the CSV lacks,
    write the merged result back to the CSV, and print a confirmation table."""
    from core.db import init_db
    from core.manual_inputs import sync_manual

    init_db()
    res = sync_manual()
    if not quiet:
        typer.echo(f"{'country':<14}{'field':<13}{'value':<20}source")
        for r in res["rows"]:
            v = r["value"] if r["field"] != "note" else (r["value"][:17] + "..." if len(r["value"]) > 20 else r["value"])
            v = f"{v:g}" if isinstance(v, float) else v
            extra = f"  (CSV overrode DB value {r['prev']:g})" if r["prev"] is not None and r["field"] != "note" else (
                "  (CSV overrode DB note)" if r["prev"] is not None else "")
            typer.echo(f"{r['country']:<14}{r['field']:<13}{v:<20}{r['origin']}{extra}")
    c = res["counts"]
    typer.echo(f"\n{len(res['rows'])} values · {c['csv']} csv · {c['db']} db · {c['merged']} merged -> {res['path']}")
    for e in res["errors"]:
        typer.echo(f"  ! {e}")


@app.command("import-etf")
def import_etf(path: Path, name: str = typer.Option(..., "--name"), amount: float = typer.Option(100.0, "--amount"),
               ccy: str = typer.Option("USD", "--ccy")):
    """Import an ETF's country weights (CSV: country or ISO, weight) and add it to the book."""
    from core.db import init_db
    from core.portfolio import import_etf as do_import

    init_db()
    res = do_import(path.read_bytes(), name, amount_k=amount, ccy=ccy)
    typer.echo(f"Imported '{name}': {res['countries']} countries, yield {res['yield_pct']:.2f}%.")
    if res["skipped"]:
        typer.echo(f"Dropped (not covered): {', '.join(res['skipped'])}")


@app.command("text-signal")
def text_signal(country: str = typer.Option(..., "--country"), file: Path = typer.Option(..., "--file")):
    """Score an official statement (0 calm → 100 distress); the latest score feeds layer T."""
    from core.ai import score_statement
    from core.db import init_db

    init_db()
    sig, used = score_statement(country, file.read_text(encoding="utf-8"))
    typer.echo(f"{country}: {sig.score}/100 ({'Gemini' if used else 'keyword scan'})")
    typer.echo(sig.summary)
    for p in sig.phrases:
        typer.echo(f'  "{p}"')


@app.command()
def news(country: str = typer.Option(..., "--country")):
    """Fetch up to 5 recent GDELT news articles (last 72h) for a country and update its Text signal (T).

    No API key. GDELT enforces a shared rate limit (min. 5s between requests) and may 429 under contention;
    this then reports 0 articles rather than failing the command.
    """
    from core import config as C
    from core.db import init_db
    from core.fetchers import fetch_gdelt_news, save_gdelt_news

    init_db()
    iso2 = C.to_iso2(country)
    if iso2 is None:
        raise typer.Exit(f"Country not covered: {country}")
    articles = fetch_gdelt_news(iso2)
    res = save_gdelt_news(iso2, articles)
    typer.echo(f"{iso2}: {res['n_articles']} article(s) from GDELT (last 72h)")
    if res["T"] is not None:
        typer.echo(f"avg tone {res['avg_tone']:+.1f} -> T = {res['T']}/100 (written to CountryScore.T)")
    elif articles:
        typer.echo("GDELT returned articles with no tone value; T left unchanged.")
    else:
        typer.echo("No articles returned (nothing matched in 72h, or GDELT rate-limited this request); "
                   "T left unchanged.")
    for a in articles:
        tone_s = f"{a['tone']:+.1f}" if a["tone"] is not None else "n/a"
        typer.echo(f"  [{tone_s}] {a['title'][:88]}  ({a['source']})")
        if a["url"]:
            typer.echo(f"          {a['url']}")


@app.command()
def models():
    """List Gemini models available to your key (pin exact IDs in .env before the demo)."""
    from core.config import get_settings

    s = get_settings()
    if not s.ai_enabled:
        raise typer.Exit("GEMINI_API_KEY is empty.")
    from google import genai

    client = genai.Client(api_key=s.gemini_api_key)
    for m in client.models.list():
        actions = getattr(m, "supported_actions", None) or []
        if not actions or "generateContent" in actions:
            typer.echo(m.name)


if __name__ == "__main__":
    app()
