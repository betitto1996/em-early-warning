"""Seed data for DEMO_MODE (spec §11).

Everything here is illustrative unless its source is "brief" (team briefs,
Friday 25 Sep 2026 close). Unknown values are left as None → shown as "n/a".
"""
from __future__ import annotations

from sqlalchemy import delete

from core.config import COUNTRIES
from core.db import session_scope
from core.models import (
    Country, CountryNote, CountryScore, Fund, FundWeight, Holding, IndicatorValue,
    Macro, ManualInput, TextSignal,
)
from core.scoring import compute_derived

ILL, BRIEF = "illustrative", "brief"

# L, S, F, M, T
LAYER_SCORES: dict[str, tuple[float, float, float, float, float]] = {
    "SN": (78, 92, 40, 95, 88), "AR": (72, 58, 70, 66, 60), "PK": (70, 75, 60, 64, 62),
    "EG": (62, 80, 74, 58, 60), "KE": (60, 72, 48, 62, 58), "LK": (55, 70, 40, 50, 42),
    "GH": (50, 65, 45, 55, 40), "TR": (58, 35, 82, 50, 55), "ZM": (48, 60, 50, 52, 38),
    "NG": (45, 55, 35, 48, 30), "ZA": (30, 60, 52, 40, 35), "BR": (20, 55, 40, 30, 25),
    "HU": (25, 45, 35, 28, 22), "ID": (22, 30, 35, 30, 20), "MX": (18, 35, 30, 25, 18),
    "CO": (35, 55, 50, 45, 35), "CL": (25, 30, 35, 22, 15), "PE": (20, 28, 30, 30, 30),
}

# spread bp, spread 1M bp, local 10Y %, inflation %, risk 1M pts;
# plus the set of fields sourced from team briefs.
MARKET: dict[str, tuple] = {
    "MX": (150, 5, 9.1, 3.9, 0, set()),
    "ID": (110, 8, 6.9, 2.5, 0, set()),
    "BR": (220, -10, 13.6, 5.0, -1, set()),
    "HU": (150, -15, 5.64, 4.0, -3, {"local_10y"}),
    "CL": (110, 3, 5.9, 4.0, 0, set()),
    "PE": (160, 2, 6.6, 2.5, 1, set()),
    "CO": (300, -15, 11.5, 5.0, -1, set()),
    "ZA": (280, 20, 8.93, 4.4, 2, {"local_10y", "inflation"}),
    "TR": (270, 35, 32.6, 31.5, 3, {"local_10y", "inflation"}),
    "NG": (450, -40, 16.85, 15.4, -4, {"local_10y", "inflation"}),  # local 10Y: FGN 2038 auction
    "EG": (620, 45, 24.0, 14.5, 4, {"inflation"}),
    "KE": (600, 25, 13.5, 4.5, 1, set()),
    "GH": (520, -10, None, None, -1, set()),
    "ZM": (550, -5, None, None, -1, set()),
    "LK": (500, -5, 11.5, 2.0, -1, set()),
    "PK": (650, 20, 12.0, 6.0, 1, set()),
    "AR": (750, 30, None, None, 2, set()),
    "SN": (1400, 60, None, None, 2, set()),
}

# 1-month FX return vs USD, % (negative = local currency weaker). Brief values; others 0 until loaded.
FX_1M_RETURN_BRIEF = {"EG": -3.1, "NG": 1.4, "ZA": -1.0, "TR": -1.7}

DRIVERS = {
    "HU": "EU funds proposal and a new 2.5% inflation target support the euro-convergence story.",
    "ZA": "Rate hike into a contracting economy; the rand weakened anyway.",
    "TR": "Lira at a record low despite a 37% policy rate.",
    "NG": "350bp rate cut, stronger naira and oil near $105.",
    "EG": "Pound down ~3% in a month; hot money can leave fast.",
    "SN": "Restructuring ahead; Eurobonds trade below 50 cents.",
}

# Illustrative flags — the team must verify before the demo.
FLAGS = {
    "SN": {"hidden_debt": True},
    "AR": {"imf_program": True, "post_restructuring": True},
    "PK": {"imf_program": True},
    "EG": {"imf_program": True},
    "LK": {"imf_program": True, "post_restructuring": True},
    "GH": {"imf_program": True, "post_restructuring": True},
    "ZM": {"imf_program": True, "post_restructuring": True},
}

MACRO = [
    dict(key="UST10", label="US 10Y", value=5.17, display="5.17%", change_text="−1 bp Fri",
         explanation="Higher US yields raise the bar EM bonds must clear and pull money back to the US."),
    dict(key="DXY", label="DXY (dollar index)", value=101.0, display="101.0", change_text="−0.3% Fri",
         explanation="A softer dollar eases pressure on EM currencies and on dollar debt service."),
    dict(key="BRENT", label="Brent", value=104.3, display="$104.3", change_text="+1% on the week",
         explanation="Oil above $100 helps exporters like Nigeria and squeezes importers like Egypt, Pakistan and Kenya."),
    dict(key="FED", label="Fed hike odds (28 Oct)", value=None, display="~66–70%", change_text="Meeting 28 Oct",
         explanation="A hike would tighten financing for the weakest issuers first."),
]

