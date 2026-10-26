"""Кредитное решение. Скоринг (PD) — вход, решение — отдельная политика банка:
knock-out правила -> полнота данных -> PD -> лимиты продукта -> контр-оферта -> ценообразование.
Исход не бинарный: APPROVED / COUNTER_OFFER / NEED_INFO / MANUAL_REVIEW / DECLINED."""
import numpy as np
import pandas as pd

from . import config
from .catalog import PRODUCTS
from .features import REQUIRED_FOR_DECISION, add_derived, annuity
from .scoring import Scorer, grade_of

APPROVED, COUNTER, NEED_INFO, MANUAL, DECLINED = "APPROVED", "COUNTER_OFFER", "NEED_INFO", "MANUAL_REVIEW", "DECLINED"
NEXT_STATUS = {APPROVED: "OFFER_READY", COUNTER: "OFFER_READY", NEED_INFO: "NEED_INFO",
               MANUAL: "UNDERWRITER_REVIEW", DECLINED: "DECLINED"}
TERM_GRID = [3, 6, 9, 12, 18, 24, 36, 48, 60, 84, 120]
AMOUNT_FACTORS = [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2]


def lgd(collateral_ratio: float) -> float:
    return float(np.clip(0.65 - 0.45 * min(collateral_ratio, 1.0), 0.2, 0.65))


def price(pd_value: float, collateral_ratio: float, product: dict) -> float:
    rate = (config.BASE_RATE + config.FUNDING_SPREAD + config.OPEX_SPREAD + config.TARGET_MARGIN
            + product["spread"] + pd_value * lgd(collateral_ratio))
    return round(float(rate), 3)


def _frame(app: dict, cands) -> pd.DataFrame:
    base = pd.DataFrame([app] * len(cands)).reset_index(drop=True)
    base["requested_amount"] = [c[1] for c in cands]
    base["term_months"] = [c[2] for c in cands]
    return add_derived(base)


def _feasible(row, product: dict, pd_value: float):
    """Возвращает список нарушенных условий продукта (пусто = можно одобрять)."""
    v = []
    if not product["min_amount"] <= row["requested_amount"] <= product["max_amount"]:
        v.append("сумма вне диапазона продукта")
    if not product["min_term"] <= row["term_months"] <= product["max_term"]:
        v.append("срок вне диапазона продукта")
    if row["age_months"] < product["min_age"]:
        v.append(f"бизнес работает меньше {product['min_age']} мес.")
    if row["collateral_ratio"] < product["min_collateral"]:
        v.append("требуется залог")
    if row["dscr"] < product["min_dscr"]:
        v.append(f"DSCR {row['dscr']:.2f} ниже порога {product['min_dscr']}")
    if row["loan_to_revenue"] > product["max_ltr"]:
        v.append(f"сумма {row['loan_to_revenue']:.0%} выручки выше лимита {product['max_ltr']:.0%}")
    if pd_value > product["pd_approve"]:
        v.append(f"PD {pd_value:.1%} выше порога автоодобрения {product['pd_approve']:.1%}")
    return v


def _offer(row, pd_value: float, product_id: str) -> dict:
    p = PRODUCTS[product_id]
    rate = price(pd_value, row["collateral_ratio"], p)
    pay = float(annuity(row["requested_amount"], rate, row["term_months"]))
    return {"product_id": product_id, "product": p["name"], "amount": int(row["requested_amount"]),
            "term_months": int(row["term_months"]), "rate": rate, "monthly_payment": round(pay),
            "total_overpayment": round(pay * row["term_months"] - row["requested_amount"]), "valid_days": 14}


def search_offers(app: dict, scorer: Scorer, product_ids, requested_amount=None, requested_term=None):
    """Перебор (продукт, сумма, срок) -> допустимые варианты, отсортированные по близости к запросу."""
    req_a = requested_amount or app["requested_amount"]
    req_t = requested_term or app["term_months"]
    cands = []
    for pid in product_ids:
        p = PRODUCTS[pid]
        terms = sorted({t for t in TERM_GRID + [req_t] if p["min_term"] <= t <= p["max_term"]})
        amounts = {int(round(req_a * f / 10_000) * 10_000) for f in AMOUNT_FACTORS}
        for a in amounts:
            if p["min_amount"] <= a <= p["max_amount"]:
                cands += [(pid, a, t) for t in terms]
    if not cands:
        return []
    df = _frame(app, cands)
    pdv = scorer.predict_pd(df)
    res = []
    for (pid, a, t), (_, row), pr in zip(cands, df.iterrows(), pdv):
        if not _feasible(row, PRODUCTS[pid], pr):
            closeness = min(a / req_a, 1.0) - 0.15 * abs(t - req_t) / req_t - (0.25 if pid != app.get("product") else 0)
            res.append((closeness, pid, row, float(pr)))
    res.sort(key=lambda x: -x[0])
    return [(_offer(r, pr, pid), pr) for _, pid, r, pr in res]


