import pytest

from core.home import alert_impact, alert_tag, rank_alerts
from core.portfolio import (
    add_bond, alerts_and_concentrations, benchmark_weights, book_weights, holding_risk, holdings, import_etf,
    lookthrough, normalize_weights, one_month_return, over_under, paid_for_risk, parse_weights_csv, pct_on_alert,
    portfolio_yield, remove_holding, vs_benchmark,
)
from core.seed import LOCAL_FUND


def test_etf_share_drops_uncovered_countries():
    shares = normalize_weights({"MX": 10, "XX": 5, "BR": 10})
    assert shares == pytest.approx({"MX": 0.5, "BR": 0.5})


def test_book_weights():
    hs = [{"amount_k": 100, "shares": {"MX": 0.5, "BR": 0.5}}, {"amount_k": 100, "shares": {"MX": 1.0}}]
    assert book_weights(hs) == pytest.approx({"MX": 75.0, "BR": 25.0})


def test_holding_and_portfolio_risk():
    risk = {"MX": 26, "BR": 35, "EG": 68}
    assert holding_risk({"MX": 0.5, "BR": 0.5}, risk) == pytest.approx(30.5)
    assert holding_risk({"EG": 1.0}, risk) == 68
    assert pct_on_alert({"MX": 60, "EG": 40}, risk) == 40


def test_portfolio_yield_amount_weighted():
    hs = [{"amount_k": 300, "yield_pct": 6.0}, {"amount_k": 100, "yield_pct": 10.0}]
    assert portfolio_yield(hs) == pytest.approx(7.0)


def test_one_month_return_usd_has_no_fx():
    r = one_month_return("USD", 7.4, {"EG": 1.0}, {"EG": -3.1})
    assert r["carry"] == pytest.approx(7.4 / 12) and r["fx"] == 0 and r["net"] == pytest.approx(7.4 / 12)


def test_one_month_return_local_bond():
    r = one_month_return("Local", 24.0, {"EG": 1.0}, {"EG": -3.1})
    assert r["carry"] == pytest.approx(2.0)
    assert r["fx"] == pytest.approx(-3.1)
    assert r["net"] == pytest.approx(-1.1)


def test_one_month_return_local_etf_lookthrough():
    shares = normalize_weights(LOCAL_FUND)
    r = one_month_return("Local", 8.9, shares, {"EG": -3.1, "NG": 1.4, "ZA": -1.0, "TR": -1.7})
    assert r["fx"] == pytest.approx(0.08 * -3.1 + 0.08 * 1.4 + 0.13 * -1.0 + 0.09 * -1.7)


@pytest.mark.parametrize("net,risk,expected", [(-0.1, 20, "No"), (0.0, 20, "No"), (0.5, 55, "Check"),
                                               (0.5, 54.9, "Yes"), (0.5, None, "Yes")])
def test_paid_for_risk(net, risk, expected):
    assert paid_for_risk(net, risk) == expected


def test_benchmark_is_average_of_sample_funds():
    bench = benchmark_weights([{"MX": 10, "BR": 90}, {"MX": 30, "BR": 70}])
    assert bench == pytest.approx({"MX": 20.0, "BR": 80.0})
    assert over_under({"MX": 20.3, "BR": 79.7}, bench) == {}
    assert over_under({"MX": 25, "BR": 75}, bench) == pytest.approx({"MX": 5.0, "BR": -5.0})


def test_parse_weights_csv_names_iso_and_header():
    data = b"Country,Weight\nMexico,10\nBRA,5.5%\nTR,4\nNorway,3\n"
    w, skipped = parse_weights_csv(data)
    assert w == {"MX": 10.0, "BR": 5.5, "TR": 4.0}
    assert skipped == ["Norway"]


