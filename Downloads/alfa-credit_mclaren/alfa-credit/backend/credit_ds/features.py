"""Описание признаков и расчёт производных. Один источник правды для обучения и онлайна."""
import numpy as np
import pandas as pd

from .config import NOMINAL_RATE_FEAT

CAT = ["legal_form", "sector", "purpose", "region_tier"]
NUM = [
    "age_months", "employees", "revenue_growth", "profit_margin", "turnover_cv",
    "neg_days_90", "debt_to_revenue", "active_loans", "overdue_cnt_12m",
    "max_overdue_days_24m", "bki_score", "inquiries_6m", "tax_arrears",
    "court_cases", "collateral_ratio", "term_months",
    # производные
    "log_revenue", "log_amount", "loan_to_revenue", "dscr", "payment_to_turnover",
]
FEATURES = CAT + NUM

# +1: больше значение -> выше риск, -1: наоборот. Передаётся в CatBoost как monotone_constraints.
MONOTONE = {
    "debt_to_revenue": 1, "overdue_cnt_12m": 1, "max_overdue_days_24m": 1, "tax_arrears": 1,
    "court_cases": 1, "turnover_cv": 1, "neg_days_90": 1, "loan_to_revenue": 1, "inquiries_6m": 1,
    "profit_margin": -1, "revenue_growth": -1, "bki_score": -1, "age_months": -1,
    "dscr": -1, "collateral_ratio": -1,
}

REQUIRED_FOR_DECISION = ["revenue", "profit_margin", "requested_amount", "term_months", "purpose", "legal_form"]


def annuity(amount, annual_rate, term_months):
    r = np.asarray(annual_rate, dtype=float) / 12.0
    n = np.asarray(term_months, dtype=float)
    amount = np.asarray(amount, dtype=float)
    return np.where(r > 0, amount * r / (1 - (1 + r) ** (-n)), amount / n)


def add_derived(df: pd.DataFrame, rate: float = NOMINAL_RATE_FEAT) -> pd.DataFrame:
    d = df.copy()
    d["log_revenue"] = np.log(d["revenue"].clip(lower=1))
    d["log_amount"] = np.log(d["requested_amount"].clip(lower=1))
    d["loan_to_revenue"] = d["requested_amount"] / d["revenue"].clip(lower=1)
    new_pay = annuity(d["requested_amount"], rate, d["term_months"])
    debt = d["debt_to_revenue"] * d["revenue"]
    existing_service = debt / 18.0 + debt * rate / 12.0       # грубая оценка обслуживания текущего долга
    cash_flow = d["revenue"] / 12.0 * d["profit_margin"]
    d["new_payment"] = new_pay
    d["dscr"] = cash_flow / (existing_service + new_pay + 1.0)
    d["payment_to_turnover"] = (new_pay + existing_service) / d["avg_monthly_turnover"].clip(lower=1)
    return d