def max_affordable(app: dict, scorer: Scorer, product_id: str):
    """«Вам предодобрено до X»: максимальная сумма, проходящая автоодобрение по продукту."""
    p = PRODUCTS[product_id]
    a = dict(app)
    a["requested_amount"] = float(p["min_amount"])
    best = None
    grid = np.unique(np.round(np.geomspace(p["min_amount"], min(p["max_amount"], app["revenue"] * p["max_ltr"]), 25) / 10_000) * 10_000)
    for amount in grid:
        a["requested_amount"] = float(amount)
        offers = search_offers(a, scorer, [product_id], requested_amount=float(amount), requested_term=app.get("term_months", 24))
        offers = [o for o in offers if o[0]["amount"] == int(amount)]
        if offers and (best is None or amount > best[0]["amount"]):
            best = offers[0]
    return best[0] if best else None


def decide(app: dict, scorer: Scorer, enrich: dict | None = None) -> dict:
    trace = []
    app = dict(app)
    enrich = enrich or {}
    prod_id = app["product"]
    product = PRODUCTS[prod_id]

    # 1. Knock-out политика (до скоринга)
    ko = []
    if enrich.get("egrul", {}).get("status", "active") != "active":
        ko.append("Компания не действует (ЕГРЮЛ/ЕГРИП): ликвидация или прекращение деятельности")
    if enrich.get("fns", {}).get("mass_address"):
        ko.append("Адрес массовой регистрации")
    if app.get("max_overdue_days_24m", 0) and app["max_overdue_days_24m"] > 90:
        ko.append("Просрочка более 90 дней за последние 24 месяца")
    if app.get("tax_arrears_amount", 0) > 0.05 * app.get("revenue", 1):
        ko.append("Существенная задолженность по налогам")
    trace.append({"step": "knockout", "triggered": ko})
    if ko:
        return _result(DECLINED, app, None, None, None, reasons=ko, trace=trace)

    # 2. Полнота данных
    missing = [f for f in REQUIRED_FOR_DECISION if app.get(f) is None or (isinstance(app.get(f), float) and np.isnan(app[f]))]
    trace.append({"step": "completeness", "missing": missing})
    if missing:
        return _result(NEED_INFO, app, None, None, None, missing=missing,
                       reasons=["Для оценки не хватает данных: " + ", ".join(missing)], trace=trace)

    # 3. Скоринг запрошенных условий
    df = _frame(app, [(prod_id, app["requested_amount"], app["term_months"])])
    row = df.iloc[0]
    pd0 = float(scorer.predict_pd(df)[0])
    grade = grade_of(pd0)
    expl = scorer.explain(df)
    trace.append({"step": "scoring", "pd": round(pd0, 4), "grade": grade, "dscr": round(float(row["dscr"]), 2)})

    # 4. Решение по запрошенным условиям
    violations = _feasible(row, product, pd0)
    trace.append({"step": "policy_check", "violations": violations})
    if not violations:
        offer = _offer(row, pd0, prod_id)
        if app["requested_amount"] > product["manual_above"]:
            return _result(MANUAL, app, pd0, grade, offer, expl=expl, trace=trace,
                           reasons=["Сумма выше лимита автоматического решения: нужен андеррайтер (принцип четырёх глаз)"])
        return _result(APPROVED, app, pd0, grade, offer, expl=expl, trace=trace)

    # 5. Контр-оферта: другие сумма/срок/продукт
    compatible = [pid for pid, p in PRODUCTS.items() if app["purpose"] in p["purposes"]]
    alts = search_offers(app, scorer, [prod_id] + [p for p in compatible if p != prod_id])
    trace.append({"step": "counter_offer_search", "found": len(alts)})
    if alts:
        offer, pd1 = alts[0]
        return _result(COUNTER, app, pd0, grade, offer, expl=expl, trace=trace,
                       reasons=["Запрошенные условия не проходят: " + "; ".join(violations)])
    # 6. Серая зона -> человек, иначе отказ
    if pd0 <= product["pd_decline"] and app["requested_amount"] >= 1_000_000:
        return _result(MANUAL, app, pd0, grade, None, expl=expl, trace=trace,
                       reasons=["Пограничный риск, автоматического варианта нет: решение за андеррайтером"] + violations)
    return _result(DECLINED, app, pd0, grade, None, expl=expl, trace=trace,
                   reasons=["Риск выше допустимого для продукта"] + violations)


def _result(outcome, app, pd_value, grade, offer, expl=None, reasons=None, missing=None, trace=None):
    return {"outcome": outcome, "next_status": NEXT_STATUS[outcome],
            "pd": None if pd_value is None else round(pd_value, 4), "grade": grade, "offer": offer,
            "reasons": reasons or [], "missing_info": missing or [],
            "drivers": expl or {"risk": [], "strengths": []}, "trace": trace or [],
            "requested": {"product": app.get("product"), "amount": app.get("requested_amount"), "term_months": app.get("term_months")}}
