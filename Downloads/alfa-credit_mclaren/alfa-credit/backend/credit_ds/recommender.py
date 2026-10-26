"""Подбор продукта под потребность: «хочу кредит на X» -> ранжированные предложения с предодобренным лимитом."""
import numpy as np

from .catalog import PRODUCTS, PURPOSE_LABELS
from .decision import max_affordable
from .scoring import Scorer

W = dict(purpose=0.45, amount=0.20, term=0.10, afford=0.25)


def _range_fit(x, lo, hi):
    if x is None:
        return 0.5
    if lo <= x <= hi:
        return 1.0
    d = (lo - x) / lo if x < lo else (x - hi) / hi
    return float(max(0.0, 1 - d))


def recommend(profile: dict, intent: dict, scorer: Scorer, k: int = 3) -> list:
    """profile — данные о компании (после обогащения), intent — результат extract_intent()['intent']."""
    purpose = intent.get("purpose") or "working_capital"
    amount = intent.get("amount") or float(np.clip(profile["revenue"] * 0.15, 300_000, 20_000_000))
    term = intent.get("term_months") or 24
    out = []
    for pid, p in PRODUCTS.items():
        if profile.get("age_months", 12) < p["min_age"]:
            continue
        purpose_match = 1.0 if purpose in p["purposes"] else 0.0
        if purpose_match == 0.0:
            continue
        if p["min_collateral"] > 0 and not intent.get("has_collateral") and profile.get("collateral_ratio", 0) < p["min_collateral"]:
            continue
        app = {**profile, "purpose": purpose, "product": pid, "requested_amount": float(amount), "term_months": int(np.clip(term, p["min_term"], p["max_term"]))}
        cap = max_affordable(app, scorer, pid)
        afford = 0.0 if cap is None else min(cap["amount"] / amount, 1.0)
        score = (W["purpose"] * purpose_match + W["amount"] * _range_fit(amount, p["min_amount"], p["max_amount"])
                 + W["term"] * _range_fit(term, p["min_term"], p["max_term"]) + W["afford"] * afford)
        if intent.get("urgent") and pid == "express":
            score += 0.08
        out.append({"product_id": pid, "name": p["name"], "pitch": p["pitch"], "score": round(score, 3),
                    "preapproved_up_to": cap["amount"] if cap else None,
                    "indicative_rate": cap["rate"] if cap else None,
                    "indicative_term_months": cap["term_months"] if cap else None,
                    "why": f"Подходит для цели «{PURPOSE_LABELS.get(purpose, purpose)}»"
                           + (f"; предодобрено до {cap['amount']:,.0f} ₽".replace(",", " ") if cap else "; по сумме потребуется индивидуальное рассмотрение")})
    return sorted(out, key=lambda x: -x["score"])[:k]
