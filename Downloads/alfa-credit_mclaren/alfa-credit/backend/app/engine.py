"""Адаптер к DS-модулю credit_ds: форма клиента -> заявка модели -> решение. Вся «интеллектуальная» логика — в credit_ds."""
import json
from credit_ds.catalog import PRODUCTS, PURPOSE_LABELS
from credit_ds.decision import price
from credit_ds.features import annuity
from credit_ds.llm import extract_intent
from credit_ds.mock_integrations import enrich
from credit_ds.news import personalize
from credit_ds.pipeline import assess, apply_enrichment, get_scorer
from credit_ds.recommender import recommend

def jd(o):  # numpy -> json
    return o.item() if hasattr(o, "item") else str(o)
def dumps(o): return json.dumps(o, ensure_ascii=False, default=jd)

def build(f: dict, amount=None, term=None) -> dict:
    amt = float(amount or f["amount"]); rev = f.get("revenue")
    a = {"inn": f["inn"], "product": f["product"], "purpose": f["purpose"], "requested_amount": amt, "term_months": int(term or f["term"]),
         "sector": f.get("sector") or "services", "region_tier": f.get("region_tier") or "million", "employees": int(f.get("employees") or 1),
         "revenue_growth": float(f.get("revenue_growth") or 0), "turnover_cv": 0.25, "neg_days_90": 0,
         "active_loans": int(f.get("active_loans") or 0), "collateral_ratio": min(float(f.get("collateral_value") or 0) / amt, 1.5)}
    if f.get("legal_form"): a["legal_form"] = f["legal_form"]
    if rev:   # без выручки ключ не передаём: модель вернёт NEED_INFO
        a["revenue"] = float(rev); a["debt_to_revenue"] = float(f.get("existing_debt") or 0) / float(rev); a["avg_monthly_turnover"] = float(rev) / 12
    else: a["debt_to_revenue"] = 0.0; a["avg_monthly_turnover"] = 1e6
    if f.get("profit_margin") is not None: a["profit_margin"] = float(f["profit_margin"])
    return a

def run(f: dict) -> dict:
    res = assess(build(f))
    if res["outcome"] == "MANUAL_REVIEW":   # подсказка андеррайтеру: условия по умолчанию
        if res["offer"]: res["suggested_offer"] = res["offer"]
        elif res["pd"] is not None:
            a = build(f); p = PRODUCTS[f["product"]]; amt = int(min(f["amount"], p["max_amount"])); rate = price(res["pd"], a["collateral_ratio"], p)
            res["suggested_offer"] = make_offer(f["product"], amt, int(f["term"]), rate)
    return res

def make_offer(pid, amount, term, rate):
    pay = float(annuity(amount, rate, term))
    return {"product_id": pid, "product": PRODUCTS[pid]["name"], "amount": int(amount), "term_months": int(term), "rate": round(float(rate), 3),
            "monthly_payment": round(pay), "total_overpayment": round(pay * term - amount), "valid_days": 14}

def profile_of(company: dict) -> dict:
    f = {**company, "product": "working_capital", "purpose": "working_capital", "amount": 1_000_000, "term": 12}
    return apply_enrichment(build(f), enrich(company["inn"]))

def assistant(company: dict, text: str) -> dict:
    it = extract_intent(text); prof = profile_of(company)
    return {"intent": it, "products": recommend(prof, it["intent"], get_scorer())}

def news(company: dict, last: dict | None, stage: str) -> list:
    app = None
    if last:
        d = last.get("decision") or {}
        app = {"purpose": last["form"]["purpose"], "outcome": d.get("outcome"), "grade": d.get("grade")}
    return personalize({"sector": company.get("sector"), "collateral_ratio": 1 if company.get("collateral_value") else 0}, app, stage)
