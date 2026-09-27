import math

import pytest

from core import config as C
from core.scoring import (
    compute_derived, country_scores, indicator_score, layer_scores, liquidity_ratio, overall_score,
    recompute_scores, round_half_up,
)


@pytest.mark.parametrize("key,value,expected", [
    ("liquidity_ratio", 2.0, 0), ("liquidity_ratio", 0.5, 100), ("liquidity_ratio", 1.25, 50),
    ("liquidity_ratio", 3.0, 0), ("liquidity_ratio", 0.1, 100),
    ("reserves_months_imports", 4.0, 50),
    ("gov_debt_pct_gdp", 70, 50), ("gov_debt_pct_gdp", 30, 0), ("gov_debt_pct_gdp", 130, 100),
    ("interest_pct_revenue", 21.5, 50),
    ("fx_12m_pct", 15, 50), ("fx_12m_pct", -5, 0),
    ("inflation", 17, 50),
    ("current_account_pct_gdp", -3, 50), ("current_account_pct_gdp", 2, 0), ("current_account_pct_gdp", -9, 100),
    ("spread_bp", 600, 50), ("cds_5y_bp", 575, 50),
])
def test_indicator_mapping(key, value, expected):
    assert indicator_score(key, value) == pytest.approx(expected)


def test_indicator_missing_is_none():
    assert indicator_score("spread_bp", None) is None
    assert indicator_score("spread_bp", math.nan) is None


def test_liquidity_ratio():
    assert liquidity_ratio(30e9, 10e9, 5e9) == pytest.approx(2.0)
    assert liquidity_ratio(30e9, None, 5e9) is None
    assert liquidity_ratio(30e9, 0, 0) is None


def test_layers_average_available_indicators():
    layers = layer_scores({"spread_bp": 600, "cds_5y_bp": None, "gov_debt_pct_gdp": 70, "interest_pct_revenue": 35})
    assert layers["M"] == pytest.approx(50)
    assert layers["S"] == pytest.approx(75)
    assert layers["L"] is None and layers["F"] is None


def test_overall_weights_and_reweighting():
    # 0.30·L + 0.25·S + 0.20·F + 0.25·M
    assert overall_score({"L": 78, "S": 92, "F": 40, "M": 95}) == 78  # 78.15
    # Re-weighted over available layers: (0.30·60 + 0.25·20) / 0.55 = 41.8
    assert overall_score({"L": 60, "S": 20, "F": None, "M": None}) == 42
    assert overall_score({"L": None, "S": None, "F": None, "M": None}) is None


def test_text_layer_not_in_overall():
    a = overall_score({"L": 50, "S": 50, "F": 50, "M": 50})
    b = overall_score({"L": 50, "S": 50, "F": 50, "M": 50, "T": 100})
    assert a == b == 50


def test_round_half_up():
    assert round_half_up(54.5) == 55  # Sri Lanka seed: 16.5 + 17.5 + 8 + 12.5
    assert round_half_up(54.49) == 54
    assert overall_score({"L": 55, "S": 70, "F": 40, "M": 50}) == 55


def test_fundamentals_and_gap():
    d = compute_derived({"L": 62, "S": 80, "F": 74, "M": 58})
    # fundamentals = (0.30·62 + 0.25·80 + 0.20·74) / 0.75 = 71.07
    assert d["fundamentals"] == 71
    assert d["gap"] == 13
    assert d["overall"] == 68


@pytest.mark.parametrize("score,band", [(0, "Low"), (34.9, "Low"), (35, "Watch"), (54, "Watch"),
                                        (55, "Alert"), (69, "Alert"), (70, "Distress"), (100, "Distress")])
def test_bands(score, band):
    assert C.band_for(score) == band


def test_seeded_scores(seeded_db):
    df = country_scores().set_index("iso2")
    assert df.loc["SN", "overall"] == 78 and df.loc["SN", "band"] == "Distress"
    assert df.loc["EG", "overall"] == 68 and df.loc["EG", "band"] == "Alert"
    assert df.loc["LK", "overall"] == 55
    assert df.loc["MX", "overall"] == 26 and df.loc["MX", "band"] == "Low"
    assert df.loc["SN", "T"] == 88


def test_recompute_keeps_seed_without_real_data(seeded_db):
    before = country_scores().set_index("iso2")["overall"].to_dict()
    recompute_scores()
    after = country_scores().set_index("iso2")["overall"].to_dict()
    assert before == after


def test_recompute_uses_manual_market_data(seeded_db):
    from core.db import session_scope
    from core.models import ManualInput

    with session_scope() as s:
        m = s.get(ManualInput, "MX")
        m.cds_5y_bp = 575  # → 50
        m.spread_bp = 600  # → 50
        m.sources = {**m.sources, "cds_5y_bp": "manual", "spread_bp": "manual"}
    recompute_scores()
    row = country_scores().set_index("iso2").loc["MX"]
    assert row["M"] == 50
    assert row["layer_sources"]["M"] == "computed"
    assert row["L"] == 18  # untouched seed layer