def test_seeded_book(seeded_db):
    lt = lookthrough()
    assert lt["total_k"] == 500
    assert lt["n_etfs"] == 2 and lt["n_bonds"] == 7
    assert sum(lt["weights"].values()) == pytest.approx(100.0)
    assert lt["weights"]["MX"] == pytest.approx((250 * 0.10 + 150 * 0.14 + 10) / 500 * 100)
    assert lt["weights"]["SN"] == pytest.approx((250 * 0.04 + 10) / 500 * 100)
    expected_yield = (250 * 7.4 + 150 * 8.9 + 15 * 24 + 20 * 16.85 + 20 * 8.9 + 10 * 32.6 + 15 * 5.64
                      + 10 * 18 + 10 * 6.4) / 500
    assert lt["portfolio_yield"] == pytest.approx(expected_yield)
    t = lt["table"].set_index("iso2")
    assert t.loc["EG", "via"] == "held via ETF and bond"
    assert t.loc["ZM", "via"] == "held via ETF"


def test_seeded_holdings_paid_for_risk(seeded_db):
    h = holdings().set_index("name")
    eg = h.loc["EG 364-day T-bill"]
    assert eg["net_1m"] == pytest.approx(2.0 - 3.1) and eg["paid"] == "No"
    assert h.loc["SN 2033 Eurobond", "paid"] == "Check"   # USD, positive carry, risk 78
    assert h.loc["MX 2034 bond", "paid"] == "Yes"
    assert h.loc["EM hard-currency bond ETF (sample)", "subtitle"] == "15 countries inside"


def test_alerts_and_concentrations(seeded_db):
    kinds = {(a["kind"], a["iso2"]) for a in alerts_and_concentrations()}
    assert ("carry", "EG") in kinds
    assert ("distress", "SN") in kinds


def test_vs_benchmark_hides_small_diffs(seeded_db):
    df = vs_benchmark()
    assert (df["diff"].abs() >= 0.5).all()


def test_add_remove_and_import(seeded_db):
    hid = add_bond("Peru", "PE 2034", "USD", 6.0, "2034", 25)
    assert hid in holdings()["id"].tolist()
    assert remove_holding(hid)
    assert hid not in holdings()["id"].tolist()
    with pytest.raises(ValueError):
        add_bond("Norway", "x", "USD", 5, None, 10)
    res = import_etf(b"country,weight\nMexico,50\nBrazil,50\n", "My ETF", amount_k=100)
    assert res["countries"] == 2
    # Imported ETF yield = weighted USD 10Y of its countries: 0.5·(5.17+1.50) + 0.5·(5.17+2.20)
    assert res["yield_pct"] == pytest.approx(0.5 * 6.67 + 0.5 * 7.37, abs=0.01)


# --- Home alerts (spec §6.5) ---------------------------------------------------

def test_alert_impact_formula():
    assert alert_impact(5, 68, 4, -3.1, 45) == pytest.approx(5 * (4 + 3.1 + 2 + 45 / 25 + 0.5))
    assert alert_impact(5, 40, None, None, None) == pytest.approx(5 * 0.5)


@pytest.mark.parametrize("risk_1m,fx,spread,tag", [
    (4, -3.1, 45, "Risk"), (0, 0, 20, "Risk"), (0, -0.1, 0, "Risk"),
    (-4, 1.4, -40, "Positive"), (-1, 0, 19, "Positive"), (0, 0, 5, "Watch"), (-1, -0.5, 0, "Risk"),
])
def test_alert_tag(risk_1m, fx, spread, tag):
    assert alert_tag(risk_1m, fx, spread) == tag


def test_home_alert_ranking(seeded_db):
    from core.home import home_view

    hv = home_view()
    alerts = hv["alerts"]
    assert len(alerts) == 5
    assert [a["impact"] for a in alerts] == sorted([a["impact"] for a in alerts], reverse=True)
    assert all(a["weight"] >= 1 for a in alerts)
    eg = next(a for a in alerts if a["iso2"] == "EG")
    assert eg["tag"] == "Risk"
    assert eg["moves"] == "risk +4 pts · currency −3.1% · spread +45 bp · held via ETF and bond"
    # AI opportunities: top opportunities with book weight < 2%
    assert all(hv["lookthrough"]["weights"].get(k, 0) < 2 for k in hv["kpis"]["opportunities"])
