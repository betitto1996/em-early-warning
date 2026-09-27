from datetime import date

import pytest

from core import config as C
from core.fetchers import parse_fred, parse_frankfurter, parse_imf, parse_world_bank, refresh_all
from core.manual_inputs import parse_manual_csv


def test_world_bank_latest_non_null():
    payload = [{"page": 1}, [
        {"countryiso3code": "EGY", "date": "2024", "value": None},
        {"countryiso3code": "EGY", "date": "2023", "value": 35.2},
        {"countryiso3code": "EGY", "date": "2022", "value": 33.0},
        {"countryiso3code": "NOR", "date": "2024", "value": 1.0},
    ]]
    rows = parse_world_bank(payload, "reserves_usd")
    assert rows == [{"iso2": "EG", "key": "reserves_usd", "value": 35.2, "year": 2023, "source": "worldbank"}]
    assert parse_world_bank([{"message": "error"}], "x") == []


def test_imf_latest_year_capped():
    payload = {"values": {"GGXWDG_NGDP": {"EGY": {"2023": 95.0, "2025": 90.0, "2026": 85.0}, "NOR": {"2025": 40}}}}
    rows = parse_imf(payload, "GGXWDG_NGDP", "gov_debt_pct_gdp", 2025)
    assert rows == [{"iso2": "EG", "key": "gov_debt_pct_gdp", "value": 90.0, "year": 2025, "source": "imf"}]


def test_frankfurter_sign_and_xof_peg():
    payload = {"rates": {
        "2025-09-20": {"TRY": 30.0, "EUR": 0.90},
        "2026-08-20": {"TRY": 40.0, "EUR": 0.90},
        "2026-09-25": {"TRY": 42.0, "EUR": 0.85},
    }}
    rows = {(r["iso2"], r["key"]): r["value"] for r in parse_frankfurter(payload)}
    assert rows[("TR", "fx_1m_pct")] == pytest.approx((42 / 40 - 1) * 100)   # + = lira weakened
    assert rows[("TR", "fx_12m_pct")] == pytest.approx((42 / 30 - 1) * 100)
    xof_now, xof_1m = 0.85 * C.XOF_PER_EUR, 0.90 * C.XOF_PER_EUR
    assert rows[("SN", "fx_1m_pct")] == pytest.approx((xof_now / xof_1m - 1) * 100)  # negative: CFA strengthened


def test_fred_skips_missing_values():
    obs = parse_fred({"observations": [{"date": "2026-09-24", "value": "5.18"}, {"date": "2026-09-25", "value": "5.17"},
                                       {"date": "2026-09-28", "value": "."}]})
    assert obs["value"] == 5.17 and obs["date"] == "2026-09-25" and obs["change_bp"] == pytest.approx(-1)


def test_manual_csv_validation():
    rows, errors = parse_manual_csv(
        "country,spread_bp,spread_1m_bp,cds_5y_bp,local_10y,fx_1m_pct,fx_12m_pct,text_signal,note\n"
        "Egypt,620,45,,24.0,3.1,,,\n"
        "Norway,100,,,,,,,\n"
        "TR,abc,,,,,,,\n"
        "ZA,99999,,,,,,,\n"
    )
    assert rows[0] == {"iso2": "EG", "spread_bp": 620, "spread_1m_bp": 45, "local_10y": 24.0, "fx_1m_pct": 3.1}
    assert len(errors) == 3


def test_refresh_offline_keeps_app_working(seeded_db, monkeypatch):
    import core.fetchers as f

    def offline(*a, **k):
        raise RuntimeError("offline")

    monkeypatch.setattr(f, "get_json", offline)
    report = refresh_all()
    assert report["World Bank"]["status"] == "failed"
    assert report["Scores"]["status"] == "unchanged"
    from core.scoring import country_scores

    assert country_scores().set_index("iso2").loc["SN", "overall"] == 78


# --- FRED local 10Y (Task 3) -----------------------------------------------------

def test_parse_fred_latest_skips_missing_and_ignores_order():
    from core.fetchers import parse_fred_latest

    payload = {"observations": [{"date": "2026-08-01", "value": "9.16"}, {"date": "2026-09-01", "value": "."},
                                {"date": "2026-07-01", "value": "9.02"}]}
    assert parse_fred_latest(payload) == {"value": 9.16, "date": "2026-08-01"}
    assert parse_fred_latest({"observations": [{"date": "2026-08-01", "value": "."}]}) is None
    assert parse_fred_latest(None) is None


