import numpy as np
import pytest
from fastapi.testclient import TestClient

from credit_ds.api import app
from credit_ds.llm import extract_intent, template_explanation
from credit_ds.monitoring import psi
from credit_ds.pipeline import assess, get_scorer
from credit_ds.news import personalize

BASE = dict(legal_form="OOO", sector="retail", region_tier="million", employees=10, revenue=24e6, revenue_growth=0.08,
            profit_margin=0.16, avg_monthly_turnover=2e6, turnover_cv=0.25, neg_days_90=1, debt_to_revenue=0.1,
            active_loans=1, collateral_ratio=0.0, product="working_capital", purpose="working_capital",
            requested_amount=2e6, term_months=36)


def app_(inn, **kw):
    return {**BASE, "inn": inn, **kw}


def test_model_quality():
    card = get_scorer().card["metrics_oot"]["catboost_calibrated"]
    assert card["gini"] > 0.5 and card["brier"] < 0.06


def test_monotonicity():
    from credit_ds.decision import _frame
    s = get_scorer()
    a = {**BASE, "inn": "x", "age_months": 48, "bki_score": 650, "overdue_cnt_12m": 0, "max_overdue_days_24m": 0,
         "inquiries_6m": 1, "tax_arrears": 0, "court_cases": 0}
    for feat, vals in [("debt_to_revenue", [0, .3, .6, 1.0]), ("overdue_cnt_12m", [0, 1, 2, 4]), ("bki_score", [800, 700, 600, 500])]:
        import pandas as pd
        df = pd.concat([_frame({**a, feat: v}, [("working_capital", 3e6, 24)]) for v in vals])
        p = s.predict_pd(df)
        assert np.all(np.diff(p) >= -1e-9), (feat, p)


def test_good_client_approved():
    r = assess(app_("7700000001"))
    assert r["outcome"] in ("APPROVED", "COUNTER_OFFER") and r["offer"]


def test_liquidating_declined():
    r = assess(app_("7700000004"))
    assert r["outcome"] == "DECLINED" and r["pd"] is None


def test_overleveraged_not_approved_as_requested():
    r = assess(app_("7700000003", debt_to_revenue=0.7, requested_amount=8e6))
    assert r["outcome"] in ("DECLINED", "MANUAL_REVIEW", "COUNTER_OFFER")
    if r["offer"]:
        assert r["offer"]["amount"] < 8e6


def test_oversized_request_gets_counter_offer():
    r = assess(app_("7700000001", requested_amount=15e6, term_months=12))
    assert r["outcome"] in ("COUNTER_OFFER", "DECLINED", "MANUAL_REVIEW")
    if r["outcome"] == "COUNTER_OFFER":
        assert r["offer"]["amount"] <= 15e6


def test_missing_data_need_info():
    r = assess(app_("7700000001", profit_margin=None))
    assert r["outcome"] == "NEED_INFO" and "profit_margin" in r["missing_info"]


def test_price_increases_with_risk():
    a = assess(app_("7700000001", requested_amount=1e6))
    b = assess(app_("7700000005", requested_amount=1e6))
    if a["offer"] and b["offer"]:
        assert b["offer"]["rate"] >= a["offer"]["rate"]


def test_intent_rules():
    i = extract_intent("Хочу купить печь для пекарни за 2,5 млн на 3 года")["intent"]
    assert i["purpose"] == "equipment" and i["amount"] == 2_500_000 and i["term_months"] == 36


def test_explanation_fallback_has_amount():
    r = assess(app_("7700000001", requested_amount=1e6))
    assert "₽" in template_explanation(r) or r["outcome"] != "APPROVED"


def test_news_changes_with_context():
    p = {"sector": "agro", "collateral_ratio": 0}
    a = personalize(p, {"purpose": "working_capital"}, "DRAFT")[0]["id"]
    b = personalize(p, {"outcome": "DECLINED", "grade": "E"}, "DECLINED")[0]["id"]
    assert a != b


def test_psi_zero_on_same():
    x = np.random.default_rng(0).normal(size=5000)
    assert psi(x, x) < 1e-6


def test_api_e2e():
    c = TestClient(app)
    assert c.get("/health").status_code == 200
    r = c.post("/ds/recommend", json={"company": {"inn": "7700000001"}, "text": "нужны деньги на закупку товара, 2 млн"})
    assert r.status_code == 200 and r.json()["products"]
    r = c.post("/ds/assess", json={"inn": "7700000001"})
    assert r.status_code == 200 and r.json()["outcome"]


def test_calibrator_is_version_independent():
    import numpy as np
    from credit_ds.scoring import PlattCalibrator
    rng = np.random.default_rng(0); raw = rng.uniform(0.01, 0.6, 3000); y = (rng.uniform(size=3000) < raw).astype(int)
    c = PlattCalibrator().fit(raw, y); p = c.predict(np.array([0.05, 0.2, 0.5]))
    assert c.lr is None and np.all(np.diff(p) > 0) and p.min() > 0 and p.max() < 1


def test_news_tags_are_human_readable():
    from credit_ds.news import personalize
    for n in personalize({"sector": "retail"}, {"purpose": "working_capital", "outcome": "DECLINED", "grade": "E"}, "DECLINED"):
        assert "credit_history" not in n["why"] and "declined" not in n["why"] and n["summary"]
