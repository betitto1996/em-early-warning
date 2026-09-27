import math

import numpy as np
import pandas as pd
import pytest

from core.markets import (
    benchmarks, fit_relative_value, pick_top_opportunities, real_10y, relative_value, residual, rv_signal, usd_10y,
)


def test_usd_10y_consistent_with_ust10():
    assert usd_10y(5.17, 450) == pytest.approx(9.67)
    assert usd_10y(5.17, None) is None


def test_real_10y():
    assert real_10y(16.85, 15.4) == pytest.approx(1.45)
    assert real_10y(None, 4.0) is None
    assert real_10y(8.0, None) is None


def test_fit_recovers_exact_curve_and_excludes_distressed():
    risk = pd.Series([10, 20, 30, 40, 50, 60, 80])
    a, b = 4.0, 0.03
    spread = pd.Series([math.exp(a + b * r) for r in risk])
    spread.iloc[-1] = 99999  # risk ≥ 70 must not affect the fit
    fa, fb = fit_relative_value(risk, spread)
    assert fa == pytest.approx(a) and fb == pytest.approx(b)


def test_fit_matches_numpy_ols():
    risk = pd.Series([26, 29, 35, 33, 28, 27, 46, 44, 55])
    spread = pd.Series([150, 110, 220, 150, 110, 160, 300, 280, 270])
    b, a = np.polyfit(risk, np.log(spread), 1)
    fa, fb = fit_relative_value(risk, spread)
    assert (fa, fb) == pytest.approx((a, b))


def test_residual_formula():
    a, b = 4.0, 0.03
    fair = math.exp(a + b * 50)
    assert residual(fair * 1.3, a, b, 50) == pytest.approx(0.30)


@pytest.mark.parametrize("risk,spread,res,signal", [
    (78, 1400, 0.9, "Distressed"), (70, 800, -0.5, "Distressed"),
    (46, 450, 0.26, "Cheap"), (46, 450, 0.25, "Fair"), (46, 450, -0.20, "Fair"), (46, 450, -0.21, "Rich"),
    (46, None, None, "n/a"),
])
def test_signal(risk, spread, res, signal):
    assert rv_signal(risk, spread, res) == signal


def test_top_opportunities_cheap_first_then_fallback():
    df = pd.DataFrame({"iso2": list("ABCDEFG"),
                       "signal": ["Cheap", "Cheap", "Fair", "Rich", "Cheap", "Distressed", "Fair"],
                       "residual": [0.3, 0.5, 0.1, -0.3, 0.26, np.nan, 0.2]})
    assert pick_top_opportunities(df) == ["B", "A", "E"]
    df["signal"] = ["Fair", "Fair", "Fair", "Rich", "Fair", "Distressed", "Fair"]
    assert pick_top_opportunities(df) == ["B", "A", "E"]  # top 3 residuals


def test_benchmarks_seeded(seeded_db):
    b = benchmarks().set_index("iso2")
    assert b.loc["NG", "usd_10y"] == pytest.approx(5.17 + 4.50)
    assert b.loc["NG", "real_10y"] == pytest.approx(16.85 - 15.4)
    assert pd.isna(b.loc["SN", "real_10y"])  # no liquid local 10Y
    assert b.loc["EG", "fx_1m"] == pytest.approx(-3.1)  # negative = weaker (UI convention)
    assert b.loc["HU", "sources"]["local_10y"] == "brief"


def test_relative_value_seeded(seeded_db):
    rv = relative_value()
    t = rv["table"].set_index("iso2")
    assert t.loc["SN", "signal"] == "Distressed" and pd.isna(t.loc["SN", "residual"])
    assert rv["b"] > 0
    assert set(rv["top_opportunities"]) <= set(t.index[t["signal"] == "Cheap"])
    assert rv["top_opportunities"][0] == "NG"