def test_fred_series_map_has_no_invented_series():
    from core.fetchers import FRED_LOCAL_10Y_SERIES

    assert set(FRED_LOCAL_10Y_SERIES) == {"BR", "MX", "CO", "CL", "PE", "HU", "ID"}
    assert FRED_LOCAL_10Y_SERIES["BR"] is None and FRED_LOCAL_10Y_SERIES["PE"] is None
    assert FRED_LOCAL_10Y_SERIES["ID"] is None


def test_fetch_fred_local_yields_without_key_returns_empty(seeded_db, monkeypatch):
    from core import config
    from core.fetchers import fetch_fred_local_yields

    monkeypatch.setenv("FRED_API_KEY", "")
    config.reset_settings()
    assert fetch_fred_local_yields() == ({}, "skipped")


def _recent(days_ago: int = 30) -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=days_ago)).replace(day=1).isoformat()


def test_fred_replaces_estimates_only_when_consistent(seeded_db, tmp_path):
    from core.db import session_scope
    from core.fetchers import apply_fred_local_yields
    from core.models import ManualInput

    csv_path = tmp_path / "manual_inputs.csv"
    csv_path.write_text('country,spread_bp,local_10y,note\nMexico,150,9.1,"est, with comma"\nChile,110,5.9,x\n'
                        "Hungary,150,5.64,confirmed\nColombia,300,11.5,y\nKenya,600,,z\n")
    with session_scope() as s:
        s.get(ManualInput, "CL").sources = {"local_10y": "manual"}           # an estimate from the CSV
        s.get(ManualInput, "KE").local_10y = None
    d = _recent()
    res = {"MX": {"value": 9.16, "date": d, "series": "a"},      # +6bp  -> replaces the 9.10 estimate
           "CL": {"value": 5.55, "date": d, "series": "b"},      # 35bp  -> replaces
           "HU": {"value": 5.46, "date": d, "series": "c"},      # source 'brief' -> never replaced
           "CO": {"value": 14.50, "date": d, "series": "d"},     # 300bp -> too far from the estimate, kept
           "BR": {"value": 13.0, "date": "2020-01-01", "series": "e"}}
    rows = {r["iso2"]: r for r in apply_fred_local_yields(res)}
    assert rows["MX"]["action"].startswith("replaced") and rows["CL"]["action"].startswith("replaced")
    assert rows["HU"]["action"].startswith("kept (confirmed")
    assert rows["CO"]["action"].startswith("kept estimate")
    assert rows["ID"]["action"] == "no FRED series"
    with session_scope() as s:
        assert s.get(ManualInput, "MX").local_10y == 9.16 and s.get(ManualInput, "MX").sources["local_10y"] == "fred"
        assert s.get(ManualInput, "HU").local_10y == 5.64       # confirmed brief value untouched
        assert s.get(ManualInput, "CO").local_10y == 11.5       # estimate kept
    text = csv_path.read_text()
    assert 'Mexico,150,9.16,"est, with comma"' in text and "Chile,110,5.55,x" in text   # CSV overwritten too
    assert "Hungary,150,5.64,confirmed" in text and "Colombia,300,11.5,y" in text


def test_fred_rejects_stale_observation(seeded_db):
    from core.fetchers import _fred_decision

    ok, action, _ = _fred_decision(9.1, "illustrative", None, 9.2, _recent(400))
    assert not ok and "old" in action
    assert _fred_decision(None, None, None, 9.2, _recent(400))[0]            # empty cell: always fill
    assert _fred_decision(9.0, "fred", None, 9.3, _recent(30))[0]            # FRED refreshes its own value


def test_fill_csv_empty_cells_keeps_existing_values(tmp_path):
    from core.manual_inputs import fill_csv_empty_cells

    p = tmp_path / "m.csv"
    p.write_text("country,local_10y,note\nHungary,5.64,a\nColombia,,b\n")
    assert fill_csv_empty_cells("local_10y", {"HU": 5.46, "CO": 12.15}, path=p) == ["CO"]
    assert p.read_text() == "country,local_10y,note\nHungary,5.64,a\nColombia,12.15,b\n"


# --- GDELT news (Task 5) ---------------------------------------------------------

