# EM Sovereign Early Warning

ShellHacks 2026 · Blackstone challenge *"Reimagining the Investor Experience."*

Shows advisors what their clients are really exposed to in EM bonds and ETFs, country by country; flags rising
risk early; surfaces where yields pay for the risk; packages it for a client meeting.
Information and comparison only, not investment advice.

## Run

```bash
python3.13 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional: add GEMINI_API_KEY / FRED_API_KEY
.venv/bin/streamlit run app.py
```

The app runs fully offline in `DEMO_MODE=true` with no keys: the database is seeded on first run with the
illustrative data from the spec (§11), and every AI feature falls back to deterministic text.

Tests: `.venv/bin/python -m pytest -q`

### CLI

```bash
python -m scripts.cli seed [--reset]        # create + seed (reset restores the demo data)
python -m scripts.cli fetch                 # World Bank, IMF, Frankfurter, FRED + manual CSV → rescore
python -m scripts.cli score                 # rescore from stored raw data
python -m scripts.cli import-etf path.csv --name "My ETF" [--amount 100 --ccy USD]
python -m scripts.cli text-signal --country EG --file statement.txt
python -m scripts.cli models                # list Gemini models → pin exact IDs in .env before the demo
```

The in-app **Refresh data** button and the **statement text signal** admin tool live in the sidebar (›› top-left).

## Structure

```
app.py              entry: page config, CSS/fonts, st.navigation (top), sidebar (data/admin), footer
pages/              home · markets · portfolio · risk_map · ask
ui/                 components (pills, alert rows, pager, AI box), charts (Plotly), data (st.cache_data wrappers)
core/               config (settings + ALL methodology constants), models, db, seed,
                    scoring, markets, portfolio, home, ai, fetchers, manual_inputs  — no Streamlit code
scripts/cli.py      Typer CLI
tests/              pytest: formulas (§6), look-through, alerts, fetch parsers, AI fallbacks + post-filter
```

## Data

| Source | Used for | Notes |
|---|---|---|
| World Bank API | reserves, months of imports, ST debt, debt service, interest/revenue, inflation | latest non-null value per country; year stored |
| IMF DataMapper (WEO) | gov. debt/GDP, inflation, current account | latest year ≤ `IMF_YEAR` |
| Frankfurter (ECB) | FX 1M / 12M for BRL HUF IDR MXN TRY ZAR + XOF (EUR × 655.957) | + = local currency weaker |
| FRED DGS10 | US 10Y | optional key; else stored brief value / `UST10_FALLBACK` |
| `data/manual_inputs.csv` | spreads, 1M change, CDS, local 10Y, FX, text signal | Bloomberg exports — analysis/demo only, git-ignored |

Raw responses are cached in `data/cache/`, so a refresh without network reuses the last good response.
See `data/manual_inputs.example.csv` for the CSV format.

## Implementation decisions to review (team)

These are choices the spec left open. Weights, thresholds and formulas from §6 are unchanged and live in
`core/config.py`.

1. **Seeded vs computed layers.** In demo mode the layer scores (L, S, F, M, T) are the seeded ones. A refresh
   recomputes a layer from raw data only when enough of its indicators come from non-illustrative sources
   (`MIN_INDICATORS_PER_LAYER`: L 1, S 1, F 2, M 1); otherwise the stored value is kept. The Risk map
   profile shows each layer's source. A live refresh (tested) replaces L/S/F with World Bank/IMF data and
   changes the ranking noticeably — `python -m scripts.cli seed --reset` restores the demo data.
2. **Rounding.** "Rounded" is implemented as round-half-up (Sri Lanka 54.5 → 55, Alert), not Python's
   banker's rounding.
3. **Source precedence** for the same indicator: manual CSV > team brief > API > illustrative.
4. **Risk 1M** is kept as stored; recomputing it needs a month-old score history (not stored yet).
5. **Country flags** (IMF program, hidden debt, post-restructuring) are illustrative seeds — verify before
   the demo (`core/seed.py → FLAGS`).
6. **Imported ETF yield** = weighted USD 10Y of its countries, as the spec says, also for local-currency ETFs.
7. **"On alert" filter** in Portfolio = holding risk ≥ 55.
8. **Streamlit ≥ 1.50** (spec said 1.40): top navigation and horizontal containers need it;
   `width="stretch"` replaces the deprecated `use_container_width`.
9. **Gemini output budget.** Newer Flash models "think" before answering and that counts against
   `AI_MAX_OUTPUT_TOKENS`; if a reply comes back empty, the app shows the built-in text. Raise the budget
   if that happens with your pinned model.

### Data caveats seen in a live refresh

- World Bank `interest_pct_revenue` is stale for some countries (Egypt's latest value is 2015). The year is
  stored and shown under **Risk map → Indicators and sources**.
- Senegal has no World Bank reserves series (pooled BCEAO reserves), so its Liquidity layer stays seeded.

## Guardrails

- Unknown values stay null and display "n/a"; nothing is invented.
- No buy/sell/hold/consider/reduce language: the AI system instruction forbids it, a sentence-level post-filter
  removes it from streamed output, and a test scans all UI copy.
- The Gemini key is read server-side only, never rendered, logged or cached to disk. `.env` and
  `.streamlit/secrets.toml` are git-ignored.
- Pin Gemini model IDs in `.env` before going on stage.

## License

MIT — see [LICENSE](LICENSE). This does not cover the Bloomberg-sourced data referenced in `data/manual_inputs.csv`
(git-ignored, never redistributed) — that data stays subject to Bloomberg's own license terms.