HARD_FUND = {"MX": 10, "ID": 10, "BR": 9, "TR": 9, "EG": 8, "ZA": 8, "NG": 7, "HU": 7, "AR": 7,
             "PK": 5, "KE": 5, "GH": 4, "SN": 4, "LK": 4, "ZM": 3}
LOCAL_FUND = {"BR": 14, "MX": 14, "ID": 14, "ZA": 13, "HU": 12, "TR": 9, "EG": 8, "NG": 8, "GH": 4, "KE": 4}
HARD_FUND_NAME = "Sample hard-currency fund"
LOCAL_FUND_NAME = "Sample local-currency fund"

# (type, name, fund or iso2, ccy, yield %, maturity, amount $k)
BOOK = [
    ("ETF", "EM hard-currency bond ETF (sample)", HARD_FUND_NAME, "USD", 7.40, None, 250),
    ("ETF", "EM local-currency bond ETF (sample)", LOCAL_FUND_NAME, "Local", 8.90, None, 150),
    ("Bond", "EG 364-day T-bill", "EG", "Local", 24.0, "2027", 15),
    ("Bond", "NG FGN 2038", "NG", "Local", 16.85, "2038", 20),
    ("Bond", "ZA R2035", "ZA", "Local", 8.9, "2035", 20),
    ("Bond", "TR 10-year TURKGB", "TR", "Local", 32.6, "2036", 10),
    ("Bond", "HU 10-year HGB", "HU", "Local", 5.64, "2036", 15),
    ("Bond", "SN 2033 Eurobond", "SN", "USD", 18.0, "2033", 10),
    ("Bond", "MX 2034 bond", "MX", "USD", 6.4, "2034", 10),
]


def seed_countries() -> None:
    with session_scope() as s:
        for c in COUNTRIES:
            s.merge(Country(**c))


def seed_all(reset: bool = False) -> None:
    with session_scope() as s:
        if reset:
            for model in (Holding, FundWeight, Fund, TextSignal, CountryNote, CountryScore,
                          ManualInput, IndicatorValue, Macro, Country):
                s.execute(delete(model))
        for c in COUNTRIES:
            s.merge(Country(**c))

        for iso2, (L, S, F, M, T) in LAYER_SCORES.items():
            risk_1m = MARKET[iso2][4]
            derived = compute_derived({"L": L, "S": S, "F": F, "M": M})
            s.merge(CountryScore(
                iso2=iso2, L=L, S=S, F=F, M=M, T=T, risk_1m_change=risk_1m,
                layer_sources={k: ILL for k in "LSFMT"}, **derived,
            ))

        for iso2, (spread, spread_1m, local_10y, inflation, _risk_1m, brief) in MARKET.items():
            # Unknown FX stays NULL ("n/a"), never a placeholder 0 (spec §15: never invent market data).
            fx_ret = FX_1M_RETURN_BRIEF.get(iso2)
            s.merge(ManualInput(
                iso2=iso2, spread_bp=spread, spread_1m_bp=spread_1m, local_10y=local_10y,
                fx_1m_pct=None if fx_ret is None else -fx_ret,  # CSV convention: + = local currency weakened
                sources={
                    "spread_bp": ILL, "spread_1m_bp": ILL,
                    "local_10y": BRIEF if "local_10y" in brief else ILL,
                    **({"fx_1m_pct": BRIEF} if iso2 in FX_1M_RETURN_BRIEF else {}),
                },
            ))
            if inflation is not None:
                s.add(IndicatorValue(iso2=iso2, key="inflation_imf", value=inflation, year=2026,
                                     source=BRIEF if "inflation" in brief else ILL))

        for c in COUNTRIES:
            s.merge(CountryNote(iso2=c["iso2"], driver=DRIVERS.get(c["iso2"]), flags=FLAGS.get(c["iso2"], {})))

        for m in MACRO:
            s.merge(Macro(**m, source=BRIEF, as_of="Fri 25 Sep 2026 close"))

        funds = {}
        for name, weights in ((HARD_FUND_NAME, HARD_FUND), (LOCAL_FUND_NAME, LOCAL_FUND)):
            f = Fund(name=name, is_sample=True)
            s.add(f)
            s.flush()
            funds[name] = f.id
            for iso2, w in weights.items():
                s.add(FundWeight(fund_id=f.id, iso2=iso2, weight=w))

        for typ, name, ref, ccy, yld, maturity, amount in BOOK:
            s.add(Holding(
                type=typ, name=name, ccy=ccy, yield_pct=yld, maturity=maturity, amount_k=amount,
                fund_id=funds[ref] if typ == "ETF" else None, iso2=ref if typ == "Bond" else None,
            ))