def test_gdelt_fips_codes_differ_from_iso_where_expected():
    from core.fetchers import GDELT_FIPS

    assert set(GDELT_FIPS) == {c["iso2"] for c in __import__("core.config", fromlist=["COUNTRIES"]).COUNTRIES}
    # These are the countries where FIPS 10-4 differs from ISO 3166 — a common source of silent bugs.
    assert GDELT_FIPS["TR"] == "TU" and GDELT_FIPS["ZA"] == "SF" and GDELT_FIPS["CL"] == "CI"
    assert GDELT_FIPS["SN"] == "SG" and GDELT_FIPS["LK"] == "CE"


def test_parse_gdelt_empty_response_is_not_an_error():
    from core.fetchers import parse_gdelt_articles

    assert parse_gdelt_articles({}) == []
    assert parse_gdelt_articles(None) == []


def test_parse_gdelt_tone_missing_is_none_not_invented():
    from core.fetchers import parse_gdelt_articles

    payload = {"articles": [
        {"title": "Egypt seeks IMF deal", "url": "https://x/1", "seendate": "20260925T120000Z",
         "domain": "reuters.com", "tone": -6.2},
        {"title": "No tone field here", "url": "https://x/2", "seendate": "20260925T110000Z", "domain": "bbc.com"},
    ]}
    out = parse_gdelt_articles(payload)
    assert out[0]["tone"] == -6.2 and out[0]["source"] == "reuters.com"
    assert out[1]["tone"] is None


def test_gdelt_tone_to_score_formula():
    from core.fetchers import gdelt_tone_to_score

    assert gdelt_tone_to_score(0) == 40           # (-0 + 10) * 4
    assert gdelt_tone_to_score(-10) == 80          # sharply negative coverage -> high distress
    assert gdelt_tone_to_score(10) == 0            # clamped at 0
    assert gdelt_tone_to_score(-30) == 100         # clamped at 100


def test_gdelt_risk_signal_averages_then_converts():
    from core.fetchers import gdelt_risk_signal

    t, avg = gdelt_risk_signal([{"tone": -10}, {"tone": -6}, {"tone": None}])
    assert avg == -8 and t == 72                    # (8+10)*4
    assert gdelt_risk_signal([{"tone": None}]) == (None, None)
    assert gdelt_risk_signal([]) == (None, None)


def test_fetch_gdelt_news_unknown_country_returns_empty(seeded_db):
    from core.fetchers import fetch_gdelt_news

    assert fetch_gdelt_news("ZZ") == []


def test_fetch_gdelt_news_falls_back_to_empty_on_any_error(seeded_db, monkeypatch):
    import core.fetchers as f

    monkeypatch.setattr(f, "get_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    assert f.fetch_gdelt_news("NG") == []


def test_save_gdelt_news_writes_articles_and_country_score_t(seeded_db):
    from core.db import session_scope
    from core.fetchers import save_gdelt_news
    from core.models import CountryScore, TextSignal

    articles = [{"title": "Nigeria debt concerns rise", "url": "https://x/a", "date": "20260925T000000Z",
                "tone": -8.0, "source": "reuters.com"},
               {"title": "Naira strengthens", "url": "https://x/b", "date": "20260925T010000Z",
                "tone": 2.0, "source": "bloomberg.com"}]
    res = save_gdelt_news("NG", articles)
    assert res["n_articles"] == 2 and res["avg_tone"] == -3.0 and res["T"] == gdelt_score_for(-3.0)
    with session_scope() as s:
        rows = s.query(TextSignal).filter(TextSignal.iso2 == "NG", TextSignal.source == "gdelt").all()
        assert len(rows) == 2
        assert {r.title for r in rows} == {"Nigeria debt concerns rise", "Naira strengthens"}
        row = s.get(CountryScore, "NG")
        assert row.T == res["T"] and row.layer_sources["T"] == "gdelt"


def test_save_gdelt_news_no_tone_leaves_t_unchanged(seeded_db):
    from core.db import session_scope
    from core.fetchers import save_gdelt_news
    from core.models import CountryScore

    with session_scope() as s:
        before = s.get(CountryScore, "NG").T
    res = save_gdelt_news("NG", [{"title": "x", "url": "https://x/c", "date": "", "tone": None, "source": "y"}])
    assert res["T"] is None
    with session_scope() as s:
        assert s.get(CountryScore, "NG").T == before


def gdelt_score_for(avg_tone: float) -> int:
    return min(100, max(0, int((-avg_tone + 10) * 4)))
